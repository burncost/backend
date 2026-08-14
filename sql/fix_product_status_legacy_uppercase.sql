-- Fix legacy uppercase product statuses to match the lowercase
-- ProductStatus enum values used by the SQLAlchemy model.
-- The 'productstatus' enum type only accepts lowercase values,
-- but legacy/seed data inserted 'ACTIVE' etc., causing
-- LookupError at read time.
UPDATE products
SET status = LOWER(status)
WHERE status IS NOT NULL
  AND status <> LOWER(status);