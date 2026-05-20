from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Response, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
import logging

from app.api.deps import get_current_user
from app.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.user import (
    PasswordUpdateRequest,
    UserCreate,
    UserLogin,
    TokenResponse,
    TokenRefresh,
    UserResponse
)
from app.crud import user as user_crud, vendor as vendor_crud
from app.services.auth_service import AuthService
from app.core.security import get_password_hash
from app.config import settings

import redis.asyncio as airedis

if settings.DEBUG:
    redis_client = airedis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        decode_responses=True
    )
else:
    redis_client=airedis.from_url(settings.REDIS_URL, ssl_cert_reqs=None, decode_responses=True)

router = APIRouter()
logger = logging.getLogger(__name__)

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
    
    existing_phone = await user_crud.get_by_phone(db, phone=user_in.phone_number)
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number already registered"
        )
    
    try:
        # Create user
        user = await user_crud.create(db, obj_in=user_in)
        
        if (user.role == "vendor"):
            rediread_dashboard = "/supplier-dashboard"
        else:
            rediread_dashboard = "/dashboard"

        # Send verification email in background
        auth_service = AuthService()
        background_tasks.add_task(
            auth_service.send_verification_email,
            email=user.email,
            # redirect_dashboard=rediread_dashboard
            # user_id=str(user.id)
        )
        
        logger.info(f"New user registered: {user.email}")
        return user
        
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
@router.post("/login", response_model=TokenResponse)
async def login(
    response: Response,
    credentials: UserLogin,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # Get user
    user = await user_crud.get_by_email(db, email=credentials.email)

    # logger.info(f"\nRedis check: {redis_client.ping()}\n")

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers="Token expired" if settings.DEBUG else "Could not validate credentials",
        )
    
    # Verify password
    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers="Token expired" if settings.DEBUG else "Could not validate credentials",
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
    
    # Only allow verified accounts to login
    if user.status == "pending_verification":
        auth_service = AuthService()
        background_tasks.add_task(
            auth_service.send_verification_email,
            email=user.email
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail":"Please check your inbox and verify your email to get started."}
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

    response_body = TokenResponse(
        access_token="",
        refresh_token="",
        expires_in=0,
        role=role,
        vendorverification=vendor_verified,
        name=user.profile.first_name if user.profile else "",
        business_name=user.profile.business_name if user.profile else ""
    )

    response = JSONResponse(content=response_body.dict())
    
    if settings.DEBUG == False:
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True,
            samesite="Strict",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="Strict",
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
    db: AsyncSession = Depends(get_db)
):
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
            samesite="Strict",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES*60,
            path="/"
        )

        response.set_cookie(
            key="refresh_token",
            value=new_refresh_token,
            httponly=True,
            secure=True,
            samesite="Strict",
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
    return {"status":"refreshed"}

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
    
    if (user.role == "vendor"):
        rediread_dashboard = "/supplier-dashboard"
    else:
        rediread_dashboard = "/dashboard"

    # background_tasks.add_task(
    #    verify_n_log, "9436936"        
    # )
    
    return {"message": "Verification email sent"}

##logout
@router.post("/logout")
async def logut(request: Request, response: Response):
    refresh_token = request.cookies.get("refresh_token")

    if refresh_token:
        try:
            payload = decode_token(refresh_token)
            user_id = payload["sub"]
            redis_client.delete(f"refresh:{user_id}")
        except:
            pass
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")

        return {"message": "Logged out."}
    

@router.post("/change-password")
async def update_password(
    payload: PasswordUpdateRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await user_crud.get(db, id=current_user["id"])

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # verify current password
    if not verify_password(payload.currentPassword, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect current password",
        )

    # hash new password
    new_hash = get_password_hash(payload.newPassword)

    # update DB
    user.password_hash = new_hash
    db.add(user)
    await db.commit()

    return {"message": "Password updated successfully"}