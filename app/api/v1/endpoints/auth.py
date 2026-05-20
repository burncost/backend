from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
import logging

from app.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.user import (
    UserCreate,
    UserLogin,
    TokenResponse,
    TokenRefresh,
    UserResponse
)
from app.crud import user as user_crud, vendor as vendor_crud
from app.services.auth_service import AuthService
from app.config import settings

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
            redirect_dashboard=rediread_dashboard
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
    credentials: UserLogin,
    db: AsyncSession = Depends(get_db)
):
    # Get user
    user = await user_crud.get_by_email(db, email=credentials.email)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    if not verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
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
    
    # Allow login even if pending verification (optional - you can restrict this)
    if user.status == "pending_verification":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in."
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
    
    # Update last login
    await user_crud.update_last_login(db, user_id=user.id)
    
    logger.info(f"User logged in: {user.email}")
    
    tokens = TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        role=role,
        vendorverification=vendor_verified,
        name=user.profile.first_name if user.profile else "",
        business_name=user.profile.business_name if user.profile else ""
    )

    return tokens

### refresh access tokens
@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    token_data: TokenRefresh,
    db: AsyncSession = Depends(get_db)
):
    try:
        payload = decode_token(token_data.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    user = await user_crud.get(db, id=user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create new tokens
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role}
    )
    new_refresh_token = create_refresh_token(
        data={"sub": str(user.id)}
    )
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )

### Verify user email with token
@router.get("/verify-email")
async def verify_email(
    token: str,
    redirect: str = "/",
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
    
    if (user.role == "vendor"):
        rediread_dashboard = "/supplier-dashboard"
    else:
        rediread_dashboard = "/dashboard"

    # Send verification email in background
    auth_service = AuthService()
    background_tasks.add_task(
        auth_service.send_verification_email,
        email=user.email,
        redirect_dashboard=rediread_dashboard
        # user_id=str(user.id)
    )
    
    return {"message": "Verification email sent"}