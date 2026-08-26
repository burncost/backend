"""Pytest bootstrap — ensure `app` is importable and Settings resolves.

The project root (containing `app/`) is `Backend/`. Insert it into sys.path so
tests can `from app... import ...` regardless of the working directory.

`app.config.Settings()` requires env vars (it has no defaults). We set a minimal
set here (test-scoped only — the real `.env` is untouched) before any app import.
"""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent  # Backend/
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ── Minimal env vars so pydantic Settings() can instantiate in tests ────────
_required_settings = {
    "DEBUG": "false",
    "SECRET_KEY": "test-secret-key-not-for-production",
    "PORT": "8000",
    "DEV_POSTGRES_SERVER": "localhost",
    "DEV_POSTGRES_USER": "test",
    "DEV_POSTGRES_PASSWORD": "test",
    "DEV_POSTGRES_DB": "test",
    "DEV_POSTGRES_PORT": "5432",
    "POSTGRES_SERVER": "localhost",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "POSTGRES_DB": "test",
    "MONGO_HOST": "mongodb://localhost:27017",
    "MONGO_DB": "burncost_test",
    "REDIS_EMAIL": "",
    "REDIS_PASSWORD": "",
    "API_URL": "http://localhost:8000/api/v1",
    "FRONTEND_URL": "http://localhost:5173",
    "RESEND_API_KEY": "",
    "BREVO_API_KEY": "",
    "UPLOAD_DIR": "uploads",
    # Auth / provider keys referenced by settings (empty is fine for tests)
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "FLUTTERWAVE_SECRET_KEY": "",
    "FLUTTERWAVE_PUBLIC_KEY": "",
    "PAYSTACK_SECRET_KEY": "",
    "PAYSTACK_PUBLIC_KEY": "",
    "AI_SERVICE_API_KEY": "",
}
for _k, _v in _required_settings.items():
    os.environ.setdefault(_k, _v)