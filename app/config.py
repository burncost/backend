from pydantic_settings import BaseSettings
from typing import List, Optional
from functools import lru_cache
from urllib.parse import quote_plus


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

    PORT: int
    
    # CORS
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:8000",
        "https://burncost.com",
        "https://www.burncost.com",
        "https://demo.burncost.com",
        "https://onboard.burncost.com",
        "https://admin.burncost.com"
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
        if self.DEBUG==False:
            # return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@/{self.POSTGRES_DB}?host=/cloudsql/{self.POSTGRES_SERVER}"
            return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}?ssl=require"
        else:
            return f"postgresql+asyncpg://{self.DEV_POSTGRES_USER}:{self.DEV_POSTGRES_PASSWORD}@{self.DEV_POSTGRES_SERVER}:{self.DEV_POSTGRES_PORT}/{self.DEV_POSTGRES_DB}"
            # return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}?ssl=require"


    # MongoDB Database
    MONGO_HOST: str
    MONGO_PORT: int = 27017
    MONGO_DB: str 
    MONGO_USER: Optional[str] = None
    MONGO_PASSWORD: Optional[str] = None
    
    @property
    def MONGODB_URL(self) -> str:
        if self.DEBUG==False:
            return f"mongodb+srv://{self.MONGO_USER}:{self.MONGO_PASSWORD}@burncost.cpm6qy5.mongodb.net/{self.MONGO_DB}?retryWrites=true&w=majority"
        else:
            # return f"mongodb://{self.MONGO_USER}:{self.MONGO_PASSWORD}@{self.MONGO_HOST}:{self.MONGO_PORT}/{self.MONGO_DB}"
            return f"mongodb://{self.MONGO_HOST}:{self.MONGO_PORT}/{self.MONGO_DB}"
    
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
        if self.DEBUG==False:
            return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{self.UPSTASH_REDIS_REST_URL}:6379"
        else:
            return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
            # return f"rediss://default:{self.UPSTASH_REDIS_REST_TOKEN}@{self.UPSTASH_REDIS_REST_URL}:6379"
    
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
    # Webhook `verif-hash` value set in the Flutterwave dashboard. Required for
    # the /payments/webhook/flutterwave endpoint (fail-closed when unset).
    FLUTTERWAVE_SECRET_HASH: Optional[str] = None
    MOCK_PAYMENT_GATEWAY: Optional[bool] = None
    MONNIFY_SECRET_KEY: Optional[str] = None
    MONNIFY_PUBLIC_KEY: Optional[str] = None
    MONNIFY_CONTRACT_CODE: Optional[str] = None
    MONNIFY_BASE_URL: Optional[str] = None

    # Identity verification (Mono) - NIN is bypassed until the flag is on;
    # CAC verification is enforced whenever a Mono key is configured.
    MONO_SECRET_KEY: Optional[str] = None
    IDENTITY_VERIFY_ENABLED: bool = False
    
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

    # Google Gemini AI
    GOOGLE_PROJECT_ID: str = "burncost-493208"
    GOOGLE_LOCATION: str = "eu"
    GOOGLE_CREDS_PATH: str = "google_creds.json"

    # ── Online price search (BOQ price chain, tier 3) ──────────────────────
    # Consulted ONLY after the product catalogue and material_rates have no
    # price for a line. Every lookup is cached and budgeted per BOQ run, and a
    # failure (site down, no network) just moves on to the offline reference -
    # a BOQ always completes.
    ONLINE_PRICE_SEARCH_ENABLED: bool = True
    # Comma-separated published price-list pages to read first.
    ONLINE_PRICE_SEARCH_SOURCES: str = "https://www.landalyst.com/prices"
    ONLINE_PRICE_SEARCH_TIMEOUT_SECONDS: float = 8.0
    # Hard cap on live lookups per BOQ run (cache hits do not count).
    ONLINE_PRICE_SEARCH_MAX_PER_RUN: int = 12
    # How long a fetched online price may be reused (in-process + MongoDB).
    ONLINE_PRICE_SEARCH_CACHE_HOURS: int = 168

    # ── Proof of delivery (geotagged live-photo gate) ──────────────────────
    # A driver's photo proof must be fresh (captured within this many minutes)
    # to unlock "mark delivered". Distance to the dropoff is recorded and
    # flagged for review but never blocks (advisory only).
    POD_GEOFENCE_RADIUS_M: int = 300
    POD_MAX_PHOTO_AGE_MIN: int = 60
    # A photo without GPS still carries the capture time; rather than dead-ending
    # the proof in the review queue the buyer is asked to confirm receipt. The
    # confirm action is only allowed while the capture calendar-day matches
    # "today" in this timezone (the drop-off locale for the primary market).
    POD_BUYER_CONFIRM_TZ: str = "Africa/Lagos"

    # ── Maps / geocoding (delivery map pickers) ────────────────────────────
    # Search + reverse-geocoding run key-less (Nominatim/Photon + OSRM) by
    # default. Set a Google Maps Platform key to switch those lookups to
    # Places/Geocoding instead - see `app/services/geo_providers.py`.
    GOOGLE_MAPS_API_KEY: Optional[str] = None

    # OAuth (Phase 12)
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    FACEBOOK_CLIENT_ID: Optional[str] = None
    FACEBOOK_CLIENT_SECRET: Optional[str] = None

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

    # ── Driver vehicle photos (onboarding gate) ─────────────────────────────
    # Every vehicle photo is normalised before storage so the stored file never
    # exceeds 0.5 MB. 0.5 MB is the *output* budget; the incoming upload may be
    # larger (up to MAX_VEHICLE_PHOTO_UPLOAD_BYTES) because it is downscaled
    # first. 1920 px on the longest edge keeps plates/body panels legible.
    VEHICLE_PHOTO_MAX_BYTES: int = 500 * 1024            # 512,000 bytes
    VEHICLE_PHOTO_MAX_EDGE: int = 1920                   # longest edge, px
    VEHICLE_PHOTO_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    VEHICLE_PHOTO_ANGLES: List[str] = ["front", "back", "left", "right"]
    
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
    
    BYPASS_EMAIL_VERIFICATION: bool = False
    TESTING: bool = False

    # CLOUDINARY
    CLOUDINARY_CLOUD_NAME: Optional[str] = None 
    CLOUDINARY_API_KEY: Optional[str] = None
    CLOUDINARY_API_SECRET: Optional[str] = None

    API_URL: str = None
    
    LOG_LEVEL: str = "INFO"
    FRONTEND_URL: str = None

    RESEND_API_KEY: Optional[str]

    BREVO_API_KEY: Optional[str]

    UPLOAD_DIR: Optional[str]

    # Token system configuration
    TOKEN_COSTS: dict = {
        "boq_generate_manual": 1,
        "boq_generate_drawing": 2,
        "export_pdf": 1,
        "export_excel": 0.5,
        "export_docx": 0.5,
        "export_csv": 0.5,
        "boq_regenerate": 1,
    }
    FREE_TIER_MONTHLY_TOKENS: int = 2
    TOKEN_PACKS: list = [
        {"tokens": 10, "price_ngn": 5_000, "price_per_token": 500},
        {"tokens": 50, "price_ngn": 20_000, "price_per_token": 400},
        {"tokens": 200, "price_ngn": 60_000, "price_per_token": 300},
    ]

    # ── Pricing: platform-margin infusion + VAT ─────────────────────────────
    # Margin is charged on the EFFECTIVE selling price (the discounted price when
    # one exists) per category, using the category's `fee_model`:
    #   percentage -> total_price x platform_margin%        (platform_margin = %)
    #   fixed      -> fee_fixed x quantity                  (fee_fixed    = NGN)
    #   service    -> treated as `percentage`
    # The margin is shared between the buyer (added on top of the item subtotal)
    # and the vendor (deducted from the settlement payout). The two shares must
    # sum to 1.0. These are the config-level fallbacks; admins can override them
    # at runtime through the `system_settings` table (see app.services.runtime_settings).
    CLIENT_MARGIN_SHARE: str = "0.40"
    VENDOR_MARGIN_SHARE: str = "0.60"

    # VAT is admin-switchable. `VAT_RATE` is a percentage (7.5 = 7.5%).
    # `VAT_BASE` selects what VAT is charged on:
    #   "subtotal_plus_margin" -> (subtotal + platform_fee)   [default]
    #   "subtotal"             -> subtotal only
    VAT_ENABLED: bool = True
    VAT_RATE: str = "7.5"
    VAT_BASE: str = "subtotal_plus_margin"

    # ── Settlement & disbursement (Phase 5) ─────────────────────────────────
    # Master switch for settlement accrual + vendor payouts. OFF by default so
    # nothing moves money until an admin explicitly enables it. Admin-flippable
    # at runtime via /admin/settlements/config (writes `system_settings`).
    SETTLEMENT_ENABLED: bool = False
    # Extra platform commission taken at settlement as a FRACTION (0.05 = 5%).
    # Defaults to 0 because the platform margin is already infused into the
    # selling price at checkout.
    SETTLEMENT_COMMISSION_RATE: str = "0.0"
    # Payout provider preference: Paystack primary, Monnify backup.
    PAYOUT_PRIMARY_GATEWAY: str = "paystack"
    PAYOUT_BACKUP_GATEWAY: str = "monnify"

    class Config:
        env_file = ".env"
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
