from sqlalchemy import Column, String, Boolean, DateTime, Enum as SQLEnum, ForeignKey, Date, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from app.core.database import Base


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    MANAGER = "manager"
    SUPPORT = "support"
    MARKETING = "marketing"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_VERIFICATION = "pending_verification"
    DEACTIVATED = "deactivated"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone_number = Column(String(20), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    # OAuth fields (Phase 12)
    auth_provider = Column(String(20), default="email", index=True)  # email | google | facebook
    oauth_id = Column(String(255), unique=True, nullable=True, index=True)
    avatar_url = Column(Text, nullable=True)

    role = Column(
        SQLEnum(
            UserRole,
            name="user_role",
            values_callable=lambda enum: [e.value for e in enum],
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=UserRole.CUSTOMER,
    )

    status = Column(
        SQLEnum(
            UserStatus,
            name="user_status",
            values_callable=lambda enum: [e.value for e in enum],
            native_enum=True,
            create_type=True,
        ),
        nullable=False,
        default=UserStatus.PENDING_VERIFICATION,
    )
    email_verified = Column(Boolean, default=False)
    phone_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    # Relationships
    profile = relationship(
        "UserProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    vendor = relationship(
        "Vendor",
        back_populates="user",
        uselist=False,
        foreign_keys="Vendor.user_id",
        cascade="all, delete-orphan"
    )
    cart_items = relationship(
        "CartItem",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    orders = relationship(
        "Order",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    addresses = relationship(
        "CustomerAddress",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    reviews = relationship(
        "Review",
        back_populates="user",
        foreign_keys="Review.user_id",
        cascade="all, delete-orphan"
    )
    vendors_verified = relationship(
        "Vendor",
        back_populates="verifier",
        foreign_keys="Vendor.verified_by"
    )

    def __repr__(self):
        return f"<User {self.email}>"


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )
    first_name = Column(String(100), nullable=False)
    other_name = Column(String(100))
    last_name = Column(String(100), nullable=False)
    business_name = Column(String(255))
    location = Column(String(100), nullable=True)
    avatar_url = Column(Text)
    date_of_birth = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="profile")

    def __repr__(self):
        return f"<UserProfile {self.first_name} {self.last_name}>"