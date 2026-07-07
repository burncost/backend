"""Storage Service - Real file storage using Cloudinary and local fallback."""
from typing import Optional, Dict, Any
import logging
import os
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)


class StorageService:
    """Service for file storage operations with Cloudinary and local fallback."""

    def __init__(self):
        self.cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
        self.cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY", "")
        self.cloudinary_api_secret = os.getenv("CLOUDINARY_API_SECRET", "")
        self.upload_dir = os.getenv("UPLOAD_DIR", "uploads")
        self.use_cloudinary = all([
            self.cloudinary_cloud_name,
            self.cloudinary_api_key,
            self.cloudinary_api_secret,
        ])

        # Ensure upload directory exists
        os.makedirs(self.upload_dir, exist_ok=True)

    async def upload_file(
        self,
        file: bytes,
        folder: str = "general",
        filename: Optional[str] = None
    ) -> str:
        """Upload a file and return its URL."""
        if not filename:
            filename = f"{uuid.uuid4().hex}"

        if self.use_cloudinary:
            return await self._upload_to_cloudinary(file, folder, filename)

        return await self._upload_local(file, folder, filename)

    async def upload_bytes(
        self,
        content: bytes,
        path: str,
        content_type: str = "application/octet-stream"
    ) -> str:
        """Upload raw bytes to a specific path."""
        if self.use_cloudinary:
            return await self._upload_bytes_cloudinary(content, path, content_type)

        return await self._upload_bytes_local(content, path)

    async def delete_file(self, url: str) -> bool:
        """Delete a file by its URL."""
        if self.use_cloudinary and "cloudinary" in url:
            return await self._delete_from_cloudinary(url)

        return await self._delete_local(url)

    async def get_file_url(self, path: str) -> str:
        """Get the URL for a stored file."""
        if self.use_cloudinary:
            return f"https://res.cloudinary.com/{self.cloudinary_cloud_name}/image/upload/{path}"

        return f"/uploads/{path}"

    async def _upload_to_cloudinary(self, file: bytes, folder: str, filename: str) -> str:
        """Upload file to Cloudinary."""
        try:
            import cloudinary
            import cloudinary.uploader

            cloudinary.config(
                cloud_name=self.cloudinary_cloud_name,
                api_key=self.cloudinary_api_key,
                api_secret=self.cloudinary_api_secret,
            )

            result = cloudinary.uploader.upload(
                file,
                folder=folder,
                public_id=filename,
                resource_type="auto",
            )

            return result.get("secure_url", "")

        except ImportError:
            logger.warning("cloudinary not installed, falling back to local storage")
            return await self._upload_local(file, folder, filename)
        except Exception as e:
            logger.error(f"Cloudinary upload failed: {str(e)}")
            return await self._upload_local(file, folder, filename)

    async def _upload_local(self, file: bytes, folder: str, filename: str) -> str:
        """Upload file to local filesystem."""
        safe_folder = folder.replace("/", os.sep).replace("\\", os.sep)
        upload_path = os.path.join(self.upload_dir, safe_folder)
        os.makedirs(upload_path, exist_ok=True)

        file_path = os.path.join(upload_path, filename)
        with open(file_path, "wb") as f:
            f.write(file)

        return f"/uploads/{safe_folder}/{filename}"

    async def _upload_bytes_cloudinary(self, content: bytes, path: str, content_type: str) -> str:
        """Upload raw bytes to Cloudinary."""
        try:
            import cloudinary
            import cloudinary.uploader

            cloudinary.config(
                cloud_name=self.cloudinary_cloud_name,
                api_key=self.cloudinary_api_key,
                api_secret=self.cloudinary_api_secret,
            )

            folder = os.path.dirname(path).replace("\\", "/")
            public_id = os.path.basename(path).rsplit(".", 1)[0]

            result = cloudinary.uploader.upload(
                content,
                folder=folder,
                public_id=public_id,
                resource_type="auto",
            )

            return result.get("secure_url", "")

        except ImportError:
            return await self._upload_bytes_local(content, path)
        except Exception as e:
            logger.error(f"Cloudinary bytes upload failed: {str(e)}")
            return await self._upload_bytes_local(content, path)

    async def _upload_bytes_local(self, content: bytes, path: str) -> str:
        """Upload raw bytes to local filesystem."""
        safe_path = path.replace("/", os.sep).replace("\\", os.sep)
        full_path = os.path.join(self.upload_dir, safe_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(content)

        return f"/uploads/{safe_path}"

    async def _delete_from_cloudinary(self, url: str) -> bool:
        """Delete a file from Cloudinary."""
        try:
            import cloudinary
            import cloudinary.api

            cloudinary.config(
                cloud_name=self.cloudinary_cloud_name,
                api_key=self.cloudinary_api_key,
                api_secret=self.cloudinary_api_secret,
            )

            # Extract public_id from URL
            public_id = url.split("/upload/")[-1].rsplit(".", 1)[0]
            result = cloudinary.uploader.destroy(public_id)
            return result.get("result") == "ok"

        except Exception as e:
            logger.error(f"Cloudinary delete failed: {str(e)}")
            return False

    async def _delete_local(self, url: str) -> bool:
        """Delete a file from local filesystem."""
        try:
            local_path = url.lstrip("/")
            full_path = os.path.join(self.upload_dir, local_path.replace("uploads/", "", 1))
            if os.path.exists(full_path):
                os.remove(full_path)
                return True
            return False
        except Exception as e:
            logger.error(f"Local delete failed: {str(e)}")
            return False
