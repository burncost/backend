from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class VendorDocument(Base):
    __tablename__ = "vendor_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    document_type = Column(String(50), nullable=False)  # 'cac', 'tin', 'utility_bill', etc.
    document_url = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    verified = Column(Boolean, default=False)
    # Tier this document supports: cac_only / documented / trusted
    tier = Column(String(20), default="cac_only")
    # Review state for the optional upgrade flow: pending / approved / rejected
    review_status = Column(String(20), default="pending")
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    vendor = relationship("Vendor", backref="documents")

    def __repr__(self):
        return f"<VendorDocument {self.document_type}>"
