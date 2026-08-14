-- =============================================================
-- LEGACY ENUM NORMALIZATION MIGRATION
-- =============================================================
-- Normalizes historical uppercase enum values to the lowercase
-- values expected by the SQLAlchemy models. Without this, reading
-- such rows raises:
--   LookupError: 'X' is not among the defined enum values
-- Applies to all VARCHAR-backed (non-native) enum columns.

-- products.status
UPDATE products SET status = LOWER(status)
WHERE status IS NOT NULL AND status <> LOWER(status);

-- orders.status
UPDATE orders SET status = LOWER(status)
WHERE status IS NOT NULL AND status <> LOWER(status);

-- orders.payment_status
UPDATE orders SET payment_status = LOWER(payment_status)
WHERE payment_status IS NOT NULL AND payment_status <> LOWER(payment_status);

-- orders.payment_method
UPDATE orders SET payment_method = LOWER(payment_method)
WHERE payment_method IS NOT NULL AND payment_method <> LOWER(payment_method);

-- order_items.vendor_status
UPDATE order_items SET vendor_status = LOWER(vendor_status)
WHERE vendor_status IS NOT NULL AND vendor_status <> LOWER(vendor_status);

-- vendors.verification_status
UPDATE vendors SET verification_status = LOWER(verification_status)
WHERE verification_status IS NOT NULL AND verification_status <> LOWER(verification_status);

-- customer_addresses.address_type
UPDATE customer_addresses SET address_type = LOWER(address_type)
WHERE address_type IS NOT NULL AND address_type <> LOWER(address_type);

-- material_rates.trend
UPDATE material_rates SET trend = LOWER(trend)
WHERE trend IS NOT NULL AND trend <> LOWER(trend);

-- NOTE: users.role, users.status, token_transactions.transaction_type
-- are native PG ENUM columns (Postgres rejects bad casing at insert),
-- so no normalization is needed for them.