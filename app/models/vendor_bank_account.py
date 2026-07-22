from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class VendorBankAccount(Base):
    __tablename__ = "vendor_bank_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    bank_name = Column(String(100), nullable=False)
    account_number = Column(String(20), nullable=False)
    account_name = Column(String(255), nullable=False)
    bank_code = Column(String(10))
    is_primary = Column(Boolean, default=False)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="bank_accounts")

    def __repr__(self):
        return f"<VendorBankAccount {self.bank_name} - {self.account_number}>"
