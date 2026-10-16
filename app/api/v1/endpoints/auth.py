from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Response, Request, Body, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
import json
import uuid
import logging

from app.api.deps import get_current_user
from app.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
)
from app.schemas.user import (
    PasswordUpdateRequest,
    UserCreate,
    UserLogin,
    TokenResponse,
    TokenRefresh,
    UserResponse,
    OAuthCompleteRequest,
    OAuthRegisterRequest,
)
from app.crud import user as user_crud, vendor as vendor_crud
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.token_service import TokenService
from app.services.payment_fraud_guard import (
    record_fraud_flag,
    record_failed_login,
    clear_failed_logins,
    is_login_locked,
)
from app.services import security_settings, two_factor

import redis.asyncio as airedis
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


def _redis_client_kwargs() -> dict:
    """
    Connection hardening for the auth Redis client.

    Without this redis-py happily reuses pooled sockets the server has already
    closed: on Windows that surfaces as `ConnectionResetError [WinError 10054]`,
    and the internal retry then pays the default 5s connect timeout. That is how
    `/auth/refresh` ended up 500-ing (or taking ~9s) on an otherwise healthy
    Redis, which logged users out with a raw 401 in the UI.

    * socket_keepalive + health_check_interval -> dead idle sockets are pinged
      out of the pool instead of being handed to a request.
    * short connect/read timeouts -> an unreachable Redis fails fast, so the
      endpoints below can answer a clean 503 instead of hanging.
    * one jittered retry on ConnectionError/TimeoutError -> a single blip is
      absorbed here rather than surfaced to the caller.
    """
    return dict(
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=3,
        socket_keepalive=True,
        health_check_interval=30,
        retry=Retry(ExponentialWithJitterBackoff(cap=1.0), retries=1),
        max_connections=20,
    )


if settings.DEBUG:
    redis_client = airedis.Redis(
        host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB,
        **_redis_client_kwargs(),
    )
else:
    redis_client = airedis.from_url(
        settings.REDIS_URL, ssl_cert_reqs=None, **_redis_client_kwargs()
    )

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Session helpers ─────────────────────────────────────────────────────────
# Login, 2FA verification and refresh all need to mint the same cookie pair, so
# the (previously duplicated) set_cookie blocks live here. `secure`/`samesite`
# follow the existing convention: None+Secure in production, Lax when DEBUG.

def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    secure = not settings.DEBUG
    samesite = "None" if secure else "Lax"
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite=samesite,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


def _tfa_claim_for(user, *, satisfied: bool) -> bool:
    """The `tfa` claim: has this session cleared the second-factor requirement?

    False for an admin-tier user while `security_2fa` is ON - the admin guard
    rejects those tokens, which closes the gap where an admin holding a session
    minted *before* the toggle was switched on would otherwise sail through.
    """
    if not security_settings.role_requires_2fa(getattr(user, "role", None)):
        return True
    return bool(satisfied)


async def _issue_session(response: Response, user, *, tfa_satisfied: bool = True) -> dict:
    """Mint + persist a session, set the cookies, and return the token pair.

    Shared by login, 2FA verification and enrolment so all three produce an
    identical session (including the `tfa` claim carried on BOTH tokens).
    """
    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    claims = {
        "sub": str(user.id),
        "role": role_str,
        "tfa": _tfa_claim_for(user, satisfied=tfa_satisfied),
    }
    access_token = create_access_token(data=claims)
    refresh_token = create_refresh_token(data=claims)

    # Store the refresh token in Redis for 7 days. A Redis outage answers 503
    # (retryable) rather than an unhandled 500 - the user can retry once the
    # store is reachable, and no partial session is issued.
    try:
        await redis_client.set(f"refresh:{user.id}", refresh_token, ex=60 * 60 * 24 * 7)
    except RedisError as e:
        logger.error("Failed to store refresh token (Redis unavailable): %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't start your session right now. Please try again in a moment.",
        )

    _set_auth_cookies(response, access_token, refresh_token)
    return {"access_token": access_token, "refresh_token": refresh_token, "role": role_str}


def _set_trust_cookie(response: Response, token: str) -> None:
    """Remember the current browser so 2FA is skipped until the trust expires."""
    secure = not settings.DEBUG
    response.set_cookie(
        key="2fa_trust",
        value=token,
        httponly=True,
        secure=secure,
        samesite="None" if secure else "Lax",
        max_age=security_settings.two_factor_remember_hours() * 3600,
        path="/",
    )


### OAuth Google (Phase 12)
@router.post("/google")
async def oauth_google(
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    code: str = Body(...),
    state: str = Body(default=None),
    role: str = Body(default=None),
    business_name: str = Body(default=None),
    redirect_uri: str = Body(default=None),
):
    """OAuth login with Google (auth code flow)."""
    auth_service = AuthService()
    user_info = await auth_service.google_oauth_login(code, redirect_uri=redirect_uri)
    if not user_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Google OAuth failed")

    # Brand-new account? Do NOT create it yet - ask the user for their role first.
    existing = await auth_service.oauth_lookup(
        db, oauth_id=user_info.get("id"), email=user_info.get("email")
    )
    if not existing:
        pending_token = str(uuid.uuid4())
        try:
            await redis_client.setex(
                f"oauth_pending:{pending_token}", 600, json.dumps(user_info)
            )
        except Exception:
            logger.error("Failed to stash pending OAuth signup in Redis", exc_info=True)
            raise HTTPException(status_code=500, detail="Could not start OAuth signup")
        return {
            "needs_role": True,
            "pending_token": pending_token,
            "email": user_info.get("email"),
            "provider": "google",
        }

    result = await auth_service.oauth_create_or_login(
        db, provider="google", oauth_id=user_info.get("id"),
        email=user_info.get("email"), full_name=user_info.get("fullName"),
        first_name=user_info.get("firstName"), last_name=user_info.get("lastName"),
        avatar_url=user_info.get("avatarUrl"), email_verified=bool(user_info.get("emailVerified")),
        role=role, business_name=business_name,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not create/log in OAuth user")

    # Send a welcome email for brand-new OAuth signups, encouraging profile completion.
    if result.get("is_new"):
        notification_service = NotificationService()
        background_tasks.add_task(
            notification_service.send_oauth_welcome_email,
            email=result["email"],
            full_name=user_info.get("fullName") or result["email"].split("@")[0],
            role=result.get("role", "customer"),
        )

    return await _oauth_response(response, result)


### OAuth Facebook (Phase 12)
@router.post("/facebook")
async def oauth_facebook(
    response: Response,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    code: str = Body(...),
    state: str = Body(default=None),
    role: str = Body(default=None),
    business_name: str = Body(default=None),
    redirect_uri: str = Body(default=None),
):
    """OAuth login with Facebook (auth code flow)."""
    auth_service = AuthService()
    user_info = await auth_service.facebook_oauth_login(code, redirect_uri=redirect_uri)
    if not user_info:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Facebook OAuth failed")

    # Brand-new account? Do NOT create it yet - ask the user for their role first.
    existing = await auth_service.oauth_lookup(
        db, oauth_id=user_info.get("id"), email=user_info.get("email")
    )
    if not existing:
        pending_token = str(uuid.uuid4())
        try:
            await redis_client.setex(
                f"oauth_pending:{pending_token}", 600, json.dumps(user_info)
            )
        except Exception:
            logger.error("Failed to stash pending OAuth signup in Redis", exc_info=True)
            raise HTTPException(status_code=500, detail="Could not start OAuth signup")
        return {
            "needs_role": True,
            "pending_token": pending_token,
            "email": user_info.get("email"),
            "provider": "facebook",
        }

    result = await auth_service.oauth_create_or_login(
        db, provider="facebook", oauth_id=user_info.get("id"),
        email=user_info.get("email"), full_name=user_info.get("fullName"),
        first_name=user_info.get("firstName"), last_name=user_info.get("lastName"),
        avatar_url=user_info.get("avatarUrl"), email_verified=bool(user_info.get("emailVerified")),
        role=role, business_name=business_name,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not create/log in OAuth user")

    # Send a welcome email for brand-new OAuth signups, encouraging profile completion.
    if result.get("is_new"):
        notification_service = NotificationService()
        background_tasks.add_task(
            notification_service.send_oauth_welcome_email,
            email=result["email"],
            full_name=user_info.get("fullName") or result["email"].split("@")[0],
            role=result.get("role", "customer"),
        )

    return await _oauth_response(response, result)


### Register a brand-new OAuth account after the user picks a role (pending-token flow)
@router.post("/oauth/register")
async def oauth_register(
    response: Response,
    payload: OAuthRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create the OAuth user (User + Profile + optional Vendor) using the verified
    provider data stashed in Redis under the one-time pending token, then issue JWT cookies."""
    auth_service = AuthService()

    pending_key = f"oauth_pending:{payload.pending_token}"
    try:
        raw = await redis_client.get(pending_key)
    except Exception:
        logger.error("Redis lookup failed for pending OAuth signup", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not complete OAuth signup")

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This signup session expired or was already used. Please sign in with Google again.",
        )
    await redis_client.delete(pending_key)  # single-use

    user_info = json.loads(raw)

    if payload.role not in ("customer", "vendor", "driver"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Choose 'customer', 'vendor' or 'driver'.",
        )

    result = await auth_service.oauth_create_or_login(
        db, provider=user_info.get("provider", "google"),
        oauth_id=user_info.get("id"),
        email=user_info.get("email"), full_name=user_info.get("fullName"),
        first_name=user_info.get("firstName"), last_name=user_info.get("lastName"),
        avatar_url=user_info.get("avatarUrl"), email_verified=bool(user_info.get("emailVerified")),
        role=payload.role, business_name=payload.business_name,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not create OAuth user")
    return await _oauth_response(response, result)


### One-time role/business-name completion for fresh OAuth accounts (Phase 13)
@router.post("/oauth/complete")
async def oauth_complete(
    response: Response,
    payload: OAuthCompleteRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a new OAuth user's role (+ optional business name) once, create the
    UserProfile and (for vendors) the Vendor row, then re-issue a JWT with the
    updated role."""
    from app.models.user import UserRole, UserProfile
    from app.models.vendor import Vendor

    if payload.role not in ("customer", "vendor", "driver"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Choose 'customer', 'vendor' or 'driver'.",
        )

    current_user.role = UserRole(payload.role)

    profile = current_user.profile
    if profile is None:
        profile = UserProfile(
            user_id=current_user.id,
            first_name=(current_user.email or "New").split("@")[0] or "New",
            last_name="User",
        )
        if payload.business_name:
            profile.business_name = payload.business_name
        db.add(profile)
    elif payload.business_name:
        profile.business_name = payload.business_name

    if payload.role == "vendor":
        existing_vendor = await vendor_crud.get_by_user_id(db, user_id=current_user.id)
        if not existing_vendor:
            db.add(Vendor(
                user_id=current_user.id,
                business_name=(payload.business_name or "").strip() or "My Business",
                business_type="general",
                city="",
                state="",
                business_address="",
                verification_status="verified",
                verification_tier="starter",
            ))

    if payload.role == "driver":
        from app.models.driver import DriverProfile
        from sqlalchemy import select
        existing_driver = await db.execute(
            select(DriverProfile).where(DriverProfile.user_id == current_user.id)
        )
        if not existing_driver.scalar_one_or_none():
            db.add(DriverProfile(
                user_id=current_user.id,
                vendor_id=None,
                source="self",
                status="active",
                full_name=((current_user.profile.first_name if current_user.profile else "") + " " + (current_user.profile.last_name if current_user.profile else "")).strip(),
                phone=None,
                availability=False,
            ))

    await db.commit()
    await db.refresh(current_user)

    # Re-issue tokens with the newly assigned role.
    access_token = create_access_token(data={"sub": str(current_user.id), "role": current_user.role})
    refresh_token = create_refresh_token(data={"sub": str(current_user.id)})

    # Persist the re-issued refresh token in Redis (mirrors login) so it passes
    # the /auth/refresh validation. Otherwise the cookie would hold a token Redis
    # doesn't know about and the session dies when the access token expires.
    await redis_client.set(
        f"refresh:{current_user.id}",
        refresh_token,
        ex=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    max_age = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    is_prod = settings.DEBUG == False
    response.set_cookie(
        key="access_token", value=access_token, httponly=True,
        secure=is_prod, samesite="None" if is_prod else "Lax",
        max_age=max_age, path="/",
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token, httponly=True,
        secure=is_prod, samesite="None" if is_prod else "Lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60, path="/",
    )

    return {
        "expires_in": max_age,
        "role": current_user.role.value,
        "business_name": (profile.business_name if profile else payload.business_name),
        "message": "Profile completed",
    }


async def _oauth_response(response: Response, result: dict):
    """Set JWT cookies and return the OAuth token payload."""
    access_token = result["access_token"]
    refresh_token = result["refresh_token"]
    # Persist the refresh token in Redis (mirrors login) so the silent-refresh
    # endpoint can validate the cookie. Without this, /auth/refresh 401s once the
    # access token expires and the client wrongly signs the user out.
    await redis_client.set(
        f"refresh:{result['user_id']}",
        refresh_token,
        ex=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    max_age = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    is_prod = settings.DEBUG == False
    response.set_cookie(
        key="access_token", value=access_token, httponly=True,
        secure=is_prod, samesite="None" if is_prod else "Lax",
        max_age=max_age, path="/",
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token, httponly=True,
        secure=is_prod, samesite="None" if is_prod else "Lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60, path="/",
    )
    return {
        "expires_in": max_age,
        "role": result.get("role"),
        "name": result.get("email"),
        "is_new": result.get("is_new"),
        "provider": result.get("provider"),
    }


### Register a new user
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: UserCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # Check if user already exists
    existing_user = await user_crud.get_by_email(db, email=user_in.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Strict role validation: only explicit, valid signup roles are accepted.
    # A missing/invalid role must never fall back to a default - reject before
    # anything is written to the database.
    allowed_roles = {"customer", "vendor", "driver"}
    if not user_in.role or user_in.role.lower() not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid role (customer, vendor or driver) is required"
        )
    
    existing_phone = None
    if user_in.phone_number:
        existing_phone = await user_crud.get_by_phone(db, phone=user_in.phone_number)
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered"
        )
    
    try:
        # Create user
        user = await user_crud.create(db, obj_in=user_in)

        # Auto-create a Vendor row for vendor-role signups so products can be added
        # immediately. Populate from signup data where available; the rest are
        # placeholders the user can update later via onboarding/settings.
        if user_in.role and user_in.role.lower() == "vendor":
            from app.models.vendor import Vendor
            db.add(Vendor(
                user_id=user.id,
                business_name=(user_in.business_name or (user.profile.business_name if user.profile else "") or "My Business").strip(),
                business_type="general",
                city=((user.profile.location if user.profile else "") or "").strip(),
                state="",
                business_address="",
                verification_status="pending",
                commission_rate=10.00,
            ))
        
        # Auto-create a DriverProfile for driver-role signups (self-onboarded).
        if user_in.role and user_in.role.lower() == "driver":
            from app.models.driver import DriverProfile
            _first = (user.profile.first_name if user.profile else "") or ""
            _last = (user.profile.last_name if user.profile else "") or ""
            db.add(DriverProfile(
                user_id=user.id,
                vendor_id=None,
                source="self",
                status="active",
                full_name=(" ".join([_first, _last]).strip() or user_in.email.split("@")[0]),
                phone=user_in.phone_number,
                availability=False,
            ))

        # Capture values as plain strings BEFORE any more commits
        # (grant_signup_tokens does 2 more commits which expires session state)
        user_email = user.email
        user_id = str(user.id)
        full_name = user.profile.first_name if user and user.profile else user_in.email.split('@')[0]
        user_role = user.role.value if user.role else "customer"
        
        # Grant free signup tokens (does 2 more commits)
        token_service = TokenService(db)
        await token_service.grant_signup_tokens(user_id)
        
        # Send welcome and verification emails in background
        notification_service = NotificationService()
        
        # Send welcome email (using pre-captured plain strings)
        background_tasks.add_task(
            notification_service.send_welcome_email,
            email=user_email,
            full_name=full_name,
            role=user_role
        )
        
        # Send verification email
        verification_token = create_access_token(
            data={"sub": user_id, "type": "email_verification"},
            expires_delta=timedelta(hours=48)
        )
        background_tasks.add_task(
            notification_service.send_verification_email,
            email=user_email,
            verification_token=verification_token,
            full_name=full_name
        )
        
        logger.info(f"New user registered: {user_email}")
        return user
        
    except ValueError as e:
        # Strict role validation failure (from crud) - nothing was written.
        logger.error(f"Registration rejected: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    except IntegrityError as e:
        logger.error(f"Database integrity error during registration: {str(e)}")
        await db.rollback()  # Explicit rollback
        
        # Check what constraint was violated
        error_str = str(e.orig).lower()
        if 'email' in error_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        elif 'phone' in error_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phone number already registered"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this information already exists"
            )

### Login user and return JWT tokens
@router.post("/login")
async def login(
    request: Request,
    response: Response,
    credentials: UserLogin,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"

    # Phase 11 (ATO protection): lockout after repeated failed logins.
    # Admin → Settings → Security → "Login Attempt Limit" controls the threshold
    # and window; the check is skipped entirely when an admin turns it off.
    if security_settings.login_attempt_limit_enabled() and await is_login_locked(
        credentials.email,
        client_ip,
        max_failures=security_settings.login_attempt_threshold(),
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again in a few minutes.",
        )

    # Get user
    user = await user_crud.get_by_email(db, email=credentials.email)

    # logger.info(f"\nRedis check: {redis_client.ping()}\n")

    if not user:
        # Phase 11: count failure and flag after repeated attempts (ATO).
        await record_failed_login(
            db, credentials.email, client_ip,
            window_seconds=security_settings.login_attempt_window_minutes() * 60,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # OAuth-only accounts have no password_hash. Return a clear, actionable
    # message instead of crashing on a None hash (which surfaced as an opaque
    # 500 to the client). Not counted as a failed attempt - no credential was
    # actually checked.
    if not user.password_hash:
        provider = (user.auth_provider or "").lower()
        if provider == "google":
            detail = "This account was created with Google. Please use 'Continue with Google' to sign in."
        elif provider == "facebook":
            detail = "This account was created with Facebook. Please use 'Continue with Facebook' to sign in."
        else:
            detail = "This account uses social sign-in. Please use the sign-in method you originally used."
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
        )

    # Verify password
    if not verify_password(credentials.password, user.password_hash):
        # Phase 11: count failure and flag after repeated attempts (ATO).
        await record_failed_login(
            db, credentials.email, client_ip,
            window_seconds=security_settings.login_attempt_window_minutes() * 60,
        )
        await record_fraud_flag(
            db,
            alert_type="Account Takeover",
            description=f"Suspected brute-force on account {credentials.email} from {client_ip}.",
            user_id=str(user.id),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    # Check if user is active
    if user.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended. Please contact support."
        )
    
    if user.status == "deactivated":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Please contact support to reactivate."
        )
    
    vendor = await vendor_crud.get_by_user_id(db, user_id=user.id)

    vendor_verified = vendor.verification_status if vendor else "n.a"

    role = user.role;
    role_str = role.value if hasattr(role, "value") else str(role)
    display_name = user.profile.first_name if user.profile else ""

    # ── Two-factor authentication gate (Admin → Settings → Security) ────────
    # An admin-tier account that has not completed a factor never gets a session
    # here - it receives a short-lived challenge token instead, so the only
    # screens it can reach are the enrolment/verification endpoints.
    if security_settings.role_requires_2fa(role):
        trusted_cookie = request.cookies.get("2fa_trust")
        trusted = bool(trusted_cookie) and await two_factor.is_trusted(str(user.id), trusted_cookie)

        if not user.two_factor_method:
            # Forced enrolment (no factor yet). No auth cookies are set.
            return JSONResponse(content={
                "two_factor_required": True,
                "enrolled": False,
                "challenge_token": two_factor.new_challenge_token(str(user.id), "enrol"),
                "default_method": security_settings.two_factor_default_method(),
                "remember_hours": security_settings.two_factor_remember_hours(),
                "role": role_str,
                "name": display_name,
            })

        if not trusted:
            # Enrolled, but this browser is not (or no longer) trusted.
            return JSONResponse(content={
                "two_factor_required": True,
                "enrolled": True,
                "method": user.two_factor_method,
                "challenge_token": two_factor.new_challenge_token(str(user.id), "verify"),
                "default_method": security_settings.two_factor_default_method(),
                "remember_hours": security_settings.two_factor_remember_hours(),
                "role": role_str,
                "name": display_name,
            })

    # Password verified and (when required) the factor is satisfied. The cookies
    # are attached to the injected Response and merged into the returned body.
    await _issue_session(response, user, tfa_satisfied=True)

    # Update last login
    await user_crud.update_last_login(db, user_id=user.id)

    # Phase 11: clear failed-login counters after a successful login.
    await clear_failed_logins(credentials.email, client_ip)

    # Start the idle clock for "Session Timeout" enforcement.
    if security_settings.session_timeout_enabled():
        await security_settings.touch_session(str(user.id))

    logger.info(f"User logged in: {user.email}")

    return {
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": role_str,
        "vendor_verification": vendor_verified,
        "name": display_name,
        "business_name": user.profile.business_name if user.profile else "",
        "two_factor_required": False,
        # The Admin UI surfaces a banner when the password has aged out; the
        # admin guard blocks console writes with 403 code="password_expired".
        "password_expired": security_settings.is_password_expired(user),
    }

### refresh access tokens
# @router.post("/refresh", response_model=TokenResponse)
# async def refresh_token(
#     token_data: TokenRefresh,
#     db: AsyncSession = Depends(get_db)
# ):
#     try:
#         payload = decode_token(token_data.refresh_token)
#     except Exception:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid or expired refresh token",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
    
#     if payload.get("type") != "refresh":
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid token type",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
    
#     user_id = payload.get("sub")
#     user = await user_crud.get(db, id=user_id)
    
#     if not user:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="User not found",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
    
#     # Create new tokens
#     access_token = create_access_token(
#         data={"sub": str(user.id), "role": user.role}
#     )
#     new_refresh_token = create_refresh_token(
#         data={"sub": str(user.id)}
#     )
    
#     return TokenResponse(
#         access_token=access_token,
#         refresh_token=new_refresh_token,
#         expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
#     )

# @router.post("/refresh", response_model=TokenResponse)
@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    token_data: TokenRefresh = Body(default=None)
):
    # Accept refresh token from (highest→lowest priority): Bearer header, JSON body, or cookie.
    # This fixes cross-origin flows (dev frontend :5173 → API :8000) where Lax/secure=False
    # cookies are blocked by browsers. The token is always also returned in the login body.
    refresh_token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        refresh_token = auth_header[7:]
    if not refresh_token and token_data and token_data.refresh_token:
        refresh_token = token_data.refresh_token
    if not refresh_token:
        refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = decode_token(refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )
    
    user_id = payload.get("sub")

    # Verify against Redis. Redis being unreachable is a *transient* server
    # problem, NOT a dead session: answering 503 (never 401) keeps the client's
    # refresh_token cookie intact so the next request can recover. A 500 here
    # used to bubble up as a raw error and bounce users out mid-session.
    try:
        stored = await redis_client.get(f"refresh:{user_id}")
    except RedisError as e:
        logger.error("Refresh-token lookup failed (Redis unavailable): %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't verify your session just now. Please try again.",
        )

    if stored != refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )
    
    user = await user_crud.get(db, id=user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Create new tokens. The `tfa` claim MUST be carried across: dropping it would
    # let a session that never cleared 2FA be refreshed into one that looks
    # verified (and vice versa, bouncing admins out of every refreshed session).
    tfa_claim = _tfa_claim_for(user, satisfied=bool(payload.get("tfa", False)))
    claims = {
        "sub": str(user.id),
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "tfa": tfa_claim,
    }
    access_token = create_access_token(data=claims)

    new_refresh_token = create_refresh_token(data=claims)

    # Store rotated refresh token. Same reasoning as the lookup above: a Redis
    # outage must not turn a valid session into a 500 - the old refresh token is
    # still the one presented next time, so the client can simply retry.
    try:
        await redis_client.set(
            f"refresh:{user.id}",
            new_refresh_token,
            ex=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
        )
    except RedisError as e:
        logger.error("Failed to rotate refresh token (Redis unavailable): %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't verify your session just now. Please try again.",
        )

    #Set new cookies
    _set_auth_cookies(response, access_token, new_refresh_token)

    # Tokens are set as HTTP-only cookies above (never exposed to JS).
    # Only non-sensitive info is returned in the body.
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return {
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": role,
        "password_expired": security_settings.is_password_expired(user),
    }

### Verify user email with token
@router.get("/verify-email")
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db)
):
    auth_service = AuthService()
    success = await auth_service.verify_email(db, token=token)
    # logger.info(f"\n\n\n{success}\n\n")
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )
    
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/login")
    
    ## use this later
    # if success == "customer":
    #     return RedirectResponse(
    #         url=f"{settings.FRONTEND_URL}/dashboard/marketplace"
    #     )
    # else:
    #     return RedirectResponse(
    #         url=f"{settings.FRONTEND_URL}/supplier-onboarding"
    #     )

# async def verify_n_log(rc_number: str):
#     result = await verify_business(rc_number)
#     logger.info(f"\n\nVerification details: {result}\n\n")

### Resend email verification
@router.post("/resend-verification")
async def resend_verification(
    email: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    user = await user_crud.get_by_email(db, email=email)
    
    # Don't reveal if email exists or not (security)
    if not user:
        return {"message": "If the email exists, verification link has been sent"}
    
    if user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified"
        )
    
    # Send verification email
    notification_service = NotificationService()
    full_name = user.profile.first_name if user and user.profile else email.split('@')[0]
    verification_token = create_access_token(
        data={"sub": str(user.id), "type": "email_verification"},
        expires_delta=timedelta(hours=48)
    )
    background_tasks.add_task(
        notification_service.send_verification_email,
        email=user.email,
        verification_token=verification_token,
        full_name=full_name
    )
    
    return {"message": "Verification email sent"}

##logout
@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    token_data: TokenRefresh = Body(default=None)
):
    # Accept refresh token from (highest→lowest priority): Bearer header, JSON body, or cookie.
    # The Admin app stores tokens in localStorage and sends the refresh token via the
    # Bearer header, while cookie-based clients (customer Frontend) rely on the cookie.
    refresh_token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        refresh_token = auth_header[7:]
    if not refresh_token and token_data and token_data.refresh_token:
        refresh_token = token_data.refresh_token
    if not refresh_token:
        refresh_token = request.cookies.get("refresh_token")

    if refresh_token:
        try:
            payload = decode_token(refresh_token)
            user_id = payload["sub"]
            await redis_client.delete(f"refresh:{user_id}")
        except:
            pass

    # Always clear auth cookies on logout, even without a valid refresh token,
    # so no stale httpOnly crumbs survive the session. Mirror the Set-Cookie
    # attributes (secure / samesite) used when the cookies were created so the
    # browser reliably deletes them - especially SameSite=None in production.
    is_prod = settings.DEBUG == False
    response.delete_cookie(
        "access_token", path="/", secure=is_prod, httponly=True,
        samesite="None" if is_prod else "Lax",
    )
    response.delete_cookie(
        "refresh_token", path="/", secure=is_prod, httponly=True,
        samesite="None" if is_prod else "Lax",
    )

    if refresh_token:
        return {"message": "Logged out."}
    return {"message": "No active session found. Cookies cleared."}

### Forgot password - send reset link
@router.post("/forgot-password")
async def forgot_password(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email: str = Query(...),
):
    user = await user_crud.get_by_email(db, email=email)

    # Don't reveal if email exists or not (security)
    if not user:
        return {"message": "If the email exists, a password reset link has been sent"}

    # Generate password reset token
    reset_token = create_access_token(
        data={"sub": str(user.id), "type": "password_reset"},
        expires_delta=timedelta(minutes=30)  # 30 minutes
    )

    # Send password reset email
    full_name = user.profile.first_name if user and user.profile else user.email.split('@')[0]
    background_tasks.add_task(
        NotificationService().send_password_reset_email,
        email=user.email,
        reset_token=reset_token,
        full_name=full_name
    )

    logger.info(f"Password reset requested for: {user.email}")

    return {"message": "If the email exists, a password reset link has been sent"}


### Reset password with token
@router.post("/reset-password")
async def reset_password(
    token: str,
    new_password: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )

    if payload.get("type") != "password_reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token type"
        )

    user_id = payload.get("sub")
    user = await user_crud.get(db, id=user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Update password via CRUD
    await user_crud.update_password(db, user_id=user_id, new_password=new_password)

    # Send password changed confirmation email
    full_name = user.profile.first_name if user and user.profile else user.email.split('@')[0]
    background_tasks.add_task(
        NotificationService().send_email,
        to=user.email,
        subject="Your Burncost password was changed",
        html_content=f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
<div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
<div style="text-align:center;margin-bottom:20px;">
    <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
</div>
<h2 style="color:#FF6B00;">Password Changed Successfully</h2>
<p>Hi {full_name},</p>
<p>Your Burncost account password was just changed.</p>
<p>If you made this change, you can ignore this email. If you did not, please contact our support team immediately.</p>
<p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
</div></body></html>"""
    )

    logger.info(f"Password reset completed for user {user_id}")
    return {"message": "Password has been reset successfully"}


@router.get("/password-policy")
async def get_password_policy():
    """Active password rules, so the clients can show the same hints the API enforces."""
    return security_settings.password_policy_view()


@router.post("/change-password")
async def update_password(
    payload: PasswordUpdateRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # verify current password
    if not verify_password(payload.currentPassword, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect current password",
        )

    # Admin → Settings → Security → "Password Complexity" (no-op when OFF).
    security_settings.enforce_password_policy(payload.newPassword)

    # update password via CRUD (also stamps password_changed_at)
    await user_crud.update_password(db, user_id=current_user.id, new_password=payload.newPassword)

    # A password change invalidates every "remember this device" 2FA trust.
    await two_factor.revoke_all(str(current_user.id))

    return {"message": "Password updated successfully"}


# ── Two-factor authentication (Admin → Settings → Security → Two-Factor) ─────
# Two supported factors, chosen per admin at enrolment:
#   email  - a 6-digit code emailed to the account (proves the channel works)
#   totp   - an authenticator app, provisioned from a server-rendered QR code
# Enrolment is mandatory while `security_2fa` is ON: login withholds all session
# cookies and hands back a short-lived challenge token instead.

PENDING_TFA_TTL_SECONDS = 15 * 60


async def _stash_pending(user_id: str, data: dict) -> None:
    try:
        await redis_client.set(f"2fa_pending:{user_id}", json.dumps(data), ex=PENDING_TFA_TTL_SECONDS)
    except RedisError as e:
        logger.error("Failed to stash pending 2FA enrolment: %s", e)
        raise HTTPException(503, "We couldn't start enrolment right now. Please try again.")


async def _read_pending(user_id: str) -> dict:
    try:
        raw = await redis_client.get(f"2fa_pending:{user_id}")
    except RedisError:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


async def _clear_pending(user_id: str) -> None:
    try:
        await redis_client.delete(f"2fa_pending:{user_id}")
    except RedisError:
        pass


async def _resolve_challenge_user(challenge_token: str, purpose: str, db: AsyncSession):
    """Map a challenge token back to its user, or raise 400."""
    uid = two_factor.decode_challenge_token(challenge_token or "", purpose)
    if not uid:
        raise HTTPException(400, "Invalid or expired challenge token. Please sign in again.")
    user = await user_crud.get(db, id=uid)
    if not user:
        raise HTTPException(404, "User not found")
    return user


async def _send_otp_email(user, code: str) -> bool:
    """Email a one-time code. Returns False when no transport accepted it."""
    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
    <div style="max-width:600px;margin:auto;background:white;border-radius:8px;padding:30px;">
    <div style="text-align:center;margin-bottom:20px;">
        <img src="https://res.cloudinary.com/ddwdbu4tf/image/upload/v1775528651/e3b31e077ad9310acc512868f1f8d64384f40417_tddsgp.png" alt="Burncost" style="height:40px;" />
    </div>
    <h2 style="color:#FF6B00;">Your sign-in code</h2>
    <p>Use this code to finish signing in to the Burncost admin console:</p>
    <p style="font-size:32px;font-weight:bold;letter-spacing:6px;margin:20px 0;">{code}</p>
    <p>This code expires in 10 minutes. If you did not try to sign in, change your password immediately.</p>
    <p style="color:#888;font-size:12px;">© 2026 Burncost. All rights reserved.</p>
    </div></body></html>"""
    try:
        return await NotificationService().send_email(
            to=user.email, subject="Your Burncost sign-in code", html_content=html
        )
    except Exception as e:  # pragma: no cover - transport failure
        logger.error("Failed to send 2FA OTP email: %s", e)
        return False


@router.get("/2fa/status")
async def two_factor_status(current_user=Depends(get_current_user)):
    """This admin's 2FA state, for the Security card in Settings."""
    return security_settings.two_factor_status(current_user)


@router.get("/security-state")
async def auth_security_state(current_user=Depends(get_current_user)):
    """Per-session security flags the Admin UI needs (banner + idle timer).

    A single cheap call so the console can warn about an expiring password and
    mirror the configured idle timeout without hitting every endpoint.
    """
    idle = security_settings.session_idle_minutes() if security_settings.session_timeout_enabled() else None
    return {
        "password_expired": security_settings.is_password_expired(current_user),
        "two_factor": security_settings.two_factor_status(current_user),
        "session_idle_minutes": idle,
    }


@router.post("/2fa/enroll/start")
async def two_factor_enroll_start(
    challenge_token: str = Body(...),
    method: str = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Begin enrolment for an admin who has no factor yet (post-login challenge)."""
    user = await _resolve_challenge_user(challenge_token, "enrol", db)
    method = (method or "").strip().lower()
    if method not in ("email", "totp"):
        raise HTTPException(400, "method must be 'email' or 'totp'")

    if method == "email":
        if not _email_provider_configured():
            raise HTTPException(400, "Email OTP is unavailable: no email provider is configured.")
        code = await two_factor.issue_email_otp(str(user.id))
        if not code:
            raise HTTPException(503, "We couldn't send a code right now. Please try again.")
        if not await _send_otp_email(user, code):
            raise HTTPException(503, "We couldn't send a code right now. Please try again.")
        await _stash_pending(str(user.id), {"method": "email"})
        return {"method": "email", "sent": True, "target": _mask_email(user.email)}

    secret = two_factor.generate_totp_secret()
    uri = two_factor.provisioning_uri(secret, user.email)
    await _stash_pending(str(user.id), {"method": "totp", "secret": secret})
    return {
        "method": "totp",
        "secret": secret,
        "otpauth_uri": uri,
        "qr_data_uri": two_factor.qr_data_uri(uri),
    }


def _email_provider_configured() -> bool:
    return bool(getattr(settings, "BREVO_API_KEY", None) or getattr(settings, "SMTP_HOST", None))


def _mask_email(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return email or ""
    visible = name[:2]
    return f"{visible}{'*' * max(1, len(name) - 2)}@{domain}"


@router.post("/2fa/enroll/confirm")
async def two_factor_enroll_confirm(
    response: Response,
    challenge_token: str = Body(...),
    code: str = Body(...),
    remember_me: bool = Body(False),
    db: AsyncSession = Depends(get_db),
):
    """Complete first-time enrolment and hand back the session that login withheld."""
    user = await _resolve_challenge_user(challenge_token, "enrol", db)
    pending = await _read_pending(str(user.id))
    method = pending.get("method")
    if method not in ("email", "totp"):
        raise HTTPException(400, "Your enrolment session expired. Please sign in again.")

    recovery_codes = None
    if method == "email":
        if not await two_factor.verify_email_otp(str(user.id), code):
            raise HTTPException(400, "Incorrect or expired code")
        # Switching to email clears any previously stored app seed/codes.
        user.totp_secret = None
        user.totp_recovery_codes = None
    else:
        secret = pending.get("secret")
        if not two_factor.verify_totp(secret, code):
            raise HTTPException(400, "Incorrect code. Check your authenticator app and try again.")
        recovery_codes = two_factor.generate_recovery_codes()
        user.totp_secret = two_factor.encrypt_secret(secret)
        user.totp_recovery_codes = two_factor.hash_recovery_codes(recovery_codes)

    user.two_factor_method = method
    user.two_factor_enabled_at = datetime.utcnow()
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _clear_pending(str(user.id))
    await two_factor.revoke_all(str(user.id))

    await _issue_session(response, user, tfa_satisfied=True)
    if remember_me:
        token = await two_factor.create_trust(
            str(user.id), security_settings.two_factor_remember_hours(), method=method,
        )
        if token:
            _set_trust_cookie(response, token)

    await user_crud.update_last_login(db, user_id=user.id)
    if security_settings.session_timeout_enabled():
        await security_settings.touch_session(str(user.id))

    logger.info(f"2FA enrolled ({method}) for {user.email}")
    return {
        "two_factor_method": method,
        "recovery_codes": recovery_codes,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
    }


@router.post("/2fa/verify")
async def two_factor_verify(
    response: Response,
    request: Request,
    challenge_token: str = Body(...),
    code: str = Body(...),
    remember_me: bool = Body(False),
    db: AsyncSession = Depends(get_db),
):
    """Finish a login by verifying the factor (an app code or a recovery code)."""
    user = await _resolve_challenge_user(challenge_token, "verify", db)
    method = user.two_factor_method
    if not method:
        raise HTTPException(400, "This account has no second factor. Please sign in again.")

    ok = False
    used_recovery = False
    if method == "email":
        ok = await two_factor.verify_email_otp(str(user.id), code)
    else:
        ok = two_factor.verify_totp(two_factor.decrypt_secret(user.totp_secret), code)
        if not ok:
            # Fall back to a single-use recovery code.
            ok, remaining = two_factor.verify_and_consume_recovery_code(user.totp_recovery_codes, code)
            if ok:
                user.totp_recovery_codes = remaining
                db.add(user)
                await db.commit()
                used_recovery = True

    if not ok:
        raise HTTPException(401, "Invalid or expired code")

    await _issue_session(response, user, tfa_satisfied=True)
    if remember_me:
        token = await two_factor.create_trust(
            str(user.id), security_settings.two_factor_remember_hours(),
            method=method,
            user_agent=request.headers.get("user-agent", ""),
            ip=request.client.host if request.client else "",
        )
        if token:
            _set_trust_cookie(response, token)

    await user_crud.update_last_login(db, user_id=user.id)
    await clear_failed_logins(user.email, request.client.host if request.client else "")
    if security_settings.session_timeout_enabled():
        await security_settings.touch_session(str(user.id))

    logger.info(f"2FA verified ({method}{' + recovery code' if used_recovery else ''}) for {user.email}")
    return {
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "two_factor_method": method,
        "used_recovery_code": used_recovery,
    }


@router.post("/2fa/resend")
async def two_factor_resend(
    challenge_token: str = Body(...),
    db: AsyncSession = Depends(get_db),
):
    """Re-send the email OTP (enrolment or verification challenge)."""
    user = None
    for purpose in ("verify", "enrol"):
        user = await _resolve_challenge_user(challenge_token, purpose, db)
        if user:
            break
    if user is None:  # pragma: no cover - _resolve_challenge_user always raises
        raise HTTPException(400, "Invalid or expired challenge token. Please sign in again.")

    pending = await _read_pending(str(user.id))
    method = user.two_factor_method or pending.get("method")
    if method != "email":
        raise HTTPException(400, "This account does not use email codes.")

    code = await two_factor.issue_email_otp(str(user.id))
    if not code or not await _send_otp_email(user, code):
        raise HTTPException(503, "We couldn't send a code right now. Please try again.")
    return {"sent": True, "target": _mask_email(user.email)}


@router.post("/2fa/method/start")
async def two_factor_method_start(
    method: str = Body(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start switching an already-enrolled admin to a different factor.

    The switch only completes once a code for the **new** method verifies, so an
    admin can never lock themselves out by switching to something that fails.
    """
    method = (method or "").strip().lower()
    if method not in ("email", "totp"):
        raise HTTPException(400, "method must be 'email' or 'totp'")

    if method == "email":
        if not _email_provider_configured():
            raise HTTPException(400, "Email OTP is unavailable: no email provider is configured.")
        code = await two_factor.issue_email_otp(str(current_user.id))
        if not code or not await _send_otp_email(current_user, code):
            raise HTTPException(503, "We couldn't send a code right now. Please try again.")
        await _stash_pending(str(current_user.id), {"method": "email"})
        return {"method": "email", "sent": True, "target": _mask_email(current_user.email)}

    secret = two_factor.generate_totp_secret()
    uri = two_factor.provisioning_uri(secret, current_user.email)
    await _stash_pending(str(current_user.id), {"method": "totp", "secret": secret})
    return {
        "method": "totp",
        "secret": secret,
        "otpauth_uri": uri,
        "qr_data_uri": two_factor.qr_data_uri(uri),
    }


@router.post("/2fa/method/confirm")
async def two_factor_method_confirm(
    code: str = Body(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Finish a method switch after verifying a code for the new method."""
    user = await user_crud.get(db, id=current_user.id)
    pending = await _read_pending(str(user.id))
    method = pending.get("method")
    if method not in ("email", "totp"):
        raise HTTPException(400, "Start the switch again - your session expired.")

    recovery_codes = None
    if method == "email":
        if not await two_factor.verify_email_otp(str(user.id), code):
            raise HTTPException(400, "Incorrect or expired code")
        user.totp_secret = None
        user.totp_recovery_codes = None
    else:
        secret = pending.get("secret")
        if not two_factor.verify_totp(secret, code):
            raise HTTPException(400, "Incorrect code. Check your authenticator app and try again.")
        recovery_codes = two_factor.generate_recovery_codes()
        user.totp_secret = two_factor.encrypt_secret(secret)
        user.totp_recovery_codes = two_factor.hash_recovery_codes(recovery_codes)

    user.two_factor_method = method
    user.two_factor_enabled_at = datetime.utcnow()
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _clear_pending(str(user.id))
    # Every remembered device was trusted for the OLD factor - drop them all.
    await two_factor.revoke_all(str(user.id))

    logger.info(f"2FA method switched to {method} for {user.email}")
    return {"two_factor_method": method, "recovery_codes": recovery_codes}


@router.post("/2fa/disable")
async def two_factor_disable(
    password: str = Body(...),
    code: str = Body(default=""),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Turn off 2FA for this account (re-enrolment is forced again if the policy is ON)."""
    if not verify_password(password, current_user.password_hash):
        raise HTTPException(401, "Incorrect password")

    if current_user.two_factor_method == "totp":
        # Require a live code too, so a hijacked session alone cannot disable it.
        ok = two_factor.verify_totp(two_factor.decrypt_secret(current_user.totp_secret), code)
        if not ok:
            ok, _ = two_factor.verify_and_consume_recovery_code(current_user.totp_recovery_codes, code)
        if not ok:
            raise HTTPException(400, "Enter a valid authenticator or recovery code to disable 2FA.")

    user = await user_crud.get(db, id=current_user.id)
    user.two_factor_method = None
    user.two_factor_enabled_at = None
    user.totp_secret = None
    user.totp_recovery_codes = None
    db.add(user)
    await db.commit()
    await two_factor.revoke_all(str(user.id))

    logger.info(f"2FA disabled for {user.email}")
    return {
        "two_factor_method": None,
        "required": security_settings.role_requires_2fa(user.role),
    }


@router.post("/2fa/recovery/regenerate")
async def two_factor_recovery_regenerate(
    password: str = Body(...),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a fresh set of single-use recovery codes (authenticator-app method only)."""
    if not verify_password(password, current_user.password_hash):
        raise HTTPException(401, "Incorrect password")
    if current_user.two_factor_method != "totp":
        raise HTTPException(400, "Recovery codes are only available with the authenticator app.")

    codes = two_factor.generate_recovery_codes()
    user = await user_crud.get(db, id=current_user.id)
    user.totp_recovery_codes = two_factor.hash_recovery_codes(codes)
    db.add(user)
    await db.commit()

    return {"recovery_codes": codes}


@router.get("/2fa/devices")
async def two_factor_devices(current_user=Depends(get_current_user)):
    """Browsers currently remembered for this account (self-expiring)."""
    devices = await two_factor.list_devices(str(current_user.id))
    return {"devices": devices, "remember_hours": security_settings.two_factor_remember_hours()}


@router.delete("/2fa/devices/{device_id}")
async def two_factor_revoke_device(device_id: str, current_user=Depends(get_current_user)):
    """Forget one remembered device, so the next login asks for a code again."""
    revoked = await two_factor.revoke_device(str(current_user.id), device_id)
    if not revoked:
        raise HTTPException(404, "That remembered device was not found or already expired.")
    return {"message": "Device forgotten"}



### Driver activation from a vendor invite
@router.post("/driver/activate")
async def driver_activate(
    token: str = Body(...),
    phone: str = Body(...),
    password: str = Body(...),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import select
    from app.models.user import User
    from app.models.driver import DriverProfile

    def _digits(v: str) -> str:
        return "".join(ch for ch in (v or "") if ch.isdigit())[-10:]

    data = decode_token(token)
    if data.get("type") != "driver_invite":
        raise HTTPException(status_code=400, detail="Invalid activation token")
    if _digits(str(data.get("phone") or "")) != _digits(phone):
        raise HTTPException(status_code=400, detail="Phone does not match this invite")
    uid = data.get("sub")
    if not uid:
        raise HTTPException(status_code=400, detail="Invalid activation token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(str(uid))))
    user = result.scalar_one_or_none()
    if not user or (user.role and user.role.value != "driver"):
        raise HTTPException(status_code=400, detail="Invalid activation token")

    user.password_hash = get_password_hash(password)
    user.status = "active"
    user.phone_verified = True

    dres = await db.execute(select(DriverProfile).where(DriverProfile.user_id == user.id))
    driver = dres.scalar_one_or_none()
    if driver:
        driver.status = "active"
        driver.phone = phone
        driver.availability = False

    await db.commit()
    return {"message": "Driver activated. You can now sign in.", "role": "driver"}