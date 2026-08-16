-- =============================================================
-- BURNCOST — DEPLOYMENT FIX: repair invalid admin/vendor password hashes
--
-- PROBLEM
--   The seed users (3 admins + 20 vendor demo accounts) were inserted with a
--   bcrypt hash ('$2b$12$...') that this backend CANNOT validate, because
--   Backend/app/core/security.py uses pwdlib.PasswordHash.recommended()
--   which defaults to ARGON2 (not bcrypt). verify_password() therefore
--   rejects the bcrypt hash → those accounts cannot log in.
--
-- FIX
--   Replace the stored password_hash with a valid Argon2 hash for the same
--   default password 'Admin@123'. Generated with the app's own:
--     from app.core.security import get_password_hash
--     get_password_hash('Admin@123')
--
-- IDEMPOTENT
--   Only rows still holding the old invalid bcrypt value are updated, so
--   running this more than once is a safe no-op.
--
-- NOTE
--   Uses a plain UPDATE with single-quoted string literals (no $$ … $$
--   dollar-quoting), so the '$' characters inside the Argon2 hash are
--   treated as literal text and won't break the statement.
--
-- DEFAULT PASSWORD after this fix:  Admin@123  (change on first login)
-- =============================================================

UPDATE users
SET password_hash = '$argon2id$v=19$m=65536,t=3,p=4$FOnBm2BZHq/aJIXsgQXFkA$YJ4+kGptmzSQgFCGn2WuiR4yOnB2dGjAp0uINaa4j8o'
WHERE password_hash = '$2b$12$NOvAelP3Llez4cvww7gCr.5EABB/AoZZESt1RraTCeeE9bK0dG5Ra';