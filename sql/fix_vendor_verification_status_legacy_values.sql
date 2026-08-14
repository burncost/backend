-- =============================================================
-- BURNCOST — DATA FIX: normalize legacy uppercase enum values
--
-- The current vendor model (Backend/app/models/vendor.py) stores
-- verification_status in lowercase e.g. 'verified', 'pending'.
--
-- Rows seeded before the lowercase migration still hold legacy
-- uppercase values ('VERIFIED', 'PENDING', ...) inserted by the
-- original schema, which the ORM cannot map back to the Python
-- enum → LookupError on read (e.g. vendor login).
--
-- NOTE: this only applies to vendors.verification_status, which is
-- a plain VARCHAR. users.status is a native PostgreSQL enum
-- (user_status) that only accepts lowercase labels ('active',
-- 'suspended', 'pending_verification', 'deactivated'), so uppercase
-- values can never exist there and it requires no normalization.
--
-- Idempotent: only touches rows still holding uppercase values.
-- =============================================================

-- VENDORS.verification_status → lowercase (VARCHAR only)
UPDATE vendors
SET verification_status = lower(verification_status)
WHERE verification_status IN ('PENDING', 'VERIFIED', 'REJECTED', 'SUSPENDED', 'DEACTIVATED');