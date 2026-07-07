"""Auth Service - Delegates to CRUD and core security."""
from typing import Optional
import logging

from app.crud import user as user_crud
from app.core.security import create_access_token, create_refresh_token
from app.config import settings

logger = logging.getLogger(__name__)


class AuthService:
    """Service for user authentication and authorization."""

    async def verify_email(self, db, token: str) -> Optional[str]:
        """Verify a user's email using a verification token."""
        logger.info(f"Verifying email with token: {token}")
        try:
            from app.core.security import decode_token
            payload = decode_token(token)
            user_id = payload.get("sub")
            if not user_id:
                logger.warning("Verification failed: no sub in token")
                return None
            user = await user_crud.get(db, id=user_id)
            if not user:
                logger.warning(f"Verification failed: user not found for token {token}")
                return None
            if user.email_verified:
                logger.info(f"Email already verified for user {user.email}")
                return user.role
            user = await user_crud.verify_email(db, user_id=user_id)
            logger.info(f"Email verified for user {user.email}")
            return user.role
        except Exception as e:
            logger.error(f"Email verification failed: {str(e)}")
            return None

    async def google_oauth_login(self, code: str) -> Optional[dict]:
        """Authenticate with Google OAuth."""
        logger.info("Processing Google OAuth login")

        if not settings.GOOGLE_CLIENT_ID:
            logger.warning("Google OAuth not configured")
            return None

        try:
            import httpx

            # Exchange code for tokens
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "redirect_uri": f"{settings.FRONTEND_URL}/auth/callback",
                        "grant_type": "authorization_code",
                    }
                )

                if token_response.status_code != 200:
                    logger.error(f"Google token exchange failed: {token_response.text}")
                    return None

                tokens = token_response.json()
                access_token = tokens.get("access_token")

                # Get user info
                user_response = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"}
                )

                if user_response.status_code != 200:
                    logger.error(f"Google user info failed: {user_response.text}")
                    return None

                user_info = user_response.json()

                return {
                    "id": user_info.get("id"),
                    "email": user_info.get("email"),
                    "fullName": user_info.get("name"),
                    "avatarUrl": user_info.get("picture"),
                    "provider": "google",
                }

        except ImportError:
            logger.warning("httpx not installed for Google OAuth")
            return None
        except Exception as e:
            logger.error(f"Google OAuth failed: {str(e)}")
            return None
