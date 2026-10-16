"""Geo lookups for the delivery map pickers (search / reverse / road snap).

The pickers need three things from outside:

* ``search_places(query)``  - a typed street, area or landmark -> candidate pins
* ``reverse_place(lat,lng)`` - a pin -> a human-readable area ("Pankshin, Plateau State")
* ``snap_to_road(lat,lng)``  - a pin -> the nearest routable point + how far it moved

All three are key-less by default (Nominatim for search/reverse, Photon as a
search fallback, OSRM's demo server for the road snap), which keeps the map
working with no billing account.

``GoogleProvider`` is fully implemented but only selected once
``GOOGLE_MAPS_API_KEY`` is set, so the Places/Geocoding upgrade is a config
change rather than a rewrite. NOTE: Google's terms require data obtained from
Places/Geocoding to be displayed on a Google map, so switching the key on also
means swapping the Leaflet/OSM renderer in ``MapSearchPicker.tsx`` for the
Google Maps JS API.

Why the chain exists: Nominatim's token matching is strict, so one unknown word
("ecwa" in "ecwa seminary church jos") can zero out an otherwise findable
landmark. Retrying unfiltered and then through Photon (which indexes the same
OSM data slightly differently) recovers those queries.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
PHOTON_SEARCH_URL = "https://photon.komoot.io/api/"
OSRM_NEAREST_URL = "https://router.project-osrm.org/nearest/v1/driving"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

USER_AGENT = "BurnCostLogistics/1.0 (delivery address search)"
TIMEOUT = 8.0


class GeoUnavailable(RuntimeError):
    """Every configured provider failed (DNS/network/upstream error)."""


def google_enabled() -> bool:
    """Google Places/Geocoding is opt-in: set GOOGLE_MAPS_API_KEY to switch on."""
    return bool(getattr(settings, "GOOGLE_MAPS_API_KEY", None))


# ── Pure mapping helpers (unit-tested without a network) ────────────────

def city_state_from_address(addr: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Pick the most useful (city, state) pair out of an address dict."""
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("county")
    )
    return city, addr.get("state")


def label_from_address(addr: Dict[str, Any], fallback: str = "") -> str:
    """A short "road, city, state" label; falls back to the full display name."""
    parts = [
        addr.get("road") or addr.get("neighbourhood") or addr.get("suburb"),
        addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county"),
        addr.get("state"),
    ]
    short = ", ".join(p for p in parts if p)
    return short or fallback


def nominatim_to_results(raw: Any) -> List[dict]:
    """Map a Nominatim ``/search`` payload onto the picker's result shape."""
    out: List[dict] = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            lat = float(entry.get("lat"))
            lng = float(entry.get("lon"))
        except (TypeError, ValueError, AttributeError):
            continue
        addr = entry.get("address") or {}
        city, state = city_state_from_address(addr)
        out.append({
            "lat": lat,
            "lng": lng,
            "label": label_from_address(addr, entry.get("display_name") or ""),
            "city": city,
            "state": state,
            "country": addr.get("country"),
        })
    return out


def photon_to_results(raw: Any) -> List[dict]:
    """Map a Photon ``/api`` payload onto the picker's result shape.

    Photon hands back OSM-derived GeoJSON: the hierarchy arrives as separate
    properties (name/street/city/state/countrycode) rather than a formatted
    address, so the label is rebuilt the same way Nominatim's is.
    """
    features = (raw or {}).get("features") if isinstance(raw, dict) else None
    out: List[dict] = []
    for feature in features or []:
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        try:
            lng, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        street = props.get("street")
        road = f"{props.get('housenumber')} {street}".strip() if street else None
        city = props.get("city") or props.get("district") or props.get("county")
        label = ", ".join(
            p for p in [props.get("name") or road, city, props.get("state")] if p
        )
        out.append({
            "lat": lat,
            "lng": lng,
            "label": label or props.get("name") or "",
            "city": city,
            "state": props.get("state"),
            "country": props.get("country"),
            "countrycode": (props.get("countrycode") or "").upper() or None,
        })
    return out


def dedupe_results(results: List[dict], precision: int = 4) -> List[dict]:
    """Drop duplicate pins (same rounded coordinate) while preserving order."""
    seen = set()
    out: List[dict] = []
    for item in results:
        try:
            key = (round(float(item["lat"]), precision), round(float(item["lng"]), precision))
        except (KeyError, TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def prefer_country(results: List[dict], country: str = "Nigeria") -> List[dict]:
    """Stable-sort so local results lead, keeping provider order inside groups.

    Nominatim reports a country *name*, Photon a country *code*, so both are
    accepted here.
    """
    wanted = country.strip().lower()

    def rank(item: dict) -> int:
        name = (item.get("country") or "").strip().lower()
        code = (item.get("countrycode") or "").strip().upper()
        return 0 if (name == wanted or (wanted == "nigeria" and code == "NG")) else 1

    return sorted(results, key=rank)


# ── Upstream calls ─────────────────────────────────────────────────────

async def _get_json(client: httpx.AsyncClient, url: str, params: Dict[str, Any],
                    headers: Optional[Dict[str, str]] = None) -> Any:
    resp = await client.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def nominatim_search(query: str, limit: int, countrycodes: Optional[str] = None) -> List[dict]:
    """Nominatim ``/search``; ``countrycodes`` optionally biases to one country."""
    params: Dict[str, Any] = {
        "q": query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": limit,
        "accept-language": "en",
    }
    if countrycodes:
        params["countrycodes"] = countrycodes
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        raw = await _get_json(client, NOMINATIM_SEARCH_URL, params)
    return nominatim_to_results(raw)


async def photon_search(query: str, limit: int) -> List[dict]:
    """Photon ``/api`` search - the key-less fallback when Nominatim draws blank."""
    params: Dict[str, Any] = {"q": query, "limit": max(limit, 8), "lang": "en"}
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        raw = await _get_json(client, PHOTON_SEARCH_URL, params)
    return photon_to_results(raw)


async def google_search(query: str, limit: int) -> List[dict]:
    """Places API (New) ``searchText`` - only reached when a key is configured."""
    key = getattr(settings, "GOOGLE_MAPS_API_KEY", None)
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key or "",
        # Only the fields the picker renders - keeps the billed SKU minimal.
        "X-Goog-FieldMask": (
            "places.displayName,places.formattedAddress,places.location,"
            "places.addressComponents"
        ),
    }
    body = {"textQuery": query, "maxResultCount": limit, "languageCode": "en"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(GOOGLE_PLACES_URL, json=body, headers=headers)
        resp.raise_for_status()
        raw = resp.json()

    out: List[dict] = []
    for place in (raw or {}).get("places") or []:
        loc = place.get("location") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        components: Dict[str, str] = {}
        for comp in place.get("addressComponents") or []:
            for ctype in comp.get("types") or []:
                components.setdefault(
                    ctype, comp.get("longText") or comp.get("shortText") or ""
                )
        out.append({
            "lat": float(loc["latitude"]),
            "lng": float(loc["longitude"]),
            "label": (place.get("displayName") or {}).get("text")
                     or place.get("formattedAddress") or "",
            "city": components.get("locality") or components.get("administrative_area_level_2"),
            "state": components.get("administrative_area_level_1"),
            "country": components.get("country"),
            "countrycode": components.get("country"),
        })
    return out


async def search_places(query: str, limit: int = 5) -> List[dict]:
    """Landmark-friendly search chain (see the module docstring).

    Google runs first when configured; otherwise Nominatim is tried with a
    Nigeria bias, then unfiltered, and only if both come back empty does Photon
    get a turn. Raises :class:`GeoUnavailable` only when *every* attempt failed
    - an honest empty result comes back as ``[]`` so the picker can say
    "No matches" rather than "search is down".
    """
    tried = 0
    failed = 0
    last_error: Optional[Exception] = None

    async def attempt(coro):
        """Run one provider, swallowing its error so the chain keeps going."""
        nonlocal tried, failed, last_error
        tried += 1
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 - provider errors are interchangeable
            failed += 1
            last_error = exc
            return []

    hits: List[dict] = []
    if google_enabled():
        hits = await attempt(google_search(query, limit))
    if not hits:
        hits = await attempt(nominatim_search(query, limit, countrycodes="ng"))
    if not hits:
        hits = await attempt(nominatim_search(query, limit))
    if not hits:
        hits = await attempt(photon_search(query, limit))

    if not hits and tried and failed == tried:
        raise GeoUnavailable(str(last_error)) from last_error

    return dedupe_results(prefer_country(hits))[:limit]


async def nominatim_reverse(lat: float, lng: float) -> dict:
    """Nominatim ``/reverse`` - the pin's street/area description."""
    params: Dict[str, Any] = {
        "lat": lat,
        "lon": lng,
        "format": "jsonv2",
        "addressdetails": 1,
        "zoom": 16,  # street-ish granularity: an area name, not a country
        "accept-language": "en",
    }
    async with httpx.AsyncClient(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        raw = await _get_json(client, NOMINATIM_REVERSE_URL, params)
    addr = (raw or {}).get("address") or {}
    city, state = city_state_from_address(addr)
    return {
        "label": label_from_address(addr, (raw or {}).get("display_name") or ""),
        "city": city,
        "state": state,
        "country": addr.get("country"),
    }


async def google_reverse(lat: float, lng: float) -> dict:
    """Geocoding API reverse lookup - only reached when a key is configured."""
    key = getattr(settings, "GOOGLE_MAPS_API_KEY", None)
    params = {"latlng": f"{lat},{lng}", "key": key, "language": "en"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        raw = await _get_json(client, GOOGLE_GEOCODE_URL, params)
    if (raw or {}).get("status") != "OK" or not (raw or {}).get("results"):
        raise GeoUnavailable(f"Google reverse geocode status {(raw or {}).get('status')}")
    entry = (raw or {}).get("results")[0]
    addr: Dict[str, Any] = {}
    for comp in entry.get("address_components") or []:
        for ctype in comp.get("types") or []:
            addr.setdefault(ctype, comp.get("long_name"))
    city, state = city_state_from_address({
        "city": addr.get("locality") or addr.get("postal_town"),
        "county": addr.get("administrative_area_level_2"),
        "state": addr.get("administrative_area_level_1"),
    })
    return {
        "label": label_from_address(addr, entry.get("formatted_address") or ""),
        "city": city,
        "state": state,
        "country": addr.get("country"),
    }


async def reverse_place(lat: float, lng: float) -> dict:
    """Describe a pin. Google first when configured, otherwise Nominatim."""
    if google_enabled():
        try:
            return await google_reverse(lat, lng)
        except Exception:  # noqa: BLE001 - fall back to the key-less provider
            pass
    try:
        return await nominatim_reverse(lat, lng)
    except Exception as exc:  # noqa: BLE001
        raise GeoUnavailable(str(exc)) from exc


async def snap_to_road(lat: float, lng: float) -> Optional[dict]:
    """Nearest routable point via OSRM's demo server (key-less).

    The distance doubles as a free sanity check on a fix: a genuine handheld
    fix sits within tens of metres of a road, while a coarse Wi-Fi/IP estimate
    can land kilometres from one. Returns ``None`` when the upstream is
    unreachable - the caller treats "no snap" as "unknown", never as "bad".
    """
    url = f"{OSRM_NEAREST_URL}/{lng},{lat}"  # OSRM takes lon,lat
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            raw = await _get_json(client, url, {"number": 1})
    except Exception:  # noqa: BLE001 - advisory only, never fatal
        return None
    waypoints = (raw or {}).get("waypoints") or []
    location = (waypoints[0].get("location") if waypoints else None) or []
    if len(location) < 2:
        return None
    try:
        return {
            "lat": float(location[1]),
            "lng": float(location[0]),
            "distance_m": float(waypoints[0].get("distance") or 0.0),
        }
    except (TypeError, ValueError):
        return None
