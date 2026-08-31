"""Auth Service - Delegates to CRUD, core security, and OAuth providers."""
from typing import Optional, Dict, Any
import logging
from datetime import datetime

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

    # ── OAuth Google ─────────────────────────────────────────────────────────

    async def google_oauth_login(self, code: str, redirect_uri: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Exchange Google OAuth code and return (create-or-login, JWT) user."""
        logger.info("Processing Google OAuth login")
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            logger.warning("Google OAuth not configured")
            return None

        try:
            import httpx

            # Phase 12: state validation is handled by the calling endpoint —
            # verify the authorization code at Google and never trust client input.
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "code": code,
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET,
                        "redirect_uri": redirect_uri or f"{settings.FRONTEND_URL}/auth/callback",
                        "grant_type": "authorization_code",
                    },
                )
                if token_response.status_code != 200:
                    logger.error(f"Google token exchange failed: {token_response.text}")
                    return None

                tokens = token_response.json()
                access_token = tokens.get("access_token")

                # Phase 12: fetch user info from Google's endpoint (server-side).
                user_response = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if user_response.status_code != 200:
                    logger.error(f"Google user info failed: {user_response.text}")
                    return None

                user_info = user_response.json()

                return {
                    "id": user_info.get("sub"),  # stable Google account id
                    "email": user_info.get("email"),
                    "fullName": user_info.get("name"),
                    "firstName": user_info.get("given_name"),
                    "lastName": user_info.get("family_name"),
                    "avatarUrl": user_info.get("picture"),
                    "emailVerified": user_info.get("email_verified"),
                    "provider": "google",
                }

        except ImportError:
            logger.warning("httpx not installed for Google OAuth")
            return None
        except Exception as e:
            logger.error(f"Google OAuth failed: {str(e)}")
            return None

    # ── OAuth Facebook ───────────────────────────────────────────────────────

    async def facebook_oauth_login(self, code: str, redirect_uri: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Exchange Facebook OAuth code and return (create-or-login, JWT) user."""
        logger.info("Processing Facebook OAuth login")
        if not settings.FACEBOOK_CLIENT_ID or not settings.FACEBOOK_CLIENT_SECRET:
            logger.warning("Facebook OAuth not configured")
            return None

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                # Exchange code for a short-lived access token.
                token_response = await client.get(
                    "https://graph.facebook.com/v19.0/oauth/access_token",
                    params={
                        "client_id": settings.FACEBOOK_CLIENT_ID,
                        "client_secret": settings.FACEBOOK_CLIENT_SECRET,
                        "redirect_uri": redirect_uri or f"{settings.FRONTEND_URL}/auth/callback/facebook",
                        "code": code,
                    },
                )
                if token_response.status_code != 200:
                    logger.error(f"Facebook token exchange failed: {token_response.text}")
                    return None

                tokens = token_response.json()
                access_token = tokens.get("access_token")

                # Phase 12: server-side validation using appsecret_proof (sent by
                # the gateway already; we rely on the code exchange result only).
                user_response = await client.get(
                    "https://graph.facebook.com/v19.0/me",
                    params={
                        "fields": "id,name,email,first_name,last_name,picture.type(large)",
                        "access_token": access_token,
                    },
                )
                if user_response.status_code != 200:
                    logger.error(f"Facebook user info failed: {user_response.text}")
                    return None

                user_info = user_response.json()

                pic = (user_info.get("picture") or {}).get("data") or {}
                return {
                    "id": str(user_info.get("id")),
                    "email": user_info.get("email"),
                    "fullName": user_info.get("name"),
                    "firstName": user_info.get("first_name"),
                    "lastName": user_info.get("last_name"),
                    "avatarUrl": pic.get("url"),
                    "emailVerified": True,
                    "provider": "facebook",
                }

        except ImportError:
            logger.warning("httpx not installed for Facebook OAuth")
            return None
        except Exception as e:
            logger.error(f"Facebook OAuth failed: {str(e)}")
            return None

    # ── Create-or-login + JWT ────────────────────────────────────────────────

    async def oauth_create_or_login(
        self, db, *, provider: str, oauth_id: str, email: str, full_name: str,
        first_name: str, last_name: str, avatar_url: str, email_verified: bool,
        role: str = None, business_name: str = None,
    ) -> Optional[Dict[str, Any]]:
        """Create or log in a user from verified provider data; returns JWT payload.

        Handles: new signup (auto-create User + UserProfile + TokenUsage, optional
        Vendor row when role='vendor'), existing-email link (connects provider to the
        existing account + backfills empty profile fields), and returning OAuth login
        (sets provider fields + issues JWT). Every biodata field the provider returns
        (oauth_id, email, full name, avatar, email_verified) is persisted so no blank
        profiles come from OAuth signups.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.models.user import User, UserProfile, UserRole
        from app.services.token_service import TokenService

        email = (email or "").strip().lower()
        if not email:
            logger.error(
                f"OAuth create/login failed: provider returned no email "
                f"(provider={provider}, oauth_id={oauth_id})"
            )
            return None

        # 1. Existing user by oauth_id (already linked provider).
        user = None
        created = False
        if oauth_id:
            result = await db.execute(
                select(User).options(selectinload(User.profile)).where(User.oauth_id == oauth_id)
            )
            user = result.scalar_one_or_none()

        # 2. Existing user by email — link the provider if not already.
        if not user and email:
            result = await db.execute(
                select(User).options(selectinload(User.profile)).where(User.email == email)
            )
            existing = result.scalar_one_or_none()
            if existing:
                user = existing
                if oauth_id:
                    user.oauth_id = oauth_id
                    user.auth_provider = provider
                    user.avatar_url = avatar_url or user.avatar_url
                    # Google OAuth emails are provider-verified - always mark them verified.
                    if provider == "google":
                        user.email_verified = True
                    else:
                        user.email_verified = user.email_verified or bool(email_verified)
                # Backfill any empty profile fields from provider data.
                if user.profile is None:
                    db.add(UserProfile(
                        user_id=user.id,
                        first_name=(first_name or full_name or email.split("@")[0]).strip() or "New",
                        last_name=(last_name or "").strip() or "User",
                        avatar_url=avatar_url,
                    ))
                else:
                    if not user.profile.first_name or user.profile.first_name in ("", "New"):
                        user.profile.first_name = (first_name or full_name or email.split("@")[0]).strip() or "New"
                    if not user.profile.last_name or user.profile.last_name in ("", "User"):
                        user.profile.last_name = (last_name or "").strip() or "User"
                    if avatar_url and not user.profile.avatar_url:
                        user.profile.avatar_url = avatar_url

        # 3. Brand-new OAuth signup (no phone/password required).
        # Strict: the client must send a valid role explicitly — no default is
        # ever written. A missing/invalid role aborts the whole signup.
        new_signup = user is None
        if new_signup:
            if not role:
                logger.error(
                    f"OAuth create/login failed: no role provided by client for "
                    f"new signup (email={email}, provider={provider})"
                )
                return None
            try:
                new_role = UserRole(role)
            except ValueError:
                logger.error(
                    f"OAuth create/login failed: invalid role {role!r} for "
                    f"new signup (email={email}, provider={provider})"
                )
                return None
            user = User(
                email=email,
                phone_number=None,
                password_hash=None,
                auth_provider=provider,
                oauth_id=oauth_id,
                avatar_url=avatar_url,
                # Google OAuth emails are provider-verified - mark verified immediately.
                email_verified=True if provider == "google" else bool(email_verified),
                status="active",
                role=new_role,
                last_login=datetime.utcnow(),
            )
        try:
            if new_signup:
                db.add(user)
                await db.flush()

                # Auto-create profile (first_name/last_name are non-nullable).
                first = (first_name or full_name or email.split("@")[0]).strip() or "New"
                last = (last_name or "").strip() or "User"
                profile = UserProfile(
                    user_id=user.id, first_name=first, last_name=last, avatar_url=avatar_url,
                    business_name=business_name,
                )
                db.add(profile)

                # Fresh OAuth vendor signup gets a Vendor row immediately (cac_only tier).
                if role == "vendor":
                    from app.models.vendor import Vendor
                    db.add(Vendor(
                        user_id=user.id,
                        business_name=(business_name or "").strip() or "My Business",
                        business_type="general",
                        city="",
                        state="",
                        business_address="",
                        verification_status="pending",
                        verification_tier="cac_only",
                    ))

                # Auto-create token usage / grant signup bonus.
                token_service = TokenService(db)
                await token_service.grant_signup_tokens(str(user.id))
                created = True
            else:
                # Update last_login for an existing/returning user.
                user.last_login = datetime.utcnow()

            await db.commit()
            await db.refresh(user)
        except Exception as e:
            await db.rollback()
            logger.error(
                f"OAuth create/login failed during DB write/commit: {e!r} "
                f"(provider={provider}, email={email}, role={role!r}, oauth_id={oauth_id})",
                exc_info=True,
            )
            return None

        # 4. Issue JWT (mirrors the email-login token flow).
        access_token = create_access_token(data={"sub": str(user.id), "role": user.role})
        refresh_token = create_refresh_token(data={"sub": str(user.id)})

        return {
            "user_id": str(user.id),
            "email": user.email,
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "provider": provider,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "is_new": created,
        }