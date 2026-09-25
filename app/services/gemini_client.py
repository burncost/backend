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
    """Return a singleton Gemini client."""
    global _client
    if _client is not None:
        return _client

    project = settings.GOOGLE_PROJECT_ID
    location = settings.GOOGLE_LOCATION

    # Resolve the credentials path relative to the Backend project root (not the
    # process CWD) so local dev works regardless of where the server is launched.
    creds_path = settings.GOOGLE_CREDS_PATH or "google_creds.json"
    if not os.path.isabs(creds_path):
        creds_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), creds_path
        )

    try:
        if os.path.exists(creds_path):
            # Local development: explicit service-account key
            credentials = (
                service_account.Credentials.from_service_account_file(
                    creds_path
                ).with_scopes(
                    ["https://www.googleapis.com/auth/cloud-platform"]
                )
            )

            _client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
                credentials=credentials,
            )

            logger.info(
                "Gemini client initialized using local service account (%s).",
                creds_path,
            )

        else:
            # Cloud Run / GCP - Uses ADC automatically
            _client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )

            logger.info("Gemini client initialized using ADC.")

    except Exception:
        logger.exception("Failed to initialize Gemini client.")
        raise

    return _client