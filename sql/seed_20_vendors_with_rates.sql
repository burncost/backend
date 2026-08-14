-- =============================================================
-- BURNCOST — SEED 20 VENDORS + PRODUCTS + MATERIAL RATES
-- Real markets only (Dei-Dei, Zone 5, Nyanya, Orile, Ojota,
-- Abule Egba, Mushin, Abule Ado, Ikeja)
--
-- Requires: categories already seeded (see seed_categories.sql:
-- parent IDs a0000001..a0000020).
--
-- Idempotent: safe to re-run (WHERE NOT EXISTS guards + unique
-- SKUs/slugs). Wrapped in a single transaction.
-- =============================================================
BEGIN;

-- =============================================================
-- 1. USERS (vendor accounts)
-- =============================================================
INSERT INTO users (id, email, phone_number, password_hash, role, status)
SELECT * FROM (VALUES
  ('20000000-0000-0000-0000-000000000001'::uuid, 'deidei.cement.steel@burncost.test', '08010000001', 'not-a-real-hash', 'vendor'::user_role, 'active'::user_status),
  ('20000000-0000-0000-0000-000000000002', 'deidei.timber@burncost.test',         '08010000002', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000003', 'deidei.blocks@burncost.test',         '08010000003', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000004', 'deidei.tanks.plumbing@burncost.test','08010000004', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000005', 'deidei.roofing@burncost.test',        '08010000005', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000006', 'deidei.hardware@burncost.test',       '08010000006', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000007', 'zone5.plumbing@burncost.test',        '08010000007', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000008', 'zone5.sanitary@burncost.test',        '08010000008', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000009', 'nyanya.electrical@burncost.test',     '08010000009', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000010', 'nyanya.paints@burncost.test',         '08010000010', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000011', 'nyanya.tiles@burncost.test',          '08010000011', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000012', 'nyanya.equipment@burncost.test',      '08010000012', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000013', 'orile.tiles.sanitary@burncost.test',  '08010000013', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000014', 'orile.doors@burncost.test',           '08010000014', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000015', 'ojota.cement@burncost.test',          '08010000015', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000016', 'abuleegba.roofing@burncost.test',     '08010000016', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000017', 'abuleegba.paints@burncost.test',      '08010000017', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000018', 'mushin.electrical@burncost.test',     '08010000018', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000019', 'tradefair.masonry@burncost.test',     '08010000019', 'not-a-real-hash', 'vendor', 'active'),
  ('20000000-0000-0000-0000-000000000020', 'ikeja.solar@burncost.test',           '08010000020', 'not-a-real-hash', 'vendor', 'active')
) AS v(id, email, phone_number, password_hash, role, status)
WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = v.id::uuid);

-- =============================================================
-- 2. VENDORS
-- =============================================================
INSERT INTO vendors (id, user_id, business_name, business_type, city, state, business_address, cac_business_registration_number, tax_identification_number, verification_status, commission_rate, rating, total_reviews, total_sales, is_featured, delivery_time, response_time, specializations, created_at, updated_at)
SELECT * FROM (VALUES
  ('21000000-0000-0000-0000-000000000001'::uuid, '20000000-0000-0000-0000-000000000001'::uuid, 'Dei-Dei Cement & Steel Depot', 'Cement & Steel Dealer', 'Abuja', 'FCT', 'Dei-Dei Main Hub, Murtala Mohammed Expressway, Zuba-Kubwa Road', 'RC-10000001', 'TIN100000001', 'verified', 2.50, 4.6, 212, 18650000.00, true, '1-3 Days', '< 1 hour', '{cement,reinforcement-steel,hardware}'::text[], NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000002', 'Dei-Dei Timber Shed', 'Timber Merchant', 'Abuja', 'FCT', 'Dei-Dei Main Hub, timber shed section, Murtala Mohammed Expressway', 'RC-10000002', 'TIN100000002', 'verified', 4.00, 4.5, 168, 14820000.00, false, '1-3 Days', '< 1 hour', '{timber-engineered-wood}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000003', 'Dei-Dei Blocks & Bricks', 'Block Manufacturer', 'Abuja', 'FCT', 'Dei-Dei Main Hub, Murtala Mohammed Expressway', 'RC-10000003', 'TIN100000003', 'verified', 3.00, 4.4, 134, 11230000.00, false, '1-3 Days', '< 1 hour', '{masonry-products,burnt-bricks}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000004', 'Dei-Dei GP Tanks & Plumbing', 'Plumbing & Tanks Dealer', 'Abuja', 'FCT', 'Dei-Dei Main Hub, Murtala Mohammed Expressway', 'RC-10000004', 'TIN100000004', 'verified', 4.50, 4.3, 96, 8790000.00, false, '1-3 Days', '< 1 hour', '{plumbing-systems,sanitary-ware}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000005', 'Dei-Dei Roofing Mart', 'Roofing Materials Dealer', 'Abuja', 'FCT', 'Dei-Dei Main Hub, Murtala Mohammed Expressway', 'RC-10000005', 'TIN100000005', 'verified', 5.00, 4.5, 145, 13150000.00, true, '1-3 Days', '< 1 hour', '{roofing-systems,doors-windows-facades}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000006', '20000000-0000-0000-0000-000000000006', 'Dei-Dei Hardware & Tools', 'Hardware Supplier', 'Abuja', 'FCT', 'Dei-Dei Main Hub, shops before main market, Murtala Mohammed Expressway', 'RC-10000006', 'TIN100000006', 'verified', 6.00, 4.4, 110, 7640000.00, false, '1-3 Days', '< 1 hour', '{tools-consumables}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000007', 'Zone 5 Plumbing Warehouse', 'Plumbing Fittings Specialist', 'Abuja', 'FCT', 'Zone 5 Building Materials Market, Michael Okpara Street, Wuse 5, opposite Shippers Plaza', 'RC-10000007', 'TIN100000007', 'verified', 4.50, 4.2, 78, 6420000.00, false, '1-3 Days', '< 1 hour', '{plumbing-systems}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000008', '20000000-0000-0000-0000-000000000008', 'Zone 5 Sanitary & Fittings', 'Sanitary Ware Dealer', 'Abuja', 'FCT', 'Zone 5 Building Materials Market, Michael Okpara Street, Wuse 5', 'RC-10000008', 'TIN100000008', 'verified', 5.50, 4.1, 64, 5280000.00, false, '1-3 Days', '< 1 hour', '{sanitary-ware}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000009', '20000000-0000-0000-0000-000000000009', 'Nyanya Electrical Supplies', 'Electrical Materials Dealer', 'Nyanya', 'FCT', 'Nyanya Market, Abuja Nasarawa Expressway (border town)', 'RC-10000009', 'TIN100000009', 'verified', 4.50, 4.3, 92, 7510000.00, false, '1-3 Days', '< 1 hour', '{electrical-systems,smart-building-systems}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000010', '20000000-0000-0000-0000-000000000010', 'Nyanya Paints & Finishing Hub', 'Paint & Finishing Dealer', 'Nyanya', 'FCT', 'Nyanya Market, Abuja Nasarawa Expressway', 'RC-10000010', 'TIN100000010', 'verified', 5.50, 4.4, 118, 9320000.00, false, '1-3 Days', '< 1 hour', '{paints-coatings,ceiling-systems}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000011', '20000000-0000-0000-0000-000000000011', 'Nyanya Tiles & Flooring Co.', 'Tiles Dealer', 'Nyanya', 'FCT', 'Nyanya Market, Abuja Nasarawa Expressway', 'RC-10000011', 'TIN100000011', 'verified', 6.00, 4.3, 87, 8140000.00, false, '1-3 Days', '< 1 hour', '{tiles-flooring}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000012', '20000000-0000-0000-0000-000000000012', 'Nyanya Equipment & Site Services', 'Equipment Hire & Services', 'Nyanya', 'FCT', 'Nyanya Market, Abuja Nasarawa Expressway', 'RC-10000012', 'TIN100000012', 'verified', 6.50, 4.2, 41, 4730000.00, false, '1-3 Days', '< 1 hour', '{equipment-site-services,fine-aggregates,coarse-aggregates}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000013', '20000000-0000-0000-0000-000000000013', 'Orile Premium Tiles & Sanitary', 'Premium Tiles & Sanitary Import', 'Lagos', 'Lagos', 'Orile-Iganmu Market, Lagos Mainland', 'RC-10000013', 'TIN100000013', 'verified', 6.00, 4.7, 156, 18900000.00, true, '2-5 Days', '< 1 hour', '{tiles-flooring,sanitary-ware}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000014', '20000000-0000-0000-0000-000000000014', 'Orile Doors, Windows & Glass', 'Doors & Glass Dealer', 'Lagos', 'Lagos', 'Orile-Iganmu Market, Lagos Mainland', 'RC-10000014', 'TIN100000014', 'verified', 6.00, 4.5, 98, 10200000.00, false, '2-5 Days', '< 1 hour', '{doors-windows-facades,glass}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000015', '20000000-0000-0000-0000-000000000015', 'Ojota Cement & Finishing Mart', 'Cement & Finishing Dealer', 'Lagos', 'Lagos', 'Ojota Market, Lagos', 'RC-10000015', 'TIN100000015', 'verified', 2.50, 4.4, 132, 15200000.00, false, '2-5 Days', '< 1 hour', '{cement,paints-coatings}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000016', '20000000-0000-0000-0000-000000000016', 'Abule Egba Roofing & Tiles', 'Roofing & Tiles Dealer', 'Lagos', 'Lagos', 'Abule Egba, Lagos-Abeokuta Expressway', 'RC-10000016', 'TIN100000016', 'verified', 5.00, 4.3, 89, 9830000.00, false, '2-5 Days', '< 1 hour', '{roofing-systems,tiles-flooring}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000017', '20000000-0000-0000-0000-000000000017', 'Abule Egba Paints & Ceiling', 'Paint & Ceiling Dealer', 'Lagos', 'Lagos', 'Abule Egba, Lagos-Abeokuta Expressway', 'RC-10000017', 'TIN100000017', 'verified', 5.50, 4.2, 71, 6840000.00, false, '2-5 Days', '< 1 hour', '{paints-coatings,ceiling-systems}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000018', '20000000-0000-0000-0000-000000000018', 'Mushin Electrical & Plumbing', 'Electrical & Plumbing Dealer', 'Lagos', 'Lagos', 'Mushin Market, Lagos', 'RC-10000018', 'TIN100000018', 'verified', 4.50, 4.1, 84, 7230000.00, false, '2-5 Days', '< 1 hour', '{electrical-systems,plumbing-systems}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000019', '20000000-0000-0000-0000-000000000019', 'Trade Fair Masonry & Bricks', 'Masonry & Brick Supplier', 'Lagos', 'Lagos', 'Abule Ado, Trade Fair Complex, Lagos', 'RC-10000019', 'TIN100000019', 'verified', 3.00, 4.4, 127, 12050000.00, false, '2-5 Days', '< 1 hour', '{masonry-products,burnt-bricks,fine-aggregates,coarse-aggregates}', NOW(), NOW()),
  ('21000000-0000-0000-0000-000000000020', '20000000-0000-0000-0000-000000000020', 'Ikeja Solar & Renewable Energy', 'Solar Energy Solutions', 'Lagos', 'Lagos', 'Ikeja, Lagos', 'RC-10000020', 'TIN100000020', 'verified', 4.50, 4.6, 73, 8900000.00, true, '2-5 Days', '< 1 hour', '{solar-renewable-energy,electrical-systems}', NOW(), NOW())
) AS v(id, user_id, business_name, business_type, city, state, business_address, cac, tin, verification_status, commission_rate, rating, total_reviews, total_sales, is_featured, delivery_time, response_time, specializations, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM vendors x WHERE x.id = v.id::uuid);

-- =============================================================
-- 3. BACKFILL REMAINING VENDOR COLUMNS
--    Populates newer columns on the 20 seeded vendors so the
--    Admin tables render real data (no NULLs).
-- =============================================================
UPDATE vendors SET
    verification_tier = 'cac_only',
    transaction_volume = COALESCE(transaction_volume, 0.00),
    verification_date = created_at,
    verified_by = user_id,
    business_image = NULL,
    rating = COALESCE(rating, 0.00),
    commission_rate = COALESCE(commission_rate, 10.00),
    total_reviews = COALESCE(total_reviews, 0),
    total_sales = COALESCE(total_sales, 0.00),
    is_featured = COALESCE(is_featured, FALSE),
    updated_at = NOW()
WHERE verification_tier IS NULL
   OR transaction_volume IS NULL
   OR verification_date IS NULL
   OR rating IS NULL
   OR total_sales IS NULL;

-- =============================================================
-- 4. VENDOR BANK ACCOUNTS (one primary per vendor)
-- =============================================================
INSERT INTO vendor_bank_accounts (id, vendor_id, bank_name, account_number, account_name, bank_code, is_primary, verified, created_at)
SELECT * FROM (VALUES
  ('22000000-0000-0000-0000-000000000001'::uuid, '21000000-0000-0000-0000-000000000001'::uuid, 'Zenith Bank', '1012345001', 'Dei-Dei Cement & Steel Depot', '057', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000002', '21000000-0000-0000-0000-000000000002', 'GTBank', '0123456002', 'Dei-Dei Timber Shed', '058', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000003', '21000000-0000-0000-0000-000000000003', 'Access Bank', '0072345003', 'Dei-Dei Blocks & Bricks', '044', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000004', '21000000-0000-0000-0000-000000000004', 'First Bank', '3051234004', 'Dei-Dei GP Tanks & Plumbing', '011', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000005', '21000000-0000-0000-0000-000000000005', 'UBA', '2090115005', 'Dei-Dei Roofing Mart', '033', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000006', '21000000-0000-0000-0000-000000000006', 'Zenith Bank', '1012345006', 'Dei-Dei Hardware & Tools', '057', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000007', '21000000-0000-0000-0000-000000000007', 'Fidelity Bank', '4111234007', 'Zone 5 Plumbing Warehouse', '070', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000008', '21000000-0000-0000-0000-000000000008', 'GTBank', '0123456008', 'Zone 5 Sanitary & Fittings', '058', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000009', '21000000-0000-0000-0000-000000000009', 'Access Bank', '0072345009', 'Nyanya Electrical Supplies', '044', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000010', '21000000-0000-0000-0000-000000000010', 'First Bank', '3051234010', 'Nyanya Paints & Finishing Hub', '011', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000011', '21000000-0000-0000-0000-000000000011', 'UBA', '2090115011', 'Nyanya Tiles & Flooring Co.', '033', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000012', '21000000-0000-0000-0000-000000000012', 'Zenith Bank', '1012345012', 'Nyanya Equipment & Site Services', '057', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000013', '21000000-0000-0000-0000-000000000013', 'GTBank', '0123456013', 'Orile Premium Tiles & Sanitary', '058', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000014', '21000000-0000-0000-0000-000000000014', 'Access Bank', '0072345014', 'Orile Doors, Windows & Glass', '044', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000015', '21000000-0000-0000-0000-000000000015', 'First Bank', '3051234015', 'Ojota Cement & Finishing Mart', '011', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000016', '21000000-0000-0000-0000-000000000016', 'UBA', '2090115016', 'Abule Egba Roofing & Tiles', '033', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000017', '21000000-0000-0000-0000-000000000017', 'Zenith Bank', '1012345017', 'Abule Egba Paints & Ceiling', '057', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000018', '21000000-0000-0000-0000-000000000018', 'GTBank', '0123456018', 'Mushin Electrical & Plumbing', '058', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000019', '21000000-0000-0000-0000-000000000019', 'Access Bank', '0072345019', 'Trade Fair Masonry & Bricks', '044', true, true, NOW()),
  ('22000000-0000-0000-0000-000000000020', '21000000-0000-0000-0000-000000000020', 'First Bank', '3051234020', 'Ikeja Solar & Renewable Energy', '011', true, true, NOW())
) AS b(id, vendor_id, bank_name, account_number, account_name, bank_code, is_primary, verified, created_at)
WHERE NOT EXISTS (SELECT 1 FROM vendor_bank_accounts x WHERE x.id = b.id::uuid);

-- =============================================================
-- 4. BRANDS (de-duped, from CATEGORY_BRANDS)
-- =============================================================
INSERT INTO brands (id, name, slug, is_active, created_at)
SELECT * FROM (VALUES
  ('23000000-0000-0000-0000-000000000001'::uuid, 'Dangote Cement', 'dangote-cement', true, NOW()),
  ('23000000-0000-0000-0000-000000000002', 'BUA Cement', 'bua-cement', true, NOW()),
  ('23000000-0000-0000-0000-000000000003', 'African Industries', 'african-industries', true, NOW()),
  ('23000000-0000-0000-0000-000000000004', 'Sunflag Steel', 'sunflag-steel', true, NOW()),
  ('23000000-0000-0000-0000-000000000005', 'Local Hardwood Suppliers', 'local-hardwood-suppliers', true, NOW()),
  ('23000000-0000-0000-0000-000000000006', 'MDF Nigeria', 'mdf-nigeria', true, NOW()),
  ('23000000-0000-0000-0000-000000000007', 'Local Block Industry', 'local-block-industry', true, NOW()),
  ('23000000-0000-0000-0000-000000000008', 'Premium Brick Company Nigeria', 'premium-brick-company-nigeria', true, NOW()),
  ('23000000-0000-0000-0000-000000000009', 'Mikano Plumbing', 'mikano-plumbing', true, NOW()),
  ('23000000-0000-0000-0000-000000000010', 'Cera Sanitary', 'cera-sanitary', true, NOW()),
  ('23000000-0000-0000-0000-000000000011', 'Tower Aluminium', 'tower-aluminium', true, NOW()),
  ('23000000-0000-0000-0000-000000000012', 'Bosch', 'bosch', true, NOW()),
  ('23000000-0000-0000-0000-000000000013', 'Coleman Wires & Cables', 'coleman-wires-cables', true, NOW()),
  ('23000000-0000-0000-0000-000000000014', 'Dulux', 'dulux', true, NOW()),
  ('23000000-0000-0000-0000-000000000015', 'Vitapur', 'vitapur', true, NOW()),
  ('23000000-0000-0000-0000-000000000016', 'Prime Doors', 'prime-doors', true, NOW()),
  ('23000000-0000-0000-0000-000000000017', 'Beta Glass', 'beta-glass', true, NOW()),
  ('23000000-0000-0000-0000-000000000018', 'Saint-Gobain', 'saint-gobain', true, NOW()),
  ('23000000-0000-0000-0000-000000000019', 'JinkoSolar', 'jinkosolar', true, NOW()),
  ('23000000-0000-0000-0000-000000000020', 'Mantrac Nigeria', 'mantrac-nigeria', true, NOW())
) AS b(id, name, slug, is_active, created_at)
WHERE NOT EXISTS (SELECT 1 FROM brands x WHERE x.name = b.name);

-- =============================================================
-- 5. PRODUCTS
--    category_id = parent category IDs from seed_categories.sql:
--      a0000001 Cement, a0000002 Reinforcement Steel,
--      a0000003 Fine Aggregates, a0000004 Coarse Aggregates,
--      a0000005 Masonry, a0000006 Burnt Bricks, a0000007 Ceiling,
--      a0000008 Tiles, a0000009 Timber, a0000010 Roofing,
--      a0000011 Plumbing, a0000012 Sanitary, a0000013 Electrical,
--      a0000014 Paints, a0000015 Doors/Windows, a0000016 Glass,
--      a0000017 Smart, a0000018 Solar, a0000019 Tools, a0000020 Equipment
--    status = 'active'
-- =============================================================
INSERT INTO products (id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
SELECT * FROM (VALUES
  -- V1 Dei-Dei Cement & Steel Depot (cement + reinforcement steel)
  ('24000000-0000-0000-0000-000000000001'::uuid, '21000000-0000-0000-0000-000000000001'::uuid, 'a0000001-0000-0000-0000-000000000001'::uuid, NULL::uuid, 'Dangote Cement 50kg', 'dangote-cement-50kg-deidei', 'CEM-DANG50-DEIDEI', 'Portland limestone cement for general construction', 'active', 12500.00, 500, 'bag', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000002', '21000000-0000-0000-0000-000000000001', 'a0000001-0000-0000-0000-000000000001', NULL, 'BUA Cement 50kg', 'bua-cement-50kg-deidei', 'CEM-BUA50-DEIDEI', 'High-quality Portland cement for building and construction', 'active', 12200.00, 400, 'bag', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000003', '21000000-0000-0000-0000-000000000001', 'a0000002-0000-0000-0000-000000000002', NULL, '12mm Reinforcement Rod', '12mm-reinforcement-rod-deidei', 'STL-ROD12-DEIDEI', 'Steel reinforcing bar for concrete structures', 'active', 8500.00, 2000, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000004', '21000000-0000-0000-0000-000000000001', 'a0000002-0000-0000-0000-000000000002', NULL, '16mm Reinforcement Rod', '16mm-reinforcement-rod-deidei', 'STL-ROD16-DEIDEI', 'Heavy-duty steel rod for structural reinforcement', 'active', 15200.00, 1500, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000005', '21000000-0000-0000-0000-000000000001', 'a0000002-0000-0000-0000-000000000002', NULL, 'Binding Wire (1kg roll)', 'binding-wire-deidei', 'STL-WIRE-DEIDEI', 'Annealed wire for tying reinforcement bars', 'active', 1800.00, 800, 'roll', true, NOW(), NOW()),

  -- V2 Dei-Dei Timber Shed (timber only)
  ('24000000-0000-0000-0000-000000000006', '21000000-0000-0000-0000-000000000002', 'a0000009-0000-0000-0000-000000000009', NULL, '2x4 Timber', '2x4-timber-deidei', 'WOD-2X4-DEIDEI', 'Standard timber for wall framing and supports', 'active', 4500.00, 3000, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000007', '21000000-0000-0000-0000-000000000002', 'a0000009-0000-0000-0000-000000000009', NULL, '2x6 Timber', '2x6-timber-deidei', 'WOD-2X6-DEIDEI', 'Larger timber for beams and heavy framing', 'active', 6500.00, 2000, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000008', '21000000-0000-0000-0000-000000000002', 'a0000009-0000-0000-0000-000000000009', NULL, '12mm Plywood', '12mm-plywood-deidei', 'WOD-PLY12-DEIDEI', 'Standard plywood sheet for furniture and formwork', 'active', 9500.00, 600, 'sheet', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000009', '21000000-0000-0000-0000-000000000002', 'a0000009-0000-0000-0000-000000000009', NULL, '18mm MDF Board', '18mm-mdf-board-deidei', 'WOD-MDF18-DEIDEI', 'Thick MDF for cabinet doors and shelving', 'active', 14500.00, 400, 'sheet', true, NOW(), NOW()),

  -- V3 Dei-Dei Blocks & Bricks (masonry + burnt bricks)
  ('24000000-0000-0000-0000-000000000010', '21000000-0000-0000-0000-000000000003', 'a0000005-0000-0000-0000-000000000005', NULL, '9-inch Hollow Block', '9-inch-hollow-block-deidei', 'BLK-9INCH-DEIDEI', 'Larger hollow block for load-bearing walls', 'active', 450.00, 20000, 'piece', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000011', '21000000-0000-0000-0000-000000000003', 'a0000005-0000-0000-0000-000000000005', NULL, '6-inch Hollow Block', '6-inch-hollow-block-deidei', 'BLK-6INCH-DEIDEI', 'Standard hollow block for wall construction', 'active', 350.00, 25000, 'piece', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000012', '21000000-0000-0000-0000-000000000003', 'a0000006-0000-0000-0000-000000000006', NULL, 'Premium Burnt Brick', 'premium-burnt-brick-deidei', 'BRK-PREM-DEIDEI', 'High-quality fired clay brick for durability and aesthetics', 'active', 150.00, 30000, 'piece', true, NOW(), NOW()),

  -- V4 Dei-Dei GP Tanks & Plumbing (plumbing + sanitary)
  ('24000000-0000-0000-0000-000000000013', '21000000-0000-0000-0000-000000000004', 'a0000011-0000-0000-0000-000000000011', NULL, 'Plastic Water Tank (1000L)', 'plastic-water-tank-1000l-deidei', 'PLB-TANK1000-DEIDEI', 'Large polyethylene water storage tank', 'active', 135000.00, 120, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000014', '21000000-0000-0000-0000-000000000004', 'a0000011-0000-0000-0000-000000000011', NULL, 'Plastic Water Tank (2500L)', 'plastic-water-tank-2500l-deidei', 'PLB-TANK2500-DEIDEI', 'Extra-large polyethylene water storage tank', 'active', 285000.00, 80, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000015', '21000000-0000-0000-0000-000000000004', 'a0000011-0000-0000-0000-000000000011', NULL, '110mm PVC Pipe', '110mm-pvc-pipe-deidei', 'PLB-PVC110-DEIDEI', 'Soil and waste PVC pipe for toilet drainage', 'active', 8500.00, 600, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000016', '21000000-0000-0000-0000-000000000004', 'a0000012-0000-0000-0000-000000000012', NULL, 'WC/Toilet Suite', 'wc-toilet-suite-deidei', 'PLB-WC-DEIDEI', 'Complete toilet bowl and cistern set', 'active', 65000.00, 90, 'unit', true, NOW(), NOW()),

  -- V5 Dei-Dei Roofing Mart (roofing + doors/windows)
  ('24000000-0000-0000-0000-000000000017', '21000000-0000-0000-0000-000000000005', 'a0000010-0000-0000-0000-000000000010', NULL, 'Aluminium Roofing Sheet', 'aluminium-roofing-sheet-deidei', 'ROF-ALUM-DEIDEI', 'Lightweight aluminium sheet for roofing', 'active', 6500.00, 1000, 'sheet', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000018', '21000000-0000-0000-0000-000000000005', 'a0000010-0000-0000-0000-000000000010', NULL, 'Stone-Coated Roofing Sheet', 'stone-coated-roofing-sheet-deidei', 'ROF-STONE-DEIDEI', 'Durable stone-coated steel roofing tile', 'active', 11500.00, 800, 'sheet', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000019', '21000000-0000-0000-0000-000000000005', 'a0000015-0000-0000-0000-000000000015', NULL, 'Aluminium Window (Standard)', 'aluminium-window-deidei', 'WIN-ALUM-DEIDEI', 'Sliding aluminium window with frame', 'active', 95000.00, 60, 'unit', true, NOW(), NOW())
) AS p(id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM products x WHERE x.sku = p.sku);

-- V6 Dei-Dei Hardware & Tools (tools + consumables)
INSERT INTO products (id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
SELECT * FROM (VALUES
  ('24000000-0000-0000-0000-000000000020'::uuid, '21000000-0000-0000-0000-000000000006'::uuid, 'a0000019-0000-0000-0000-000000000019'::uuid, NULL::uuid, 'Angle Grinder', 'angle-grinder-deidei', 'HWT-ANGLEGRINDER-DEIDEI', 'Power tool for cutting and grinding metal/stone', 'active', 32000.00, 150, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000021', '21000000-0000-0000-0000-000000000006', 'a0000019-0000-0000-0000-000000000019', NULL, 'Electric Drill', 'electric-drill-deidei', 'HWT-DRILL-DEIDEI', 'Power drill for wood, metal and masonry', 'active', 45000.00, 100, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000022', '21000000-0000-0000-0000-000000000006', 'a0000019-0000-0000-0000-000000000019', NULL, 'PPE Safety Kit', 'ppe-safety-kit-deidei', 'HWT-PPE-DEIDEI', 'Hard hat, gloves, goggles, and safety vest', 'active', 8500.00, 300, 'unit', true, NOW(), NOW()),

  -- V7 Zone 5 Plumbing Warehouse (plumbing)
  ('24000000-0000-0000-0000-000000000023', '21000000-0000-0000-0000-000000000007', 'a0000011-0000-0000-0000-000000000011', NULL, '20mm PVC Pipe', '20mm-pvc-pipe-zone5', 'PLB-PVC20-ZONE5', 'Standard PVC pipe for water supply lines', 'active', 1200.00, 5000, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000024', '21000000-0000-0000-0000-000000000007', 'a0000011-0000-0000-0000-000000000011', NULL, 'Ball Valve (1/2 inch)', 'ball-valve-zone5', 'PLB-BALLVALVE-ZONE5', 'Brass ball valve for water flow control', 'active', 3500.00, 1000, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000025', '21000000-0000-0000-0000-000000000007', 'a0000011-0000-0000-0000-000000000011', NULL, 'Water Pump (1HP)', 'water-pump-1hp-zone5', 'PLB-PUMP-ZONE5', 'Submersible or surface water pump', 'active', 185000.00, 40, 'unit', true, NOW(), NOW()),

  -- V8 Zone 5 Sanitary & Fittings (sanitary, premium)
  ('24000000-0000-0000-0000-000000000026', '21000000-0000-0000-0000-000000000008', 'a0000012-0000-0000-0000-000000000012', NULL, 'WC Toilet Suite (Close Coupled)', 'wc-toilet-suite-close-coupled-zone5', 'SAN-WC-ZONE5', 'Complete toilet bowl and cistern with seat cover', 'active', 85000.00, 70, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000027', '21000000-0000-0000-0000-000000000008', 'a0000012-0000-0000-0000-000000000012', NULL, 'Wash Basin (Countertop)', 'wash-basin-countertop-zone5', 'SAN-BASIN-ZONE5', 'Ceramic countertop wash basin', 'active', 45000.00, 80, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000028', '21000000-0000-0000-0000-000000000008', 'a0000012-0000-0000-0000-000000000012', NULL, 'Mixer Faucet Set', 'mixer-faucet-set-zone5', 'SAN-MIXER-ZONE5', 'Single-lever mixer for basin or sink', 'active', 38000.00, 120, 'unit', true, NOW(), NOW()),

  -- V9 Nyanya Electrical Supplies (electrical + smart)
  ('24000000-0000-0000-0000-000000000029', '21000000-0000-0000-0000-000000000009', 'a0000013-0000-0000-0000-000000000013', NULL, '2.5mm² Electrical Cable', '2-5mm-electrical-cable-nyanya', 'ELC-CABLE2.5-NYANYA', 'Single-core copper cable for power sockets', 'active', 118000.00, 200, 'roll', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000030', '21000000-0000-0000-0000-000000000009', 'a0000013-0000-0000-0000-000000000013', NULL, 'Distribution Board (8-way)', 'distribution-board-8way-nyanya', 'ELC-DB-NYANYA', 'Consumer unit for circuit breaker distribution', 'active', 35000.00, 150, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000031', '21000000-0000-0000-0000-000000000009', 'a0000017-0000-0000-0000-000000000017', NULL, 'CCTV Camera (IP, 2MP)', 'cctv-camera-ip-2mp-nyanya', 'SMR-CCTV-NYANYA', 'IP security camera for remote monitoring', 'active', 55000.00, 100, 'unit', true, NOW(), NOW()),

  -- V10 Nyanya Paints & Finishing Hub (paints + ceiling)
  ('24000000-0000-0000-0000-000000000032', '21000000-0000-0000-0000-000000000010', 'a0000014-0000-0000-0000-000000000014', NULL, 'Emulsion Paint (4L)', 'emulsion-paint-4l-nyanya', 'PNT-EMULSION4L-NYANYA', 'Water-based interior wall paint', 'active', 28000.00, 500, 'bucket', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000033', '21000000-0000-0000-0000-000000000010', 'a0000014-0000-0000-0000-000000000014', NULL, 'Gloss Paint (4L)', 'gloss-paint-4l-nyanya', 'PNT-GLOSS4L-NYANYA', 'Oil-based gloss paint for wood and metal', 'active', 42000.00, 350, 'bucket', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000034', '21000000-0000-0000-0000-000000000010', 'a0000007-0000-0000-0000-000000000007', NULL, 'PVC Ceiling Sheet', 'pvc-ceiling-sheet-nyanya', 'CLG-PVC-NYANYA', 'Water-resistant PVC panel for ceiling finishing', 'active', 4800.00, 2000, 'sheet', true, NOW(), NOW())
) AS p(id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM products x WHERE x.sku = p.sku);

-- V11 Nyanya Tiles & Flooring Co. (tiles)
INSERT INTO products (id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
SELECT * FROM (VALUES
  ('24000000-0000-0000-0000-000000000035'::uuid, '21000000-0000-0000-0000-000000000011'::uuid, 'a0000008-0000-0000-0000-000000000008'::uuid, NULL::uuid, 'Ceramic Floor Tile (sqm)', 'ceramic-floor-tile-sqm-nyanya', 'TL-CERAMIC-NYANYA', 'Glazed ceramic tile for indoor flooring', 'active', 13000.00, 1500, 'sqm', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000036', '21000000-0000-0000-0000-000000000011', 'a0000008-0000-0000-0000-000000000008', NULL, 'Porcelain Tile (sqm)', 'porcelain-tile-sqm-nyanya', 'TL-PORCELAIN-NYANYA', 'Dense porcelain tile for high-traffic areas', 'active', 15500.00, 1200, 'sqm', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000037', '21000000-0000-0000-0000-000000000011', 'a0000008-0000-0000-0000-000000000008', NULL, 'Tile Adhesive (25kg)', 'tile-adhesive-25kg-nyanya', 'TL-ADHESIVE-NYANYA', 'Cement-based adhesive for tile installation', 'active', 7500.00, 800, 'bag', true, NOW(), NOW()),

  -- V12 Nyanya Equipment & Site Services (equipment + aggregates)
  ('24000000-0000-0000-0000-000000000038', '21000000-0000-0000-0000-000000000012', 'a0000020-0000-0000-0000-000000000020', NULL, 'Concrete Mixer (per day)', 'concrete-mixer-per-day-nyanya', 'EQP-MIXER-NYANYA', 'Drum concrete mixer for on-site mixing', 'active', 45000.00, 15, 'day', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000039', '21000000-0000-0000-0000-000000000012', 'a0000020-0000-0000-0000-000000000020', NULL, 'Haulage Service (per trip)', 'haulage-service-per-trip-nyanya', 'EQP-HAULAGE-NYANYA', 'Material transportation service within city', 'active', 60000.00, 30, 'trip', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000040', '21000000-0000-0000-0000-000000000012', 'a0000003-0000-0000-0000-000000000003', NULL, 'Sharp Sand (per ton)', 'sharp-sand-per-ton-nyanya', 'SND-SHARP-NYANYA', 'Coarse sand for concrete and block work', 'active', 38000.00, 1000, 'ton', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000041', '21000000-0000-0000-0000-000000000012', 'a0000004-0000-0000-0000-000000000004', NULL, 'Gravel (3/4 inch per ton)', 'gravel-3-4-inch-per-ton-nyanya', 'AGG-GRAVEL34-NYANYA', 'Crushed granite aggregate for concrete', 'active', 45000.00, 900, 'ton', true, NOW(), NOW()),

  -- V13 Orile Premium Tiles & Sanitary (premium imports)
  ('24000000-0000-0000-0000-000000000042', '21000000-0000-0000-0000-000000000013', 'a0000008-0000-0000-0000-000000000008', NULL, 'Marble Tile (sqm)', 'marble-tile-sqm-orile', 'TL-MARBLE-ORILE', 'Natural marble tile for luxury finishes', 'active', 38000.00, 600, 'sqm', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000043', '21000000-0000-0000-0000-000000000013', 'a0000008-0000-0000-0000-000000000008', NULL, 'Granite Tile (sqm)', 'granite-tile-sqm-orile', 'TL-GRANITE-ORILE', 'Natural granite tile for durable flooring', 'active', 42000.00, 500, 'sqm', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000044', '21000000-0000-0000-0000-000000000013', 'a0000012-0000-0000-0000-000000000012', NULL, 'Bathtub (Standard)', 'bathtub-standard-orile', 'SAN-BATHTUB-ORILE', 'Acrylic or cast iron bathtub', 'active', 185000.00, 40, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000045', '21000000-0000-0000-0000-000000000013', 'a0000012-0000-0000-0000-000000000012', NULL, 'Shower Enclosure', 'shower-enclosure-orile', 'SAN-SHOWER-ORILE', 'Glass shower cubicle with sliding door', 'active', 145000.00, 50, 'unit', true, NOW(), NOW()),

  -- V14 Orile Doors, Windows & Glass (doors + glass)
  ('24000000-0000-0000-0000-000000000046', '21000000-0000-0000-0000-000000000014', 'a0000015-0000-0000-0000-000000000015', NULL, 'Security Door (Metal)', 'security-door-metal-orile', 'DR-SECURITY-ORILE', 'Steel security door with grilles', 'active', 165000.00, 60, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000047', '21000000-0000-0000-0000-000000000014', 'a0000015-0000-0000-0000-000000000015', NULL, 'Flush Door (Standard)', 'flush-door-standard-orile', 'DR-FLUSH-ORILE', 'Hollow-core flush door for interior rooms', 'active', 72000.00, 200, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000048', '21000000-0000-0000-0000-000000000014', 'a0000016-0000-0000-0000-000000000016', NULL, 'Tempered Glass (6mm, sqm)', 'tempered-glass-6mm-sqm-orile', 'GLS-TEMPERED-ORILE', 'Heat-strengthened safety glass for doors', 'active', 18000.00, 1000, 'sqm', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000049', '21000000-0000-0000-0000-000000000014', 'a0000016-0000-0000-0000-000000000016', NULL, 'Mirror Glass (4mm, sqm)', 'mirror-glass-4mm-sqm-orile', 'GLS-MIRROR-ORILE', 'Silver-backed mirror glass for bathrooms', 'active', 15000.00, 800, 'sqm', true, NOW(), NOW()),

  -- V15 Ojota Cement & Finishing Mart (cement + paints)
  ('24000000-0000-0000-0000-000000000050', '21000000-0000-0000-0000-000000000015', 'a0000001-0000-0000-0000-000000000001', NULL, 'Dangote Cement 50kg', 'dangote-cement-50kg-ojota', 'CEM-DANG50-OJOTA', 'Portland limestone cement for general construction', 'active', 11500.00, 800, 'bag', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000051', '21000000-0000-0000-0000-000000000015', 'a0000014-0000-0000-0000-000000000014', NULL, 'Emulsion Paint (20L)', 'emulsion-paint-20l-ojota', 'PNT-EMULSION20L-OJOTA', 'Large bucket of interior emulsion paint', 'active', 125000.00, 300, 'bucket', true, NOW(), NOW()),

  -- V16 Abule Egba Roofing & Tiles (roofing + tiles)
  ('24000000-0000-0000-0000-000000000052', '21000000-0000-0000-0000-000000000016', 'a0000010-0000-0000-0000-000000000010', NULL, 'Aluminium Gutter', 'aluminium-gutter-abuleegba', 'ROF-GUTTER-ABULEEGBA', 'Rainwater gutter for roof drainage', 'active', 5500.00, 900, 'length', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000053', '21000000-0000-0000-0000-000000000016', 'a0000008-0000-0000-0000-000000000008', NULL, 'Ceramic Floor Tile (sqm)', 'ceramic-floor-tile-sqm-abuleegba', 'TL-CERAMIC-ABULEEGBA', 'Glazed ceramic tile for indoor flooring', 'active', 12500.00, 1100, 'sqm', true, NOW(), NOW()),

  -- V17 Abule Egba Paints & Ceiling (paints + ceiling)
  ('24000000-0000-0000-0000-000000000054', '21000000-0000-0000-0000-000000000017', 'a0000014-0000-0000-0000-000000000014', NULL, 'Textured Coating (20L)', 'textured-coating-20l-abuleegba', 'PNT-TEXCOAT-ABULEEGBA', 'Decorative textured wall coating', 'active', 135000.00, 200, 'bucket', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000055', '21000000-0000-0000-0000-000000000017', 'a0000007-0000-0000-0000-000000000007', NULL, 'Gypsum Ceiling Board', 'gypsum-ceiling-board-abuleegba', 'CLG-GYPSUM-ABULEEGBA', 'Standard gypsum board for suspended ceilings', 'active', 6500.00, 700, 'sheet', true, NOW(), NOW()),

  -- V18 Mushin Electrical & Plumbing (electrical + plumbing)
  ('24000000-0000-0000-0000-000000000056', '21000000-0000-0000-0000-000000000018', 'a0000013-0000-0000-0000-000000000013', NULL, 'MCB (20A)', 'mcb-20a-mushin', 'ELC-MCB-MUSHIN', 'Miniature circuit breaker for overload protection', 'active', 4500.00, 1500, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000057', '21000000-0000-0000-0000-000000000018', 'a0000013-0000-0000-0000-000000000013', NULL, 'LED Bulb (10W)', 'led-bulb-10w-mushin', 'ELC-LED-MUSHIN', 'Energy-saving LED bulb for general lighting', 'active', 1800.00, 4000, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000058', '21000000-0000-0000-0000-000000000018', 'a0000011-0000-0000-0000-000000000011', NULL, 'Gate Valve', 'gate-valve-mushin', 'PLB-GATEVALVE-MUSHIN', 'Brass gate valve for on/off water control', 'active', 6500.00, 800, 'unit', true, NOW(), NOW()),

  -- V19 Trade Fair Masonry & Bricks (masonry + aggregates)
  ('24000000-0000-0000-0000-000000000059', '21000000-0000-0000-0000-000000000019', 'a0000005-0000-0000-0000-000000000005', NULL, 'Solid Block', 'solid-block-tradefair', 'BLK-SOLID-TRADEFAIR', 'Dense solid block for foundations and columns', 'active', 550.00, 18000, 'piece', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000060', '21000000-0000-0000-0000-000000000019', 'a0000006-0000-0000-0000-000000000006', NULL, 'Face Brick', 'face-brick-tradefair', 'BRK-FACE-TRADEFAIR', 'Aesthetic brick for exposed wall finishes', 'active', 180.00, 20000, 'piece', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000061', '21000000-0000-0000-0000-000000000019', 'a0000004-0000-0000-0000-000000000004', NULL, 'Granite Chippings (per ton)', 'granite-chippings-per-ton-tradefair', 'AGG-GRANITE-TRADEFAIR', 'Decorative granite chips for landscaping', 'active', 48000.00, 800, 'ton', true, NOW(), NOW()),

  -- V20 Ikeja Solar & Renewable Energy (solar)
  ('24000000-0000-0000-0000-000000000062', '21000000-0000-0000-0000-000000000020', 'a0000018-0000-0000-0000-000000000018', NULL, 'Solar Panel (550W)', 'solar-panel-550w-ikeja', 'SOL-PANEL550-IKEJA', 'Monocrystalline solar photovoltaic panel', 'active', 285000.00, 200, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000063', '21000000-0000-0000-0000-000000000020', 'a0000018-0000-0000-0000-000000000018', NULL, 'Inverter (5kVA)', 'inverter-5kva-ikeja', 'SOL-INV5KVA-IKEJA', 'Pure sine wave inverter for off-grid system', 'active', 650000.00, 120, 'unit', true, NOW(), NOW()),
  ('24000000-0000-0000-0000-000000000064', '21000000-0000-0000-0000-000000000020', 'a0000018-0000-0000-0000-000000000018', NULL, 'Lithium Battery (200Ah)', 'lithium-battery-200ah-ikeja', 'SOL-BATT200-IKEJA', 'Lithium-ion deep cycle battery for solar storage', 'active', 950000.00, 100, 'unit', true, NOW(), NOW())
) AS p(id, vendor_id, category_id, brand_id, name, slug, sku, short_description, status, base_price, quantity, unit_of_measure, is_verified, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM products x WHERE x.sku = p.sku);

-- =============================================================
-- 6. MATERIAL RATES + HISTORY
--    One material_rate per seeded product (FK category + vendor).
--    state = vendor's state (FCT for Abuja, Lagos for Lagos).
--    current_price = product base_price. trend = 'stable'.
-- =============================================================
INSERT INTO material_rates (id, category_id, material_name, specification, unit, current_price, previous_price, currency, state, lga, supplier_id, trend, source, verified_at, created_at, updated_at)
SELECT * FROM (VALUES
  ('25000000-0000-0000-0000-000000000001'::uuid, 'a0000001-0000-0000-0000-000000000001'::uuid, 'Dangote Cement 50kg', '50kg bag', 'bag', 12500.00, NULL::numeric, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000001'::uuid, 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000002', 'a0000001-0000-0000-0000-000000000001', 'BUA Cement 50kg', '50kg bag', 'bag', 12200.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000001', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000003', 'a0000002-0000-0000-0000-000000000002', '12mm Reinforcement Rod', '12mm x 12m', 'length', 8500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000001', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000004', 'a0000002-0000-0000-0000-000000000002', '16mm Reinforcement Rod', '16mm x 12m', 'length', 15200.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000001', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000005', 'a0000002-0000-0000-0000-000000000002', 'Binding Wire', '1kg roll', 'roll', 1800.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000001', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000006', 'a0000009-0000-0000-0000-000000000009', '2x4 Timber', '2x4 x 12ft', 'length', 4500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000002', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000007', 'a0000009-0000-0000-0000-000000000009', '2x6 Timber', '2x6 x 12ft', 'length', 6500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000002', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000008', 'a0000009-0000-0000-0000-000000000009', '12mm Plywood', '12mm x 8x4', 'sheet', 9500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000002', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000009', 'a0000005-0000-0000-0000-000000000005', '9-inch Hollow Block', '225mm', 'piece', 450.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000003', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000010', 'a0000005-0000-0000-0000-000000000005', '6-inch Hollow Block', '150mm', 'piece', 350.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000003', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000011', 'a0000006-0000-0000-0000-000000000006', 'Premium Burnt Brick', 'standard', 'piece', 150.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000003', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000012', 'a0000011-0000-0000-0000-000000000011', 'Plastic Water Tank (1000L)', '1000L', 'unit', 135000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000004', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000013', 'a0000011-0000-0000-0000-000000000011', 'Plastic Water Tank (2500L)', '2500L', 'unit', 285000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000004', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000014', 'a0000011-0000-0000-0000-000000000011', '110mm PVC Pipe', '110mm', 'length', 8500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000004', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000015', 'a0000012-0000-0000-0000-000000000012', 'WC/Toilet Suite', 'low level', 'unit', 65000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000004', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000016', 'a0000010-0000-0000-0000-000000000010', 'Aluminium Roofing Sheet', 'longspan 0.55mm', 'sheet', 6500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000005', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000017', 'a0000010-0000-0000-0000-000000000010', 'Stone-Coated Roofing Sheet', '0.45mm', 'sheet', 11500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000005', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000018', 'a0000015-0000-0000-0000-000000000015', 'Aluminium Window (Standard)', '1.2x1.2m', 'unit', 95000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000005', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000019', 'a0000019-0000-0000-0000-000000000019', 'Angle Grinder', '7 inch', 'unit', 32000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000006', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000020', 'a0000019-0000-0000-0000-000000000019', 'Electric Drill', '13mm', 'unit', 45000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000006', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000021', 'a0000011-0000-0000-0000-000000000011', '20mm PVC Pipe', '20mm', 'length', 1200.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000007', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000022', 'a0000011-0000-0000-0000-000000000011', 'Ball Valve (1/2 inch)', '1/2 inch brass', 'unit', 3500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000007', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000023', 'a0000011-0000-0000-0000-000000000011', 'Water Pump (1HP)', '1HP submersible', 'unit', 185000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000007', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000024', 'a0000012-0000-0000-0000-000000000012', 'WC Toilet Suite (Close Coupled)', 'close coupled', 'unit', 85000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000008', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000025', 'a0000012-0000-0000-0000-000000000012', 'Wash Basin (Countertop)', 'countertop', 'unit', 45000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000008', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000026', 'a0000013-0000-0000-0000-000000000013', '2.5mm Electrical Cable', '2.5mm single core', 'roll', 118000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000009', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000027', 'a0000013-0000-0000-0000-000000000013', 'Distribution Board (8-way)', '8-way', 'unit', 35000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000009', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000028', 'a0000014-0000-0000-0000-000000000014', 'Emulsion Paint (4L)', '4L interior', 'bucket', 28000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000010', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000029', 'a0000007-0000-0000-0000-000000000007', 'PVC Ceiling Sheet', 'standard', 'sheet', 4800.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000010', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000030', 'a0000008-0000-0000-0000-000000000008', 'Ceramic Floor Tile', '600x600mm', 'sqm', 13000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000011', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000031', 'a0000008-0000-0000-0000-000000000008', 'Porcelain Tile', '600x600mm', 'sqm', 15500.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000011', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000032', 'a0000003-0000-0000-0000-000000000003', 'Sharp Sand', 'per ton', 'ton', 38000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000012', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000033', 'a0000004-0000-0000-0000-000000000004', 'Gravel (3/4 inch)', '3/4 inch', 'ton', 45000.00, NULL, 'NGN', 'FCT', NULL, '21000000-0000-0000-0000-000000000012', 'stable', 'manual', NOW(), NOW(), NOW())
) AS r(id, category_id, material_name, specification, unit, current_price, previous_price, currency, state, lga, supplier_id, trend, source, verified_at, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM material_rates x WHERE x.id = r.id::uuid);

INSERT INTO material_rates (id, category_id, material_name, specification, unit, current_price, previous_price, currency, state, lga, supplier_id, trend, source, verified_at, created_at, updated_at)
SELECT * FROM (VALUES
  ('25000000-0000-0000-0000-000000000034'::uuid, 'a0000008-0000-0000-0000-000000000008'::uuid, 'Marble Tile', 'premium import', 'sqm', 38000.00, NULL::numeric, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000013'::uuid, 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000035', 'a0000012-0000-0000-0000-000000000012', 'Bathtub (Standard)', 'standard acrylic', 'unit', 185000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000013', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000036', 'a0000015-0000-0000-0000-000000000015', 'Security Door (Metal)', '0.9x2.1m', 'unit', 165000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000014', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000037', 'a0000016-0000-0000-0000-000000000016', 'Tempered Glass (6mm)', '6mm', 'sqm', 18000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000014', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000038', 'a0000001-0000-0000-0000-000000000001', 'Dangote Cement 50kg', '50kg bag', 'bag', 11500.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000015', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000039', 'a0000010-0000-0000-0000-000000000010', 'Aluminium Gutter', '5m length', 'length', 5500.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000016', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000040', 'a0000008-0000-0000-0000-000000000008', 'Ceramic Floor Tile', '600x600mm', 'sqm', 12500.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000016', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000041', 'a0000014-0000-0000-0000-000000000014', 'Textured Coating (20L)', '20L decorative', 'bucket', 135000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000017', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000042', 'a0000013-0000-0000-0000-000000000013', 'MCB (20A)', '20A', 'unit', 4500.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000018', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000043', 'a0000013-0000-0000-0000-000000000013', 'LED Bulb (10W)', '10W', 'unit', 1800.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000018', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000044', 'a0000005-0000-0000-0000-000000000005', 'Solid Block', 'standard', 'piece', 550.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000019', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000045', 'a0000006-0000-0000-0000-000000000006', 'Face Brick', 'exposed finish', 'piece', 180.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000019', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000046', 'a0000018-0000-0000-0000-000000000018', 'Solar Panel (550W)', '550W mono', 'unit', 285000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000020', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000047', 'a0000018-0000-0000-0000-000000000018', 'Inverter (5kVA)', '5kVA pure sine', 'unit', 650000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000020', 'stable', 'manual', NOW(), NOW(), NOW()),
  ('25000000-0000-0000-0000-000000000048', 'a0000018-0000-0000-0000-000000000018', 'Lithium Battery (200Ah)', '200Ah', 'unit', 950000.00, NULL, 'NGN', 'Lagos', NULL, '21000000-0000-0000-0000-000000000020', 'stable', 'manual', NOW(), NOW(), NOW())
) AS r(id, category_id, material_name, specification, unit, current_price, previous_price, currency, state, lga, supplier_id, trend, source, verified_at, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM material_rates x WHERE x.id = r.id::uuid);

-- Material rate history: an older baseline + a current record per rate.
INSERT INTO material_rate_history (id, rate_id, price, recorded_at, source)
SELECT * FROM (VALUES
  ('26000000-0000-0000-0000-000000000001'::uuid, '25000000-0000-0000-0000-000000000001'::uuid, 12000.00, NOW() - INTERVAL '45 days', 'manual'),
  ('26000000-0000-0000-0000-000000000002', '25000000-0000-0000-0000-000000000001', 12500.00, NOW() - INTERVAL '10 days', 'manual'),
  ('26000000-0000-0000-0000-000000000003', '25000000-0000-0000-0000-000000000002', 11800.00, NOW() - INTERVAL '40 days', 'manual'),
  ('26000000-0000-0000-0000-000000000004', '25000000-0000-0000-0000-000000000002', 12200.00, NOW() - INTERVAL '12 days', 'manual'),
  ('26000000-0000-0000-0000-000000000005', '25000000-0000-0000-0000-000000000003', 8000.00, NOW() - INTERVAL '60 days', 'manual'),
  ('26000000-0000-0000-0000-000000000006', '25000000-0000-0000-0000-000000000003', 8500.00, NOW() - INTERVAL '15 days', 'manual'),
  ('26000000-0000-0000-0000-000000000007', '25000000-0000-0000-0000-000000000004', 14500.00, NOW() - INTERVAL '55 days', 'manual'),
  ('26000000-0000-0000-0000-000000000008', '25000000-0000-0000-0000-000000000004', 15200.00, NOW() - INTERVAL '20 days', 'manual'),
  ('26000000-0000-0000-0000-000000000009', '25000000-0000-0000-0000-000000000006', 4200.00, NOW() - INTERVAL '70 days', 'manual'),
  ('26000000-0000-0000-0000-000000000010', '25000000-0000-0000-0000-000000000006', 4500.00, NOW() - INTERVAL '25 days', 'manual'),
  ('26000000-0000-0000-0000-000000000011', '25000000-0000-0000-0000-000000000007', 6200.00, NOW() - INTERVAL '65 days', 'manual'),
  ('26000000-0000-0000-0000-000000000012', '25000000-0000-0000-0000-000000000007', 6500.00, NOW() - INTERVAL '18 days', 'manual'),
  ('26000000-0000-0000-0000-000000000013', '25000000-0000-0000-0000-000000000009', 420.00, NOW() - INTERVAL '50 days', 'manual'),
  ('26000000-0000-0000-0000-000000000014', '25000000-0000-0000-0000-000000000009', 450.00, NOW() - INTERVAL '14 days', 'manual'),
  ('26000000-0000-0000-0000-000000000015', '25000000-0000-0000-0000-000000000010', 330.00, NOW() - INTERVAL '48 days', 'manual'),
  ('26000000-0000-0000-0000-000000000016', '25000000-0000-0000-0000-000000000010', 350.00, NOW() - INTERVAL '12 days', 'manual'),
  ('26000000-0000-0000-0000-000000000017', '25000000-0000-0000-0000-000000000012', 130000.00, NOW() - INTERVAL '80 days', 'manual'),
  ('26000000-0000-0000-0000-000000000018', '25000000-0000-0000-0000-000000000012', 135000.00, NOW() - INTERVAL '30 days', 'manual'),
  ('26000000-0000-0000-0000-000000000019', '25000000-0000-0000-0000-000000000016', 6200.00, NOW() - INTERVAL '60 days', 'manual'),
  ('26000000-0000-0000-0000-000000000020', '25000000-0000-0000-0000-000000000016', 6500.00, NOW() - INTERVAL '16 days', 'manual')
) AS h(id, rate_id, price, recorded_at, source)
WHERE NOT EXISTS (SELECT 1 FROM material_rate_history x WHERE x.id = h.id::uuid);

INSERT INTO material_rate_history (id, rate_id, price, recorded_at, source)
SELECT * FROM (VALUES
  ('26000000-0000-0000-0000-000000000021'::uuid, '25000000-0000-0000-0000-000000000019'::uuid, 30000.00, NOW() - INTERVAL '75 days', 'manual'),
  ('26000000-0000-0000-0000-000000000022', '25000000-0000-0000-0000-000000000019', 32000.00, NOW() - INTERVAL '22 days', 'manual'),
  ('26000000-0000-0000-0000-000000000023', '25000000-0000-0000-0000-000000000021', 1100.00, NOW() - INTERVAL '55 days', 'manual'),
  ('26000000-0000-0000-0000-000000000024', '25000000-0000-0000-0000-000000000021', 1200.00, NOW() - INTERVAL '20 days', 'manual'),
  ('26000000-0000-0000-0000-000000000025', '25000000-0000-0000-0000-000000000026', 112000.00, NOW() - INTERVAL '85 days', 'manual'),
  ('26000000-0000-0000-0000-000000000026', '25000000-0000-0000-0000-000000000026', 118000.00, NOW() - INTERVAL '35 days', 'manual'),
  ('26000000-0000-0000-0000-000000000027', '25000000-0000-0000-0000-000000000028', 26500.00, NOW() - INTERVAL '40 days', 'manual'),
  ('26000000-0000-0000-0000-000000000028', '25000000-0000-0000-0000-000000000028', 28000.00, NOW() - INTERVAL '11 days', 'manual'),
  ('26000000-0000-0000-0000-000000000029', '25000000-0000-0000-0000-000000000030', 12500.00, NOW() - INTERVAL '45 days', 'manual'),
  ('26000000-0000-0000-0000-000000000030', '25000000-0000-0000-0000-000000000030', 13000.00, NOW() - INTERVAL '12 days', 'manual'),
  ('26000000-0000-0000-0000-000000000031', '25000000-0000-0000-0000-000000000034', 36000.00, NOW() - INTERVAL '70 days', 'manual'),
  ('26000000-0000-0000-0000-000000000032', '25000000-0000-0000-0000-000000000034', 38000.00, NOW() - INTERVAL '28 days', 'manual'),
  ('26000000-0000-0000-0000-000000000033', '25000000-0000-0000-0000-000000000036', 158000.00, NOW() - INTERVAL '65 days', 'manual'),
  ('26000000-0000-0000-0000-000000000034', '25000000-0000-0000-0000-000000000036', 165000.00, NOW() - INTERVAL '19 days', 'manual'),
  ('26000000-0000-0000-0000-000000000035', '25000000-0000-0000-0000-000000000038', 11000.00, NOW() - INTERVAL '45 days', 'manual'),
  ('26000000-0000-0000-0000-000000000036', '25000000-0000-0000-0000-000000000038', 11500.00, NOW() - INTERVAL '10 days', 'manual'),
  ('26000000-0000-0000-0000-000000000037', '25000000-0000-0000-0000-000000000042', 4300.00, NOW() - INTERVAL '50 days', 'manual'),
  ('26000000-0000-0000-0000-000000000038', '25000000-0000-0000-0000-000000000042', 4500.00, NOW() - INTERVAL '15 days', 'manual'),
  ('26000000-0000-0000-0000-000000000039', '25000000-0000-0000-0000-000000000046', 275000.00, NOW() - INTERVAL '90 days', 'manual'),
  ('26000000-0000-0000-0000-000000000040', '25000000-0000-0000-0000-000000000046', 285000.00, NOW() - INTERVAL '30 days', 'manual')
) AS h(id, rate_id, price, recorded_at, source)
WHERE NOT EXISTS (SELECT 1 FROM material_rate_history x WHERE x.id = h.id::uuid);

COMMIT;
