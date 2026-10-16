"""Extract lightweight EXIF metadata from an uploaded proof photo.

Used by the delivery proof-of-delivery flow to (a) confirm a photo is genuinely
live/geotagged and (b) persist the capture time + GPS so a flagged delivery can
be reviewed later. Design constraint: this is best-effort and NEVER raises — a
missing/broken EXIF block simply yields empty fields so proof capture is not
crashed by a camera that omits metadata (the caller decides how to treat that).
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Optional

try:  # Pillow is a hard dependency, but guard the import so a stripped
    # deployment that removes image libs can still import this module.
    from PIL import Image, ExifTags  # type: ignore

    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ExifTags = None  # type: ignore
    _PIL_AVAILABLE = False


# EXIF tags we keep in the audit blob (kept small so `raw_exif` stays sane).
_KEEP_TAGS = {
    "DateTimeOriginal",
    "DateTimeDigitized",
    "DateTime",
    "Make",
    "Model",
    "Software",
    "Orientation",
    "ExifImageWidth",
    "ExifImageHeight",
}

# GPS lives in a dedicated sub-IFD (0x8825).
_GPS_IFD_TAG = 0x8825


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dms_to_decimal(dms: Any, ref: Any) -> Optional[float]:
    """Convert a GPS (degrees, minutes, seconds) triple to signed decimal."""
    if not dms or len(dms) != 3:
        return None
    try:
        deg = _to_float(dms[0]) or 0.0
        minutes = _to_float(dms[1]) or 0.0
        seconds = _to_float(dms[2]) or 0.0
        decimal = deg + minutes / 60.0 + seconds / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode("ascii", "ignore")
        if isinstance(ref, str) and ref.strip().upper() in ("S", "W"):
            decimal = -decimal
        return round(decimal, 8)
    except Exception:
        return None


def _parse_exif_datetime(raw: Any) -> Optional[datetime]:
    """Parse the EXIF 'YYYY:MM:DD HH:MM:SS' stamp into a naive UTC datetime."""
    if not raw:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", "ignore")
        except Exception:
            return None
    if not isinstance(raw, str):
        return None
    raw = raw.strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _as_jsonable(value: Any) -> Any:
    """Best-effort coercion of EXIF values into JSON-serialisable primitives."""
    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", "ignore")
        if isinstance(value, (list, tuple)):
            return [_as_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _as_jsonable(v) for k, v in value.items()}
        return str(value)
    except Exception:
        return str(value)


def extract_photo_metadata(content: bytes) -> Dict[str, Any]:
    """Return metadata gleaned from an image's EXIF block.

    Keys:
      - ``exif_taken_at`` (datetime | None): DateTimeOriginal/Digitized/DateTime
      - ``exif_lat`` / ``exif_lng`` (float | None): GPS position, signed decimal
      - ``has_exif`` (bool): whether any readable EXIF block was present
      - ``has_gps`` (bool): whether a usable GPS position was decoded
      - ``raw_exif`` (dict): the compact, JSON-safe EXIF payload for audit
    """
    result: Dict[str, Any] = {
        "exif_taken_at": None,
        "exif_lat": None,
        "exif_lng": None,
        "has_exif": False,
        "has_gps": False,
        "raw_exif": {},
    }
    if not content or not _PIL_AVAILABLE:
        return result

    try:
        with Image.open(BytesIO(content)) as img:
            try:
                exif = img.getexif()
            except Exception:
                exif = None
            if not exif:
                return result

            raw: Dict[str, Any] = {}
            taken_at: Optional[datetime] = None
            for tag_id, value in exif.items():
                name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                    taken_at = taken_at or _parse_exif_datetime(value)
                if name in _KEEP_TAGS:
                    raw[name] = _as_jsonable(value)

            # GPS lives in a sub-IFD.
            gps_lat = gps_lng = None
            try:
                gps_ifd = exif.get_ifd(_GPS_IFD_TAG)
            except Exception:
                gps_ifd = {}
            if gps_ifd:
                gps = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in gps_ifd.items()}
                gps_lat = _dms_to_decimal(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef"))
                gps_lng = _dms_to_decimal(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef"))
                if taken_at is None:
                    taken_at = _parse_exif_datetime(gps.get("GPSDateStamp"))
                raw["GPS"] = _as_jsonable(gps)

            result["exif_taken_at"] = taken_at
            result["exif_lat"] = gps_lat
            result["exif_lng"] = gps_lng
            result["has_exif"] = True
            result["has_gps"] = gps_lat is not None and gps_lng is not None
            result["raw_exif"] = raw
    except Exception:
        # A non-image upload or corrupt file: keep the empty defaults.
        return result

    return result
