"""Shared Gemini client — initialised once with service-account credentials."""
from __future__ import annotations

import os
import logging
from typing import Optional

from google import genai
from google.oauth2 import service_account

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def get_gemini_client() -> genai.Client:
    """Return a singleton Gemini enterprise client."""
    global _client
    if _client is not None:
        return _client

    creds_path = settings.GOOGLE_CREDS_PATH or os.getenv("GOOGLE_CREDS_PATH", "google_creds.json")
    project = settings.GOOGLE_PROJECT_ID or os.getenv("GOOGLE_PROJECT_ID", "burncost-493208")
    location = settings.GOOGLE_LOCATION or os.getenv("GOOGLE_LOCATION", "us-central1")

    if not os.path.exists(creds_path):
        logger.warning("Google credentials file not found at %s — Gemini calls will fail", creds_path)
        _client = genai.Client(project=project, location=location)
        return _client

    try:
        credentials = service_account.Credentials.from_service_account_file(
            creds_path
        ).with_scopes(["https://www.googleapis.com/auth/cloud-platform"])

        _client = genai.Client(
            enterprise=True,
            project=project,
            location=location,
            credentials=credentials,
        )
        logger.info("Gemini enterprise client initialised (project=%s, location=%s)", project, location)
    except Exception as exc:
        logger.error("Failed to initialise Gemini client: %s", exc)
        _client = genai.Client(project=project, location=location)

    return _client
