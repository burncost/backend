"""Driver vehicle-photo onboarding requirement (pure helpers, no DB/HTTP).

Covers the two halves of the rule:
- the image pipeline that guarantees every stored photo is <= 0.5 MB while
  staying legible (and never upscaling a small photo), and
- the model / endpoint helpers that decide whether the four-angle set is
  complete - the same truth the delivery accept gate reads.
"""
import io

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.models.driver import DriverProfile
from app.api.v1.endpoints.drivers import _validate_angle
from app.api.v1.endpoints.deliveries import _require_vehicle_photos
from app.utils.image_processing import (
    ImageTooLarge,
    UnsupportedImage,
    normalize_image,
)


def _noise_jpeg(width: int, height: int, quality: int = 95) -> bytes:
    """A detailed, hard-to-compress test image (a flat fill would fit instantly)."""
    img = Image.effect_noise((width, height), 96).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _flat_jpeg(width: int, height: int) -> bytes:
    """A clean, highly compressible image - used where only geometry matters."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


# -- image normalisation -------------------------------------------------

def test_normalize_respects_byte_budget():
    out = normalize_image(_noise_jpeg(900, 700), max_bytes=320_000, max_edge=1920)
    assert len(out) <= 320_000
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 900


def test_default_budget_is_half_a_megabyte():
    assert settings.VEHICLE_PHOTO_MAX_BYTES == 512_000
    assert settings.VEHICLE_PHOTO_MAX_EDGE == 1920


def test_large_photo_is_downscaled_to_max_edge():
    out = normalize_image(_flat_jpeg(3000, 2000))
    with Image.open(io.BytesIO(out)) as img:
        assert max(img.size) <= settings.VEHICLE_PHOTO_MAX_EDGE


def test_small_photo_is_never_upscaled():
    out = normalize_image(_noise_jpeg(640, 480))
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (640, 480)


def test_transparent_png_is_flattened_to_rgb_jpeg():
    buf = io.BytesIO()
    Image.new("RGBA", (500, 400), (10, 20, 30, 0)).save(buf, format="PNG")
    out = normalize_image(buf.getvalue())
    with Image.open(io.BytesIO(out)) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert img.size == (500, 400)


def test_non_image_bytes_are_rejected():
    with pytest.raises(UnsupportedImage):
        normalize_image(b"this is definitely not an image")
    with pytest.raises(UnsupportedImage):
        normalize_image(b"")


def test_unreachable_budget_raises_image_too_large():
    # A 40 KB budget cannot be met without dropping below the readability floor.
    with pytest.raises(ImageTooLarge):
        normalize_image(_noise_jpeg(900, 700), max_bytes=40_000, max_edge=1920)


# -- angle validation ----------------------------------------------------

def test_angle_validation_is_case_and_space_insensitive():
    assert _validate_angle(" Front ") == "front"
    assert _validate_angle("BACK") == "back"
    assert _validate_angle("left") == "left"
    assert _validate_angle("right") == "right"


@pytest.mark.parametrize("angle", ["", "front-left", "side", "roof"])
def test_unknown_angle_is_rejected(angle):
    with pytest.raises(HTTPException) as exc:
        _validate_angle(angle)
    assert exc.value.status_code == 422
    assert "front" in exc.value.detail


# -- completeness / delivery gate ---------------------------------------

def _profile(**photos) -> DriverProfile:
    return DriverProfile(**photos)


def test_profile_is_incomplete_with_no_photos():
    profile = _profile()
    assert profile.vehicle_photos_complete is False
    assert profile.missing_vehicle_photos == ["front", "back", "left", "right"]


def test_profile_tracks_partial_photo_set():
    profile = _profile(vehicle_photo_front="https://cdn/front.jpg")
    assert profile.vehicle_photos == {
        "front": "https://cdn/front.jpg",
        "back": None,
        "left": None,
        "right": None,
    }
    assert profile.missing_vehicle_photos == ["back", "left", "right"]
    assert profile.vehicle_photos_complete is False


def test_profile_is_complete_with_all_four_photos():
    profile = _profile(
        vehicle_photo_front="https://cdn/front.jpg",
        vehicle_photo_back="https://cdn/back.jpg",
        vehicle_photo_left="https://cdn/left.jpg",
        vehicle_photo_right="https://cdn/right.jpg",
    )
    assert profile.missing_vehicle_photos == []
    assert profile.vehicle_photos_complete is True
    assert _require_vehicle_photos(profile) is None


def test_delivery_gate_names_the_missing_angles():
    profile = _profile(
        vehicle_photo_front="https://cdn/front.jpg",
        vehicle_photo_back="https://cdn/back.jpg",
    )
    with pytest.raises(HTTPException) as exc:
        _require_vehicle_photos(profile)
    assert exc.value.status_code == 403
    assert "left" in exc.value.detail and "right" in exc.value.detail
    assert "front" not in exc.value.detail
