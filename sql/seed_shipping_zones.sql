-- =============================================================
-- BURNCOST — SEED SHIPPING ZONES + MAPPINGS
-- Required for production shipping calculation
-- (shipping_service.py resolves zones by code 'metro' / 'national'
--  and queries shipping_zone_mappings for inter-state pairs)
--
-- Idempotent: safe to re-run. Mappings resolve zone_id by CODE
-- (never hardcoded UUIDs), so they always satisfy the FK even if
-- a zone row was pre-created via the ORM with a random UUID.
-- Wrapped in a single transaction.
-- =============================================================
BEGIN;

-- =============================================================
-- 1. SHIPPING ZONES
-- =============================================================
INSERT INTO shipping_zones (id, name, code, base_rate, rate_per_kg, free_weight_kg, handling_fee, estimated_days_min, estimated_days_max, is_active, created_at, updated_at)
SELECT * FROM (VALUES
  ('30000000-0000-0000-0000-000000000001'::uuid, 'Metro (Same State)',   'metro',    5000.00, 100.00, 10, 500.00,  1, 2, true, NOW(), NOW()),
  ('30000000-0000-0000-0000-000000000002'::uuid, 'National (Inter-state)','national', 10000.00, 75.00, 10, 1000.00, 3, 5, true, NOW(), NOW()),
  ('30000000-0000-0000-0000-000000000003'::uuid, 'Regional (Adjacent)',  'regional', 7500.00, 85.00, 10, 750.00,  2, 4, true, NOW(), NOW())
) AS z(id, name, code, base_rate, rate_per_kg, free_weight_kg, handling_fee, estimated_days_min, estimated_days_max, is_active, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM shipping_zones x WHERE x.code = z.code);

-- =============================================================
-- 2. SHIPPING ZONE MAPPINGS
--    origin_state -> destination_state -> zone
--    zone_id resolved from shipping_zones by CODE.
--    Seeded with the common marketplace routes (FCT / Lagos hubs).
-- =============================================================
INSERT INTO shipping_zone_mappings (id, origin_state, origin_city, destination_state, destination_city, zone_id)
SELECT m.id, m.origin_state, m.origin_city, m.destination_state, m.destination_city, z.id
FROM (VALUES
  -- Same-state (metro) — FCT & Lagos
  ('31000000-0000-0000-0000-000000000001'::uuid, 'FCT',   NULL, 'FCT',   NULL, 'metro'),
  ('31000000-0000-0000-0000-000000000002'::uuid, 'Lagos', NULL, 'Lagos', NULL, 'metro'),
  -- More same-state (common market states)
  ('31000000-0000-0000-0000-000000000003'::uuid, 'Oyo',   NULL, 'Oyo',   NULL, 'metro'),
  ('31000000-0000-0000-0000-000000000004'::uuid, 'Ogun',  NULL, 'Ogun',  NULL, 'metro'),
  ('31000000-0000-0000-0000-000000000005'::uuid, 'Kaduna',NULL, 'Kaduna',NULL, 'metro'),
  ('31000000-0000-0000-0000-000000000006'::uuid, 'Rivers',NULL, 'Rivers',NULL, 'metro'),
  -- FCT (Abuja) outbound — adjacent (regional)
  ('31000000-0000-0000-0000-000000000011'::uuid, 'FCT', NULL, 'Niger',   NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000012'::uuid, 'FCT', NULL, 'Kaduna',  NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000013'::uuid, 'FCT', NULL, 'Plateau', NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000014'::uuid, 'FCT', NULL, 'Kogi',    NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000015'::uuid, 'FCT', NULL, 'Nasarawa',NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000016'::uuid, 'FCT', NULL, 'Benue',   NULL, 'regional'),
  -- Lagos outbound — adjacent (regional)
  ('31000000-0000-0000-0000-000000000021'::uuid, 'Lagos', NULL, 'Ogun', NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000022'::uuid, 'Lagos', NULL, 'Oyo',  NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000023'::uuid, 'Lagos', NULL, 'Osun', NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000024'::uuid, 'Lagos', NULL, 'Ondo', NULL, 'regional'),
  ('31000000-0000-0000-0000-000000000025'::uuid, 'Lagos', NULL, 'Ekiti',NULL, 'regional'),
  -- Inter-region (long haul) — national
  ('31000000-0000-0000-0000-000000000031'::uuid, 'Lagos', NULL, 'FCT',   NULL, 'national'),
  ('31000000-0000-0000-0000-000000000032'::uuid, 'FCT',   NULL, 'Lagos', NULL, 'national'),
  ('31000000-0000-0000-0000-000000000033'::uuid, 'Lagos', NULL, 'Rivers',NULL, 'national'),
  ('31000000-0000-0000-0000-000000000034'::uuid, 'Rivers',NULL, 'Lagos', NULL, 'national'),
  ('31000000-0000-0000-0000-000000000035'::uuid, 'Lagos', NULL, 'Enugu', NULL, 'national'),
  ('31000000-0000-0000-0000-000000000036'::uuid, 'Enugu', NULL, 'Lagos', NULL, 'national'),
  ('31000000-0000-0000-0000-000000000037'::uuid, 'Kaduna',NULL, 'FCT',   NULL, 'national'),
  ('31000000-0000-0000-0000-000000000038'::uuid, 'FCT',   NULL, 'Kano',  NULL, 'national'),
  ('31000000-0000-0000-0000-000000000039'::uuid, 'Kano',  NULL, 'Lagos', NULL, 'national'),
  ('31000000-0000-0000-0000-000000000040'::uuid, 'Rivers',NULL, 'Enugu', NULL, 'national')
) AS m(id, origin_state, origin_city, destination_state, destination_city, zone_code)
JOIN shipping_zones z ON z.code = m.zone_code
WHERE NOT EXISTS (
  SELECT 1 FROM shipping_zone_mappings x
  WHERE x.origin_state = m.origin_state
    AND x.destination_state = m.destination_state
    AND x.zone_id = z.id
);

COMMIT;