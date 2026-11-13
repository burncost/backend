"""Geo provider layer for the delivery map pickers.

Covers the pure mapping/ordering helpers (no network) plus the provider chain's
fallback behaviour, which is what makes landmark queries such as
"ecwa seminary church jos" resolve. Upstream HTTP calls are stubbed out.
"""
import asyncio

import pytest

from app.services import geo_providers as geo


def _run(coro):
    return asyncio.run(coro)


# ── Nominatim mapping ──────────────────────────────────────────────────

def test_nominatim_maps_short_label_and_hierarchy():
    raw = [{
        "lat": "9.8965", "lon": "8.8587",
        "display_name": "ECWA Seminary Church, Jos, Plateau, Nigeria",
        "address": {"road": "Bukuru Road", "city": "Jos", "state": "Plateau", "country": "Nigeria"},
    }]
    out = geo.nominatim_to_results(raw)
    assert len(out) == 1
    assert out[0]["lat"] == pytest.approx(9.8965)
    assert out[0]["lng"] == pytest.approx(8.8587)
    assert out[0]["label"] == "Bukuru Road, Jos, Plateau"
    assert out[0]["city"] == "Jos"
    assert out[0]["state"] == "Plateau"


def test_nominatim_falls_back_to_display_name_and_town():
    raw = [{
        "lat": "9.1", "lon": "8.1",
        "display_name": "Somewhere, Nigeria",
        "address": {"town": "Pankshin", "state": "Plateau"},
    }]
    out = geo.nominatim_to_results(raw)
    assert out[0]["label"] == "Pankshin, Plateau"
    assert out[0]["city"] == "Pankshin"


def test_nominatim_skips_unusable_entries():
    raw = [{"lat": "not-a-number", "lon": "8.1"}, {"lon": "8.1"}, "junk", None]
    assert geo.nominatim_to_results(raw) == []
    assert geo.nominatim_to_results({"not": "a list"}) == []


# ── Photon mapping ─────────────────────────────────────────────────────

def test_photon_maps_geojson_feature():
    raw = {"features": [{
        "geometry": {"coordinates": [8.8587, 9.8965]},  # GeoJSON is lon,lat
        "properties": {
            "name": "ECWA Seminary", "city": "Jos", "state": "Plateau",
            "country": "Nigeria", "countrycode": "ng",
        },
    }]}
    out = geo.photon_to_results(raw)
    assert len(out) == 1
    assert out[0]["lat"] == pytest.approx(9.8965)
    assert out[0]["lng"] == pytest.approx(8.8587)
    assert out[0]["label"] == "ECWA Seminary, Jos, Plateau"
    assert out[0]["countrycode"] == "NG"


def test_photon_uses_street_when_unnamed_and_skips_bad_geometry():
    raw = {"features": [
        {"geometry": {"coordinates": [8.5, 9.0]},
         "properties": {"housenumber": "12", "street": "Rukuba Road", "city": "Jos",
                        "state": "Plateau", "countrycode": "ng"}},
        {"geometry": {"coordinates": []}, "properties": {"name": "Nowhere"}},
    ]}
    out = geo.photon_to_results(raw)
    assert [r["label"] for r in out] == ["12 Rukuba Road, Jos, Plateau"]


def test_photon_handles_empty_or_broken_payloads():
    assert geo.photon_to_results(None) == []
    assert geo.photon_to_results({}) == []
    assert geo.photon_to_results({"features": [{"properties": {}}]}) == []


# ── Ordering / de-duplication ──────────────────────────────────────────

def test_dedupe_keeps_first_of_each_pin_in_order():
    results = [
        {"lat": 9.89651, "lng": 8.85871, "label": "first"},
        {"lat": 9.89652, "lng": 8.85872, "label": "same pin"},
        {"lat": 9.90000, "lng": 8.86000, "label": "other pin"},
    ]
    out = geo.dedupe_results(results)
    assert [r["label"] for r in out] == ["first", "other pin"]


def test_dedupe_drops_entries_without_coordinates():
    assert geo.dedupe_results([{"label": "no coords"}, {"lat": None, "lng": None}]) == []


def test_prefer_country_puts_local_hits_first_and_keeps_inner_order():
    results = [
        {"lat": 1, "lng": 1, "country": "Ghana"},
        {"lat": 2, "lng": 2, "country": "Nigeria"},
        {"lat": 3, "lng": 3, "country": "Nigeria"},
        {"lat": 4, "lng": 4, "countrycode": "NG"},
    ]
    out = geo.prefer_country(results)
    assert [r["lat"] for r in out] == [2, 3, 4, 1]


def test_prefer_country_keeps_provider_order_when_nothing_matches():
    results = [{"lat": 1, "lng": 1, "country": "Ghana"}, {"lat": 2, "lng": 2, "country": "Togo"}]
    assert geo.prefer_country(results) == results


def test_label_and_city_helpers_handle_empty_address():
    assert geo.label_from_address({}, "Full, Display, Name") == "Full, Display, Name"
    assert geo.label_from_address({}) == ""
    assert geo.city_state_from_address({"county": "Plateau"}) == ("Plateau", None)


# ── Provider chain behaviour ───────────────────────────────────────────

def _stub(monkeypatch, *, nominatim=None, photon=None, google=None, google_on=False):
    """Replace every provider with a scripted fake (no HTTP leaves the process)."""
    calls = {"nominatim": [], "photon": [], "google": []}

    async def fake_nominatim(query, limit, countrycodes=None):
        calls["nominatim"].append(countrycodes)
        return list(nominatim or [])

    async def fake_photon(query, limit):
        calls["photon"].append(query)
        return list(photon or [])

    async def fake_google(query, limit):
        calls["google"].append(query)
        return list(google or [])

    monkeypatch.setattr(geo, "nominatim_search", fake_nominatim)
    monkeypatch.setattr(geo, "photon_search", fake_photon)
    monkeypatch.setattr(geo, "google_search", fake_google)
    monkeypatch.setattr(geo, "google_enabled", lambda: google_on)
    return calls


def test_search_falls_through_to_photon_when_nominatim_draws_blank(monkeypatch):
    """The ECWA case: Nominatim finds nothing, Photon still resolves the landmark."""
    calls = _stub(monkeypatch, photon=[{"lat": 9.89, "lng": 8.85, "label": "ECWA Seminary"}])
    out = _run(geo.search_places("ecwa seminary church jos", 5))
    assert [r["label"] for r in out] == ["ECWA Seminary"]
    # Nigeria-biased attempt, then unfiltered, then the fallback.
    assert calls["nominatim"] == ["ng", None]
    assert calls["photon"] == ["ecwa seminary church jos"]


def test_search_stops_at_the_first_provider_with_hits(monkeypatch):
    calls = _stub(
        monkeypatch,
        nominatim=[{"lat": 9.1, "lng": 8.1, "label": "Jos", "country": "Nigeria"}],
    )
    out = _run(geo.search_places("jos", 5))
    assert len(out) == 1
    assert calls["nominatim"] == ["ng"]  # no second shot, no Photon call
    assert calls["photon"] == []


def test_search_respects_the_limit_after_dedupe(monkeypatch):
    hits = [{"lat": 9.0 + i * 0.01, "lng": 8.0 + i * 0.01, "label": f"hit {i}"} for i in range(8)]
    hits.insert(1, dict(hits[0]))  # a duplicate pin must not eat a slot
    _stub(monkeypatch, nominatim=hits)
    out = _run(geo.search_places("jos", 3))
    assert [r["label"] for r in out] == ["hit 0", "hit 1", "hit 2"]


def test_search_uses_google_first_only_when_a_key_is_configured(monkeypatch):
    calls = _stub(
        monkeypatch,
        google=[{"lat": 9.9, "lng": 8.8, "label": "ECWA Seminary, Jos"}],
        google_on=True,
    )
    out = _run(geo.search_places("ecwa seminary church jos", 5))
    assert calls["google"] == ["ecwa seminary church jos"]
    assert calls["nominatim"] == []  # Google answered, key-less providers stay idle
    assert out[0]["label"] == "ECWA Seminary, Jos"


def test_search_raises_when_every_provider_fails(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(geo, "nominatim_search", boom)
    monkeypatch.setattr(geo, "photon_search", boom)
    monkeypatch.setattr(geo, "google_enabled", lambda: False)
    with pytest.raises(geo.GeoUnavailable):
        _run(geo.search_places("jos", 5))


def test_search_returns_empty_for_an_honest_miss(monkeypatch):
    """A provider answering "nothing found" is a miss, not an outage."""
    _stub(monkeypatch)
    assert _run(geo.search_places("zzzz nonexistent", 5)) == []


def test_reverse_uses_google_only_when_a_key_is_configured(monkeypatch):
    async def fake_google_reverse(lat, lng):
        return {"label": "Google Road, Jos", "city": "Jos", "state": "Plateau", "country": "Nigeria"}

    async def fake_nominatim_reverse(lat, lng):
        return {"label": "OSM Road, Jos", "city": "Jos", "state": "Plateau", "country": "Nigeria"}

    monkeypatch.setattr(geo, "google_reverse", fake_google_reverse)
    monkeypatch.setattr(geo, "nominatim_reverse", fake_nominatim_reverse)

    monkeypatch.setattr(geo, "google_enabled", lambda: True)
    assert _run(geo.reverse_place(9.9, 8.8))["label"] == "Google Road, Jos"

    monkeypatch.setattr(geo, "google_enabled", lambda: False)
    assert _run(geo.reverse_place(9.9, 8.8))["label"] == "OSM Road, Jos"


def test_reverse_raises_geo_unavailable_so_the_endpoint_can_degrade(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(geo, "nominatim_reverse", boom)
    monkeypatch.setattr(geo, "google_enabled", lambda: False)
    with pytest.raises(geo.GeoUnavailable):
        _run(geo.reverse_place(9.9, 8.8))


# ── Road snap (bad-fix detector) ───────────────────────────────────────

def test_snap_to_road_reads_osrm_lon_lat_order(monkeypatch):
    """OSRM answers in GeoJSON order (lon,lat) — the helper must swap back."""
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            captured["url"] = url

            class FakeResp:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"waypoints": [{"location": [8.8588686, 9.8964209], "distance": 8.25}]}

            return FakeResp()

    monkeypatch.setattr(geo.httpx, "AsyncClient", FakeClient)
    snap = _run(geo.snap_to_road(9.8964195, 8.8588705))
    assert captured["url"].endswith("/8.8588705,9.8964195")
    assert snap["lat"] == pytest.approx(9.8964209)
    assert snap["lng"] == pytest.approx(8.8588686)
    assert snap["distance_m"] == pytest.approx(8.25)


def test_snap_to_road_returns_none_when_osrm_is_unreachable(monkeypatch):
    class BoomClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("dns failure")

    monkeypatch.setattr(geo.httpx, "AsyncClient", BoomClient)
    assert _run(geo.snap_to_road(9.9, 8.8)) is None


def test_snap_to_road_returns_none_for_an_empty_waypoint_list(monkeypatch):
    class EmptyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            class FakeResp:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"waypoints": []}

            return FakeResp()

    monkeypatch.setattr(geo.httpx, "AsyncClient", EmptyClient)
    assert _run(geo.snap_to_road(9.9, 8.8)) is None


def test_google_is_off_without_a_key(monkeypatch):
    # Pin the config instead of trusting the machine: a developer .env that sets
    # GOOGLE_MAPS_API_KEY must not turn this "off without a key" check into a
    # false failure.
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", None)
    assert geo.google_enabled() is False


# ── Road route (Routes API) ─────────────────────────────────────────────

ROUTES_RAW = {
    "routes": [{
        "distanceMeters": 12400,
        "duration": "1500s",
        "staticDuration": "1080s",
        "polyline": {"encodedPolyline": "abc_xyz"},
    }],
}


def test_google_route_maps_polyline_distance_and_traffic_eta():
    out = geo.google_route_to_dict(ROUTES_RAW)
    assert out["polyline"] == "abc_xyz"
    assert out["distance_km"] == pytest.approx(12.4)
    # `duration` is the traffic-aware ETA (25 mins, not the 18 min static one).
    assert out["duration_min"] == 25
    assert out["traffic_aware"] is True


def test_google_route_falls_back_to_static_duration_without_traffic():
    raw = {"routes": [{
        "distanceMeters": 300,
        "duration": "20s",
        "staticDuration": "20s",
        "polyline": {"encodedPolyline": "p"},
    }]}
    out = geo.google_route_to_dict(raw)
    assert out["traffic_aware"] is False
    # Sub-minute routes still read as a minute rather than "0 min".
    assert out["duration_min"] == 1
    assert out["distance_km"] == pytest.approx(0.3)


def test_google_route_tolerates_missing_static_duration():
    raw = {"routes": [{
        "distanceMeters": 1000,
        "duration": "600s",
        "polyline": {"encodedPolyline": "p"},
    }]}
    out = geo.google_route_to_dict(raw)
    assert out["duration_min"] == 10
    assert out["traffic_aware"] is False


def test_google_route_rejects_misses_and_incomplete_payloads():
    """No drivable path is a normal miss (HTTP 200 + empty routes), not a crash."""
    assert geo.google_route_to_dict({"routes": []}) is None
    assert geo.google_route_to_dict({}) is None
    assert geo.google_route_to_dict({"routes": [{}]}) is None
    assert geo.google_route_to_dict({"routes": [{"polyline": {}}]}) is None
    assert geo.google_route_to_dict(None) is None


def _fake_routes_client(captured, payload):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["body"] = json or {}
            captured["headers"] = headers or {}

            class FakeResp:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return payload

            return FakeResp()

    return FakeClient


def test_driving_route_is_off_without_a_key(monkeypatch):
    # Same config pin as test_google_is_off_without_a_key: routing is billed, so
    # it must stay off when no key is configured, whatever the local .env holds.
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", None)
    assert _run(geo.driving_route((9.0, 8.0), (9.1, 8.1))) is None


def test_driving_route_posts_lat_lng_pairs_to_routes(monkeypatch):
    captured = {}
    monkeypatch.setattr(geo, "google_enabled", lambda: True)
    monkeypatch.setattr(geo.settings, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(geo.httpx, "AsyncClient", _fake_routes_client(captured, ROUTES_RAW))

    out = _run(geo.driving_route((9.8964, 8.8589), (9.92, 8.89)))
    assert captured["url"] == geo.GOOGLE_ROUTES_URL
    assert captured["headers"]["X-Goog-Api-Key"] == "test-key"
    # Routes rejects the request outright without a field mask.
    assert "routes.polyline.encodedPolyline" in captured["headers"]["X-Goog-FieldMask"]
    body = captured["body"]
    assert body["origin"]["location"]["latLng"] == {"latitude": 9.8964, "longitude": 8.8589}
    assert body["destination"]["location"]["latLng"] == {"latitude": 9.92, "longitude": 8.89}
    assert body["travelMode"] == "DRIVE"
    assert body["routingPreference"] == "TRAFFIC_AWARE"
    # Departure time is implied ("now") rather than sent.
    assert "departureTime" not in body
    assert out["distance_km"] == pytest.approx(12.4)
    assert out["duration_min"] == 25


def test_driving_route_swallows_upstream_errors(monkeypatch):
    monkeypatch.setattr(geo, "google_enabled", lambda: True)

    async def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(geo, "google_driving_route", boom)
    assert _run(geo.driving_route((9.0, 8.0), (9.1, 8.1))) is None
