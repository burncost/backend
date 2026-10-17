"""Image normalisation for user uploads (driver vehicle photos).

Vehicle photos arrive straight from phone cameras: 8-15 MP, 4-12 MB, often with
EXIF orientation and GPS metadata. We do not need (and cannot afford) the
original: 1920 px on the longest edge is more than enough to identify a vehicle,
its plate and any body damage, and it keeps Cloudinary storage and mobile data
usage sane.

The contract is byte-budget driven: a photo is resized and re-encoded until the
stored JPEG is at or below ``max_bytes`` (default 0.5 MB), *without* dropping
below a readability floor. Quality is stepped down first (cheap, keeps pixels),
then the dimensions shrink only if the quality ladder is exhausted. The first
encode that fits wins, so the result is always the highest quality that fits the
budget.

Nothing here trusts the filename or the declared MIME type: the content is
decoded by Pillow, and ``Image.MAX_IMAGE_PIXELS`` stops decompression bombs
before a single pixel is allocated.
"""
from __future__ import annotations

import io
import logging
from typing import Optional, Tuple

from PIL import Image, ImageOps

from app.config import settings

logger = logging.getLogger(__name__)

# Pillow raises DecompressionBombError above 2x this value and only warns in
# between. 40 MP covers every phone camera we expect; anything larger is either
# a scan we cannot process in memory or a bomb.
Image.MAX_IMAGE_PIXELS = 40_000_000

# Quality ladder: highest first, so we stop at the best quality that fits.
QUALITY_STEPS: Tuple[int, ...] = (88, 85, 82, 78, 75, 72, 68, 64, 60)
# Never shrink below this on the longest edge - the photo must stay legible.
MIN_EDGE = 1200
# Each failed pass through the quality ladder shrinks the long edge by this much.
SCALE_STEP = 0.88
# Formats we accept as input. Anything else is rejected before decode.
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}


class ImageTooLarge(Exception):
    """The image could not be reduced to the byte budget without losing legibility."""


class UnsupportedImage(Exception):
    """The uploaded bytes are not a decodable image in an accepted format."""


def _flatten(img: Image.Image) -> Image.Image:
    """Return an RGB image; transparent PNGs are matted on white (JPEG has no alpha)."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def _encode(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def normalize_image(
    content: bytes,
    *,
    max_bytes: Optional[int] = None,
    max_edge: Optional[int] = None,
) -> bytes:
    """Downscale + re-encode ``content`` to a JPEG of at most ``max_bytes``.

    Raises ``UnsupportedImage`` when the bytes are not a supported image and
    ``ImageTooLarge`` when even the readability floor cannot fit the budget.
    """
    max_bytes = int(max_bytes or settings.VEHICLE_PHOTO_MAX_BYTES)
    max_edge = int(max_edge or settings.VEHICLE_PHOTO_MAX_EDGE)

    if not content:
        raise UnsupportedImage("Empty file")

    try:
        with Image.open(io.BytesIO(content)) as opened:
            if (opened.format or "").upper() not in ALLOWED_FORMATS:
                raise UnsupportedImage(f"Unsupported image format: {opened.format}")
            # Apply the camera's EXIF orientation so a portrait photo is not
            # stored sideways, then strip all metadata (EXIF/GPS) on re-encode.
            img = ImageOps.exif_transpose(opened)
            img = _flatten(img)
            img.load()  # force decode now; catches truncated/corrupt files here
    except UnsupportedImage:
        raise
    except Image.DecompressionBombError as exc:  # pragma: no cover - defensive
        raise UnsupportedImage("Image dimensions are too large") from exc
    except Exception as exc:
        raise UnsupportedImage("File is not a valid image") from exc

    if img.width < 1 or img.height < 1:
        raise UnsupportedImage("File is not a valid image")

    # Never upscale: a small photo is stored at its native size.
    edge = min(max_edge, max(img.width, img.height))

    while True:
        working = img
        if max(working.width, working.height) > edge:
            working = working.copy()
            working.thumbnail((edge, edge), Image.LANCZOS)

        for quality in QUALITY_STEPS:
            encoded = _encode(working, quality)
            if len(encoded) <= max_bytes:
                return encoded

        # Quality floor reached and still too big: shrink and retry.
        next_edge = int(edge * SCALE_STEP)
        if next_edge < MIN_EDGE:
            raise ImageTooLarge(
                "Photo could not be reduced below the size limit - try a smaller image"
            )
        edge = next_edge
