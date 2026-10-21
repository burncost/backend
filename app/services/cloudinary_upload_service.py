import cloudinary
import cloudinary.uploader
from fastapi import UploadFile, HTTPException, status
import logging

from app.config import settings

logger = logging.getLogger(__name__)

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)


async def upload_image_to_cloudinary(content: bytes) -> str:
    """Upload already-normalised image bytes to Cloudinary and return the secure URL.

    ``content`` must be the output of ``app.utils.image_processing.normalize_image``
    - the byte budget and EXIF stripping happen there, not here.
    """
    try:
        result = cloudinary.uploader.upload(
            content,
            folder="vendor_images",
            resource_type="image",
            transformation=[
                {"width": 800, "height": 800, "crop": "limit"},
                {"quality": "auto", "fetch_format": "auto"},
            ],
        )
        return result["secure_url"]
    except Exception as e:
        logger.error(f"Cloudinary upload failed: {e}")
        raise