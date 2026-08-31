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

import redis.asyncio as airedis

if settings.DEBUG:
    redis_client = airedis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB, decode_responses=True)
    # redis_client=airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
else:
    redis_client=airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)

router = APIRouter()
logger = logging.getLogger(__name__)


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

    # Brand-new account? Do NOT create it yet — ask the user for their role first.
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

    return _oauth_response(response, result)


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

    # Brand-new account? Do NOT create it yet — ask the user for their role first.
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

    return _oauth_response(response, result)


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

    if payload.role not in ("customer", "vendor"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Choose 'customer' or 'vendor'.",
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
    return _oauth_response(response, result)


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

    if payload.role not in ("customer", "vendor"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Choose 'customer' or 'vendor'.",
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
                verification_status="pending",
                verification_tier="cac_only",
            ))

    await db.commit()
    await db.refresh(current_user)

    # Re-issue tokens with the newly assigned role.
    access_token = create_access_token(data={"sub": str(current_user.id), "role": current_user.role})
    refresh_token = create_refresh_token(data={"sub": str(current_user.id)})

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


def _oauth_response(response: Response, result: dict):
    """Set JWT cookies and return the OAuth token payload."""
    access_token = result["access_token"]
    refresh_token = result["refresh_token"]
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
    # A missing/invalid role must never fall back to a default — reject before
    # anything is written to the database.
    allowed_roles = {"customer", "vendor"}
    if not user_in.role or user_in.role.lower() not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid role (customer or vendor) is required"
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
        # Strict role validation failure (from crud) — nothing was written.
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
    if await is_login_locked(credentials.email, client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again in a few minutes.",
        )

    # Get user
    user = await user_crud.get_by_email(db, email=credentials.email)

    # logger.info(f"\nRedis check: {redis_client.ping()}\n")

    if not user:
        # Phase 11: count failure and flag after repeated attempts (ATO).
        await record_failed_login(db, credentials.email, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    # Verify password
    if not verify_password(credentials.password, user.password_hash):
        # Phase 11: count failure and flag after repeated attempts (ATO).
        await record_failed_login(db, credentials.email, client_ip)
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

    # Create tokens
    access_token = create_access_token(
        data={"sub": str(user.id), "role": role}
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id)}
    )

    # Store refresh tokens in redis for7 days
    await redis_client.set(
        f"refresh:{user.id}",
        refresh_token,
        ex=60*60*24*7
    )

    # Tokens are set as HTTP-only cookies below (never exposed to JS).
    # Only non-sensitive info is returned in the body.
    role_str = role.value if hasattr(role, "value") else str(role)
    response_body = {
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": role_str,
        "vendor_verification": vendor_verified,
        "name": user.profile.first_name if user.profile else "",
        "business_name": user.profile.business_name if user.profile else ""
    }

    response = JSONResponse(content=response_body)
    
    if settings.DEBUG == False:
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True,
            samesite="None",
            # domain=".burncost.com", #return this when in production
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="None",
            # domain=".burncost.com", #return this when in production
            max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )
    else:
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,
            samesite="Lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=False,
            samesite="Lax",
            max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

    # Update last login
    await user_crud.update_last_login(db, user_id=user.id)

    # Phase 11: clear failed-login counters after a successful login.
    await clear_failed_logins(credentials.email, client_ip)
    
    logger.info(f"User logged in: {user.email}")

    return response

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

    # Verify against Redis
    stored = await redis_client.get(f"refresh:{user_id}")

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

    # Create new tokens
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role}
    )

    new_refresh_token = create_refresh_token(
        data={"sub": str(user.id)}
    )

    # Store rotated refresh token
    await redis_client.set(
        f"refresh:{user.id}",
        new_refresh_token,
        ex=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    )

    #Set new cookies
    if settings.DEBUG == False:
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True,
            samesite="None",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,
            samesite="None",
            max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )
    else:
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,
            samesite="Lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=False,
            samesite="Lax",
            max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

    # Tokens are set as HTTP-only cookies above (never exposed to JS).
    # Only non-sensitive info is returned in the body.
    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return {
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "role": role,
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
    # browser reliably deletes them — especially SameSite=None in production.
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

    # update password via CRUD
    await user_crud.update_password(db, user_id=current_user.id, new_password=payload.newPassword)

    return {"message": "Password updated successfully"}
