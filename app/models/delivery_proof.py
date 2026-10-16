"""Proof of delivery capture (Phase 1A of the driver & delivery feature).

A `DeliveryProof` is the evidential record captured when a delivery is completed
so an order is only treated as "delivered" with supporting proof. It is additive:
it lives entirely in its own table and does not alter `delivery_jobs`, `orders`
or any onboarding/order lifecycle columns already in use.

- `pod_type`: photo | signature | note  (what was captured)
- `file_url`: stored image URL (Cloudinary / local via StorageService)
- `note`: optional textual evidence (e.g. received by <name>, contact number)
- `captured_lat/lng`: optional geotag at the moment of capture
- `submitted_by`: driver | vendor | admin | buyer (who captured it)

Design constraint: at most ONE proof row per delivery job (unique `delivery_job_id`),
so re-capture updates the existing row instead of creating duplicates.
"""
from sqlalchemy import Column, String, Text, DateTime, Numeric, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DeliveryProof(Base):
    __tablename__ = "delivery_proofs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    pod_type = Column(String(20), default="photo", nullable=False)  # photo | signature | note
    file_url = Column(Text)  # stored image URL (photo / signature scan)
    note = Column(Text)

    # Optional geotag captured at the moment of proof.
    captured_lat = Column(Numeric(11, 8))
    captured_lng = Column(Numeric(11, 8))

    # Who captured the proof: driver | vendor | admin | buyer
    submitted_by = Column(String(20), default="driver", nullable=False)

    captured_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Geotag / freshness verification (additive, Phase 1A+) ──────────────
    # Extracted from the photo's EXIF block at capture; the gate + review
    # flag are computed from these so verification survives a page reload.
    exif_taken_at = Column(DateTime)                 # DateTimeOriginal (or GPS date)
    exif_lat = Column(Numeric(11, 8))                # signed decimal from GPS IFD
    exif_lng = Column(Numeric(11, 8))
    gps_source = Column(String(20))                  # exif | device | none
    geofence_distance_m = Column(Numeric(12, 2))     # distance to dropoff (metres)
    geofence_radius_m = Column(Numeric(12, 2))       # radius in force at capture
    within_geofence = Column(Boolean)                # None when it could not be judged
    is_live = Column(Boolean, default=False)         # photo age within the freshness window
    needs_review = Column(Boolean, default=False, nullable=False)
    # verified | unverified | out_of_range | awaiting_buyer | buyer_confirmed
    # (null for non-photo proof)
    verification_status = Column(String(20))
    verification_note = Column(Text)                 # human-readable reason for the verdict
    raw_exif = Column(JSONB)                         # compact audit blob

    # ── Human review of a flagged proof (additive) ─────────────────────────
    # `needs_review` marks the proof as queued; these record the reviewer's
    # outcome so "reviewed & accepted" is distinguishable from "reviewed &
    # rejected" (a single boolean cannot express both).
    review_status = Column(String(20))               # pending | cleared | rejected
    reviewed_by = Column(String(64))                 # reviewer user id
    reviewed_at = Column(DateTime)
    review_note = Column(String(500))                # reviewer's note

    # ── Buyer receipt confirmation (additive) ──────────────────────────────
    # A photo with no GPS cannot be geofence-verified, so instead of a stalemate
    # the buyer confirms receipt. `buyer_confirmation_required` is set by the
    # scorer; the endpoint only accepts the confirmation while the photo's
    # capture calendar-day matches the server's "today" (see
    # `settings.POD_BUYER_CONFIRM_TZ`). Re-capture clears the confirmation.
    buyer_confirmation_required = Column(Boolean, default=False, nullable=False)
    buyer_confirmed_at = Column(DateTime)
    buyer_confirmed_by = Column(UUID(as_uuid=True))  # confirming buyer's user id

    job = relationship("DeliveryJob")

    def __repr__(self) -> str:
        return f"<DeliveryProof id={self.id} job={self.delivery_job_id} type={self.pod_type}>"
