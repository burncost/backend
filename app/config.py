from pydantic_settings import BaseSettings
from typing import List, Optional
from functools import lru_cache


class Settings(BaseSettings):
    # Application
    PROJECT_NAME: str = "Burncost"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool
    
    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:8000",
        # "https://www.burncost.com/",
        # "https://burncost.com/"
    ]
    
    # PostgreSQL Database
    DEV_POSTGRES_SERVER: str
    DEV_POSTGRES_USER: str
    DEV_POSTGRES_PASSWORD: str
    DEV_POSTGRES_DB: str
    DEV_POSTGRES_PORT: str

    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_PORT: int = 5432
    
    @property
    def DATABASE_URL(self) -> str:
        if settings.DEBUG==False:
            return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@/{self.POSTGRES_DB}?host=/cloudsql/{self.POSTGRES_SERVER}"
        else:
            return f"postgresql+asyncpg://{self.DEV_POSTGRES_USER}:{self.DEV_POSTGRES_PASSWORD}@{self.DEV_POSTGRES_SERVER}:{self.DEV_POSTGRES_PORT}/{self.DEV_POSTGRES_DB}"
        

    # MongoDB Database
    MONGO_HOST: str
    MONGO_PORT: int = 27017
    MONGO_DB: str 
    MONGO_USER: Optional[str] = None
    MONGO_PASSWORD: Optional[str] = None
    
    @property
    def MONGODB_URL(self) -> str:
        if self.MONGO_USER and self.MONGO_PASSWORD and settings.DEBUG==False:
            return f"mongodb+srv://{self.MONGO_USER}:{self.MONGO_PASSWORD}@burncost.cpm6qy5.mongodb.net/{self.MONGO_DB}?retryWrites=true&w=majority"
        else:
            return f"mongodb://{self.MONGO_USER}:{self.MONGO_PASSWORD}@{self.MONGO_HOST}:{self.MONGO_PORT}/{self.MONGO_DB}"
        # return f"mongodb://{self.MONGO_HOST}:{self.MONGO_PORT}"
    
    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_EMAIL: str
    REDIS_PASSWORD: str
    
    UPSTASH_REDIS_REST_URL: Optional[str] = None
    UPSTASH_REDIS_REST_TOKEN: Optional[str] = None

    @property
    def REDIS_URL(self) -> str:
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN and settings.DEBUG==False:
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{self.UPSTASH_REDIS_REST_URL}:6379"
        else:
            return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # Celery
    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None
    
    # Cloud Storage
    STORAGE_PROVIDER: str = "gcs"
    LOCAL_STORAGE_PATH: Optional[str] = None  # ADD THIS
    GCS_BUCKET_NAME: Optional[str] = None
    GCS_PROJECT_ID: Optional[str] = None
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_S3_BUCKET: Optional[str] = None
    
    # Payment Gateways
    PAYSTACK_SECRET_KEY: Optional[str] = None
    PAYSTACK_PUBLIC_KEY: Optional[str] = None
    FLUTTERWAVE_SECRET_KEY: Optional[str] = None
    FLUTTERWAVE_PUBLIC_KEY: Optional[str] = None
    
    # Email Configuration
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAILS_FROM_EMAIL: Optional[str] = None
    EMAILS_FROM_NAME: Optional[str] = None
    
    # SMS Configuration
    SMS_PROVIDER: str = "termii"
    TERMII_API_KEY: Optional[str] = None
    TERMII_SENDER_ID: Optional[str] = None
    
    # AI/ML Service
    AI_SERVICE_URL: Optional[str] = None
    AI_SERVICE_API_KEY: Optional[str] = None
    AI_MODEL: Optional[str] = None
    AI_BASE_URL: Optional[str] = None
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = 60
    
    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100
    
    # File Upload
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024
    ALLOWED_DOCUMENT_EXTENSIONS: List[str] = [
        ".dwg", ".dxf", ".rvt", ".ifc", ".pdf", ".xlsx", ".docx"
    ]
    
    # BigQuery
    BIGQUERY_PROJECT_ID: Optional[str] = None
    BIGQUERY_DATASET: Optional[str] = None
    
    # ADD THESE MISSING FIELDS:
    ENABLE_REGISTRATION: bool = True
    ENABLE_VENDOR_REGISTRATION: bool = True
    ENABLE_PAYMENT_GATEWAY: bool = True
    ENABLE_SMS_NOTIFICATIONS: bool = True
    ENABLE_EMAIL_NOTIFICATIONS: bool = True
    ENABLE_AI_BOQ_GENERATION: bool = True
    
    MOCK_PAYMENT_GATEWAY: bool = False
    BYPASS_EMAIL_VERIFICATION: bool = False
    TESTING: bool = False

    # CLOUDINARY
    CLOUDINARY_CLOUD_NAME: Optional[str] = None 
    CLOUDINARY_API_KEY: Optional[str] = None
    CLOUDINARY_API_SECRET: Optional[str] = None

    # API_URL: str = "http://localhost:8000/api/v1"
    API_URL: str = "https://api.burncost.com/api/v1"
    
    LOG_LEVEL: str = "INFO"
    # FRONTEND_URL: str = "http://localhost:5173"
    FRONTEND_URL: str = "https://burncost.com"

    RESEND_API_KEY: Optional[str]

    BREVO_API_KEY: Optional[str]
    
    class Config:
        env_file = ".env"
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
