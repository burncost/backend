from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from Backend.final import app
from config import settings
import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET
)

@app.post("/upload-document/")
async def upload_document(file: UploadFile = File(...)):
    result = cloudinary.uploader.upload(
        file.file,
        folder="supplier_documents",
        resource_type="auto"
    )

    return {
        "url": result["secure_url"]
    }