-- =============================================================
-- BURNCOST — SEED CATEGORIES (20 strategic parents + subcategories)
--
-- This is the CANONICAL fixed-UUID seed used by the other SQL
-- seed files (seed_20_vendors_with_rates.sql references parent
-- category IDs a0000001..a0000020). Run this BEFORE that file.
--
-- NOTE: Backend/app/seed_categories.py seeds the same 20 parents
-- but generates RANDOM uuid.uuid4() IDs on every run (and deletes
-- existing rows). Use one source of truth: either this SQL file
-- or that Python script — not both on the same database, or the
-- fixed IDs referenced by the vendor/product seed will not resolve.
--
-- Idempotent: safe to re-run (ON CONFLICT (slug) DO NOTHING).
-- Wrapped in a single transaction.
-- =============================================================
BEGIN;

-- =============================================================
-- 1. PARENT CATEGORIES (fixed IDs a0000001..a0000020)
-- =============================================================
INSERT INTO categories (id, name, slug, parent_id, description, is_active, display_order, division, material_type, default_unit, waste_factor, platform_margin, fee_model, fee_fixed, created_at, updated_at)
SELECT * FROM (VALUES
  ('a0000001-0000-0000-0000-000000000001'::uuid, 'Cement',                    'cement',                    NULL::uuid, 'Cement — CQI, Fee model: fixed',                true, 1,  'Structure',        'material', 'bag',     0.00, 1.50,  'fixed',      200.00,   NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000000002'::uuid, 'Reinforcement Steel',       'reinforcement-steel',       NULL,        'Reinforcement Steel — SQI, Fee model: percentage', true, 2,  'Structure',        'material', 'tonne',   0.00, 1.25,  'percentage', NULL,     NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000000003'::uuid, 'Fine Aggregates',          'fine-aggregates',          NULL,        'Fine Aggregates — N/A, Fee model: fixed',       true, 3,  'Structure',        'material', 'trip',    0.00, 5.00,  'fixed',      10000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000000004'::uuid, 'Coarse Aggregates',        'coarse-aggregates',        NULL,        'Coarse Aggregates — N/A, Fee model: fixed',     true, 4,  'Structure',        'material', 'trip',    0.00, 5.00,  'fixed',      19000.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000000005'::uuid, 'Masonry Products',         'masonry-products',         NULL,        'Masonry Products — BQI, Fee model: fixed',      true, 5,  'Structure',        'material', 'piece',   0.00, 3.00,  'fixed',      55.00,    NOW(), NOW()),
  ('a0000006-0000-0000-0000-000000000006'::uuid, 'Burnt Bricks',             'burnt-bricks',             NULL,        'Burnt Bricks — N/A, Fee model: percentage',     true, 6,  'Structure',        'material', 'piece',   0.00, 5.00,  'percentage', NULL,     NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000000007'::uuid, 'Ceiling Systems',          'ceiling-systems',          NULL,        'Ceiling Systems — N/A, Fee model: percentage',  true, 7,  'Finishes',         'material', 'sheet',   0.00, 5.50,  'percentage', NULL,     NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000000008'::uuid, 'Tiles & Flooring',         'tiles-flooring',           NULL,        'Tiles & Flooring — FQI, Fee model: percentage', true, 8,  'Finishes',         'material', 'm2',      0.00, 6.00,  'percentage', NULL,     NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000000009'::uuid, 'Timber & Engineered Wood', 'timber-engineered-wood',   NULL,        'Timber & Engineered Wood — WQI, Fee model: percentage', true, 9, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000000010'::uuid, 'Roofing Systems',          'roofing-systems',          NULL,        'Roofing Systems — RQI, Fee model: percentage',  true, 10, 'Building Envelope', 'material', 'sheet', 0.00, 5.00,  'percentage', NULL,     NOW(), NOW()),
  ('a0000011-0000-0000-0000-000000000011'::uuid, 'Plumbing Systems',         'plumbing-systems',         NULL,        'Plumbing Systems — PQI, Fee model: percentage', true, 11, 'MEP',              'material', 'piece',  0.00, 4.50,  'percentage', NULL,     NOW(), NOW()),
  ('a0000012-0000-0000-0000-000000000012'::uuid, 'Sanitary Ware',            'sanitary-ware',            NULL,        'Sanitary Ware — N/A, Fee model: percentage',    true, 12, 'MEP',              'material', 'unit',   0.00, 5.50,  'percentage', NULL,     NOW(), NOW()),
  ('a0000013-0000-0000-0000-000000000013'::uuid, 'Electrical Systems',       'electrical-systems',       NULL,        'Electrical Systems — EQI, Fee model: percentage', true, 13, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000014-0000-0000-0000-000000000014'::uuid, 'Paints & Coatings',        'paints-coatings',          NULL,        'Paints & Coatings — PQI, Fee model: percentage', true, 14, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000000015'::uuid, 'Doors, Windows & Facades', 'doors-windows-facades',    NULL,        'Doors, Windows & Facades — DFQI, Fee model: percentage', true, 15, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000016-0000-0000-0000-000000000016'::uuid, 'Glass',                    'glass',                    NULL,        'Glass — N/A, Fee model: percentage',             true, 16, 'Building Envelope', 'material', 'm2',   0.00, 6.00,  'percentage', NULL,     NOW(), NOW()),
  ('a0000017-0000-0000-0000-000000000017'::uuid, 'Smart Building Systems',   'smart-building-systems',   NULL,        'Smart Building Systems — N/A, Fee model: percentage', true, 17, 'Building Services', 'material', 'system', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000018-0000-0000-0000-000000000018'::uuid, 'Solar & Renewable Energy', 'solar-renewable-energy',   NULL,        'Solar & Renewable Energy — SQI, Fee model: percentage', true, 18, 'Building Services', 'material', 'system', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000019-0000-0000-0000-000000000019'::uuid, 'Tools & Consumables',      'tools-consumables',        NULL,        'Tools & Consumables — N/A, Fee model: percentage', true, 19, 'Finishes', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000000020'::uuid, 'Equipment & Site Services','equipment-site-services',  NULL,        'Equipment & Site Services — N/A, Fee model: service', true, 20, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW())
) AS c(id, name, slug, parent_id, description, is_active, display_order, division, material_type, default_unit, waste_factor, platform_margin, fee_model, fee_fixed, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM categories x WHERE x.slug = c.slug);

-- =============================================================
-- 2. SUBCATEGORIES (children of the 20 parents)
-- =============================================================
INSERT INTO categories (id, name, slug, parent_id, description, is_active, display_order, division, material_type, default_unit, waste_factor, platform_margin, fee_model, fee_fixed, created_at, updated_at)
SELECT * FROM (VALUES
  -- Cement subcategories
  ('a0000001-0000-0000-0000-000000010001'::uuid, 'General Purpose Cement',   'cement-general-purpose-cement',      'a0000001-0000-0000-0000-000000000001'::uuid, 'General purpose cement', true, 1, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010002'::uuid, 'High Strength Cement',     'cement-high-strength-cement',        'a0000001-0000-0000-0000-000000000001', 'High strength cement',   true, 2, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010003'::uuid, 'Sulphate Resistant Cement','cement-sulphate-resistant-cement',   'a0000001-0000-0000-0000-000000000001', 'Sulphate resistant cement', true, 3, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010004'::uuid, 'White Cement',             'cement-white-cement',                'a0000001-0000-0000-0000-000000000001', 'White cement', true, 4, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010005'::uuid, 'Masonry Cement',           'cement-masonry-cement',              'a0000001-0000-0000-0000-000000000001', 'Masonry cement', true, 5, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010006'::uuid, 'Oil Well Cement',          'cement-oil-well-cement',             'a0000001-0000-0000-0000-000000000001', 'Oil well cement', true, 6, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010007'::uuid, 'Bulk Cement',              'cement-bulk-cement',                 'a0000001-0000-0000-0000-000000000001', 'Bulk cement', true, 7, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010008'::uuid, 'Ready-Mix Cement',         'cement-ready-mix-cement',            'a0000001-0000-0000-0000-000000000001', 'Ready-mix cement', true, 8, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),
  ('a0000001-0000-0000-0000-000000010009'::uuid, 'Cement Additives',         'cement-cement-additives',            'a0000001-0000-0000-0000-000000000001', 'Cement additives', true, 9, 'Structure', 'material', 'bag', 0.00, 1.50, 'fixed', 200.00, NOW(), NOW()),

  -- Reinforcement Steel subcategories
  ('a0000002-0000-0000-0000-000000020001'::uuid, 'TMT Rebars',               'reinforcement-steel-tmt-rebars',     'a0000002-0000-0000-0000-000000000002', 'TMT rebars', true, 1, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020002'::uuid, 'Mild Steel Bars',          'reinforcement-steel-mild-steel-bars', 'a0000002-0000-0000-0000-000000000002', 'Mild steel bars', true, 2, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020003'::uuid, 'High Yield Bars',          'reinforcement-steel-high-yield-bars', 'a0000002-0000-0000-0000-000000000002', 'High yield bars', true, 3, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020004'::uuid, 'Wire Rods',                'reinforcement-steel-wire-rods',      'a0000002-0000-0000-0000-000000000002', 'Wire rods', true, 4, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020005'::uuid, 'Binding Wire',             'reinforcement-steel-binding-wire',    'a0000002-0000-0000-0000-000000000002', 'Binding wire', true, 5, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020006'::uuid, 'Steel Mesh',               'reinforcement-steel-steel-mesh',      'a0000002-0000-0000-0000-000000000002', 'Steel mesh', true, 6, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020007'::uuid, 'Steel Plates',             'reinforcement-steel-steel-plates',    'a0000002-0000-0000-0000-000000000002', 'Steel plates', true, 7, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020008'::uuid, 'Structural Steel',         'reinforcement-steel-structural-steel','a0000002-0000-0000-0000-000000000002', 'Structural steel', true, 8, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020009'::uuid, 'Hollow Sections',          'reinforcement-steel-hollow-sections', 'a0000002-0000-0000-0000-000000000002', 'Hollow sections', true, 9, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),
  ('a0000002-0000-0000-0000-000000020010'::uuid, 'Angles & Channels',        'reinforcement-steel-angles-channels', 'a0000002-0000-0000-0000-000000000002', 'Angles and channels', true, 10, 'Structure', 'material', 'tonne', 0.00, 1.25, 'percentage', NULL, NOW(), NOW()),

  -- Fine Aggregates subcategories
  ('a0000003-0000-0000-0000-000000030001'::uuid, 'Sharp Sand',               'fine-aggregates-sharp-sand',         'a0000003-0000-0000-0000-000000000003', 'Sharp sand', true, 1, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030002'::uuid, 'Plaster Sand',             'fine-aggregates-plaster-sand',       'a0000003-0000-0000-0000-000000000003', 'Plaster sand', true, 2, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030003'::uuid, 'Filling Sand',             'fine-aggregates-filling-sand',       'a0000003-0000-0000-0000-000000000003', 'Filling sand', true, 3, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030004'::uuid, 'White Sand',               'fine-aggregates-white-sand',         'a0000003-0000-0000-0000-000000000003', 'White sand', true, 4, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030005'::uuid, 'River Sand',               'fine-aggregates-river-sand',         'a0000003-0000-0000-0000-000000000003', 'River sand', true, 5, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030006'::uuid, 'Laterite',                 'fine-aggregates-laterite',           'a0000003-0000-0000-0000-000000000003', 'Laterite', true, 6, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030007'::uuid, 'Top Soil',                 'fine-aggregates-top-soil',           'a0000003-0000-0000-0000-000000000003', 'Top soil', true, 7, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030008'::uuid, 'Hardcore',                 'fine-aggregates-hardcore',           'a0000003-0000-0000-0000-000000000003', 'Hardcore', true, 8, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),
  ('a0000003-0000-0000-0000-000000030009'::uuid, 'Quarry Dust',              'fine-aggregates-quarry-dust',        'a0000003-0000-0000-0000-000000000003', 'Quarry dust', true, 9, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 10000.00, NOW(), NOW()),

  -- Coarse Aggregates subcategories
  ('a0000004-0000-0000-0000-000000040001'::uuid, 'Granite 1/2"',             'coarse-aggregates-granite-1-2',      'a0000004-0000-0000-0000-000000000004', 'Granite 1/2 inch', true, 1, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040002'::uuid, 'Granite 3/4"',             'coarse-aggregates-granite-3-4',      'a0000004-0000-0000-0000-000000000004', 'Granite 3/4 inch', true, 2, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040003'::uuid, 'Granite 1"',               'coarse-aggregates-granite-1',        'a0000004-0000-0000-0000-000000000004', 'Granite 1 inch', true, 3, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040004'::uuid, 'Granite 1-1/2"',           'coarse-aggregates-granite-1-1-2',    'a0000004-0000-0000-0000-000000000004', 'Granite 1.5 inch', true, 4, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040005'::uuid, 'Stone Dust',               'coarse-aggregates-stone-dust',       'a0000004-0000-0000-0000-000000000004', 'Stone dust', true, 5, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040006'::uuid, 'Crusher Run',              'coarse-aggregates-crusher-run',      'a0000004-0000-0000-0000-000000000004', 'Crusher run', true, 6, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040007'::uuid, 'Gravel',                   'coarse-aggregates-gravel',           'a0000004-0000-0000-0000-000000000004', 'Gravel', true, 7, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040008'::uuid, 'Chippings',                'coarse-aggregates-chippings',        'a0000004-0000-0000-0000-000000000004', 'Chippings', true, 8, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),
  ('a0000004-0000-0000-0000-000000040009'::uuid, 'Base Course',              'coarse-aggregates-base-course',      'a0000004-0000-0000-0000-000000000004', 'Base course', true, 9, 'Structure', 'material', 'trip', 0.00, 5.00, 'fixed', 19000.00, NOW(), NOW()),

  -- Masonry Products subcategories
  ('a0000005-0000-0000-0000-000000050001'::uuid, '9" Hollow Blocks',         'masonry-products-9-hollow-blocks',   'a0000005-0000-0000-0000-000000000005', '9 inch hollow blocks', true, 1, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050002'::uuid, '6" Hollow Blocks',         'masonry-products-6-hollow-blocks',   'a0000005-0000-0000-0000-000000000005', '6 inch hollow blocks', true, 2, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050003'::uuid, 'Solid Blocks',             'masonry-products-solid-blocks',      'a0000005-0000-0000-0000-000000000005', 'Solid blocks', true, 3, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050004'::uuid, 'Interlocking Blocks',      'masonry-products-interlocking-blocks','a0000005-0000-0000-0000-000000000005', 'Interlocking blocks', true, 4, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050005'::uuid, 'Paving Blocks',            'masonry-products-paving-blocks',     'a0000005-0000-0000-0000-000000000005', 'Paving blocks', true, 5, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050006'::uuid, 'Kerbs',                    'masonry-products-kerbs',             'a0000005-0000-0000-0000-000000000005', 'Kerbs', true, 6, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050007'::uuid, 'Concrete Bricks',          'masonry-products-concrete-bricks',   'a0000005-0000-0000-0000-000000000005', 'Concrete bricks', true, 7, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050008'::uuid, 'Burnt Bricks',             'masonry-products-burnt-bricks',      'a0000005-0000-0000-0000-000000000005', 'Burnt bricks', true, 8, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),
  ('a0000005-0000-0000-0000-000000050009'::uuid, 'Sandcrete Bricks',         'masonry-products-sandcrete-bricks',  'a0000005-0000-0000-0000-000000000005', 'Sandcrete bricks', true, 9, 'Structure', 'material', 'piece', 0.00, 3.00, 'fixed', 55.00, NOW(), NOW()),

  -- Burnt Bricks subcategories
  ('a0000006-0000-0000-0000-000000060001'::uuid, 'Premium Bricks',           'burnt-bricks-premium-bricks',        'a0000006-0000-0000-0000-000000000006', 'Premium bricks', true, 1, 'Structure', 'material', 'piece', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),

  -- Ceiling Systems subcategories
  ('a0000007-0000-0000-0000-000000070001'::uuid, 'PVC Ceiling',              'ceiling-systems-pvc-ceiling',        'a0000007-0000-0000-0000-000000000007', 'PVC ceiling', true, 1, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070002'::uuid, 'POP',                      'ceiling-systems-pop',                'a0000007-0000-0000-0000-000000000007', 'POP', true, 2, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070003'::uuid, 'Gypsum Board',             'ceiling-systems-gypsum-board',       'a0000007-0000-0000-0000-000000000007', 'Gypsum board', true, 3, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070004'::uuid, 'Acoustic Ceiling',         'ceiling-systems-acoustic-ceiling',   'a0000007-0000-0000-0000-000000000007', 'Acoustic ceiling', true, 4, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070005'::uuid, 'Suspended Ceiling',        'ceiling-systems-suspended-ceiling',  'a0000007-0000-0000-0000-000000000007', 'Suspended ceiling', true, 5, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070006'::uuid, 'Wooden Ceiling',           'ceiling-systems-wooden-ceiling',     'a0000007-0000-0000-0000-000000000007', 'Wooden ceiling', true, 6, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070007'::uuid, 'Mineral Fibre Ceiling',    'ceiling-systems-mineral-fibre-ceiling','a0000007-0000-0000-0000-000000000007', 'Mineral fibre ceiling', true, 7, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000007-0000-0000-0000-000000070008'::uuid, 'Ceiling Accessories',      'ceiling-systems-ceiling-accessories','a0000007-0000-0000-0000-000000000007', 'Ceiling accessories', true, 8, 'Finishes', 'material', 'sheet', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),

  -- Tiles & Flooring subcategories
  ('a0000008-0000-0000-0000-000000080001'::uuid, 'Ceramic Tiles',            'tiles-flooring-ceramic-tiles',       'a0000008-0000-0000-0000-000000000008', 'Ceramic tiles', true, 1, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080002'::uuid, 'Porcelain Tiles',          'tiles-flooring-porcelain-tiles',     'a0000008-0000-0000-0000-000000000008', 'Porcelain tiles', true, 2, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080003'::uuid, 'Marble',                   'tiles-flooring-marble',              'a0000008-0000-0000-0000-000000000008', 'Marble', true, 3, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080004'::uuid, 'Granite Tiles',            'tiles-flooring-granite-tiles',       'a0000008-0000-0000-0000-000000000008', 'Granite tiles', true, 4, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080005'::uuid, 'Terrazzo',                 'tiles-flooring-terrazzo',            'a0000008-0000-0000-0000-000000000008', 'Terrazzo', true, 5, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080006'::uuid, 'Vinyl Flooring',           'tiles-flooring-vinyl-flooring',      'a0000008-0000-0000-0000-000000000008', 'Vinyl flooring', true, 6, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080007'::uuid, 'SPC Flooring',             'tiles-flooring-spc-flooring',        'a0000008-0000-0000-0000-000000000008', 'SPC flooring', true, 7, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080008'::uuid, 'Laminate Flooring',        'tiles-flooring-laminate-flooring',   'a0000008-0000-0000-0000-000000000008', 'Laminate flooring', true, 8, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080009'::uuid, 'Engineered Wood Flooring', 'tiles-flooring-engineered-wood-flooring', 'a0000008-0000-0000-0000-000000000008', 'Engineered wood flooring', true, 9, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080010'::uuid, 'Solid Hardwood Flooring',  'tiles-flooring-solid-hardwood-flooring', 'a0000008-0000-0000-0000-000000000008', 'Solid hardwood flooring', true, 10, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080011'::uuid, 'Bamboo Flooring',          'tiles-flooring-bamboo-flooring',     'a0000008-0000-0000-0000-000000000008', 'Bamboo flooring', true, 11, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080012'::uuid, 'Outdoor Decking',          'tiles-flooring-outdoor-decking',     'a0000008-0000-0000-0000-000000000008', 'Outdoor decking', true, 12, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000008-0000-0000-0000-000000080013'::uuid, 'Tile Adhesive, Grout & Spacers', 'tiles-flooring-tile-adhesive-grout-spacers', 'a0000008-0000-0000-0000-000000000008', 'Tile adhesive, grout and spacers', true, 13, 'Finishes', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),

  -- Timber & Engineered Wood subcategories
  ('a0000009-0000-0000-0000-000000090001'::uuid, 'Hardwood & Softwood',      'timber-engineered-wood-hardwood-softwood', 'a0000009-0000-0000-0000-000000000009', 'Hardwood and softwood', true, 1, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090002'::uuid, 'Marine Plywood & Commercial Plywood', 'timber-engineered-wood-marine-plywood-commercial-plywood', 'a0000009-0000-0000-0000-000000000009', 'Marine plywood and commercial plywood', true, 2, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090003'::uuid, 'MDF & HDF',                'timber-engineered-wood-mdf-hdf',     'a0000009-0000-0000-0000-000000000009', 'MDF and HDF', true, 3, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090004'::uuid, 'MFC & OSB',                'timber-engineered-wood-mfc-osb',     'a0000009-0000-0000-0000-000000000009', 'MFC and OSB', true, 4, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090005'::uuid, 'Particle Board & LVL',     'timber-engineered-wood-particle-board-lvl', 'a0000009-0000-0000-0000-000000000009', 'Particle board and LVL', true, 5, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090006'::uuid, 'Laminated & Finger Joint Timber', 'timber-engineered-wood-laminated-finger-joint-timber', 'a0000009-0000-0000-0000-000000000009', 'Laminated and finger joint timber', true, 6, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000009-0000-0000-0000-000000090007'::uuid, 'Bamboo Boards & Veneers',  'timber-engineered-wood-bamboo-boards-veneers', 'a0000009-0000-0000-0000-000000000009', 'Bamboo boards and veneers', true, 7, 'Finishes', 'material', 'sheet', 0.00, 4.00, 'percentage', NULL, NOW(), NOW()),

  -- Roofing Systems subcategories
  ('a0000010-0000-0000-0000-000000100001'::uuid, 'Long Span & Step Tile Aluminium', 'roofing-systems-long-span-step-tile-aluminium', 'a0000010-0000-0000-0000-000000000010', 'Long span and step tile aluminium', true, 1, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000100002'::uuid, 'Stone-Coated & Zinc Roofing', 'roofing-systems-stone-coated-zinc-roofing', 'a0000010-0000-0000-0000-000000000010', 'Stone-coated and zinc roofing', true, 2, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000100003'::uuid, 'Galvanized & Fibre Cement Roofing', 'roofing-systems-galvanized-fibre-cement-roofing', 'a0000010-0000-0000-0000-000000000010', 'Galvanized and fibre cement roofing', true, 3, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000100004'::uuid, 'Polycarbonate Roofing & Trusses', 'roofing-systems-polycarbonate-roofing-trusses', 'a0000010-0000-0000-0000-000000000010', 'Polycarbonate roofing and trusses', true, 4, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000100005'::uuid, 'Flashings, Gutters & Downpipes', 'roofing-systems-flashings-gutters-downpipes', 'a0000010-0000-0000-0000-000000000010', 'Flashings, gutters and downpipes', true, 5, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000010-0000-0000-0000-000000100006'::uuid, 'Roofing Screws, Ridge Caps & Valleys', 'roofing-systems-roofing-screws-ridge-caps-valleys', 'a0000010-0000-0000-0000-000000000010', 'Roofing screws, ridge caps and valleys', true, 6, 'Building Envelope', 'material', 'sheet', 0.00, 5.00, 'percentage', NULL, NOW(), NOW()),

  -- Plumbing Systems subcategories
  ('a0000011-0000-0000-0000-000000110001'::uuid, 'PVC, PPR & HDPE Pipes',    'plumbing-systems-pvc-ppr-hdpe-pipes', 'a0000011-0000-0000-0000-000000000011', 'PVC, PPR and HDPE pipes', true, 1, 'MEP', 'material', 'piece', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000011-0000-0000-0000-000000110002'::uuid, 'UPVC, CPVC & Drainage Pipes', 'plumbing-systems-upvc-cpvc-drainage-pipes', 'a0000011-0000-0000-0000-000000000011', 'UPVC, CPVC and drainage pipes', true, 2, 'MEP', 'material', 'piece', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000011-0000-0000-0000-000000110003'::uuid, 'Sewer & Pressure Pipes',   'plumbing-systems-sewer-pressure-pipes', 'a0000011-0000-0000-0000-000000000011', 'Sewer and pressure pipes', true, 3, 'MEP', 'material', 'piece', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000011-0000-0000-0000-000000110004'::uuid, 'Valves & Fittings',        'plumbing-systems-valves-fittings',    'a0000011-0000-0000-0000-000000000011', 'Valves and fittings', true, 4, 'MEP', 'material', 'piece', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000011-0000-0000-0000-000000110005'::uuid, 'Water Pumps, Tanks & Borehole Accessories', 'plumbing-systems-water-pumps-tanks-borehole-accessories', 'a0000011-0000-0000-0000-000000000011', 'Water pumps, tanks and borehole accessories', true, 5, 'MEP', 'material', 'piece', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),

  -- Sanitary Ware subcategories
  ('a0000012-0000-0000-0000-000000120001'::uuid, 'Water Closets & Wash Basins', 'sanitary-ware-water-closets-wash-basins', 'a0000012-0000-0000-0000-000000000012', 'Water closets and wash basins', true, 1, 'MEP', 'material', 'unit', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000012-0000-0000-0000-000000120002'::uuid, 'Urinals & Bathtubs',       'sanitary-ware-urinals-bathtubs',      'a0000012-0000-0000-0000-000000000012', 'Urinals and bathtubs', true, 2, 'MEP', 'material', 'unit', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000012-0000-0000-0000-000000120003'::uuid, 'Shower Systems & Cabinets','sanitary-ware-shower-systems-cabinets', 'a0000012-0000-0000-0000-000000000012', 'Shower systems and cabinets', true, 3, 'MEP', 'material', 'unit', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000012-0000-0000-0000-000000120004'::uuid, 'Mixers, Faucets & Sinks',  'sanitary-ware-mixers-faucets-sinks',  'a0000012-0000-0000-0000-000000000012', 'Mixers, faucets and sinks', true, 4, 'MEP', 'material', 'unit', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),

  -- Electrical Systems subcategories
  ('a0000013-0000-0000-0000-000000130001'::uuid, 'Cables & Wires',           'electrical-systems-cables-wires',     'a0000013-0000-0000-0000-000000000013', 'Cables and wires', true, 1, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000013-0000-0000-0000-000000130002'::uuid, 'Conduits & Trunking',      'electrical-systems-conduits-trunking','a0000013-0000-0000-0000-000000000013', 'Conduits and trunking', true, 2, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000013-0000-0000-0000-000000130003'::uuid, 'Switches, Sockets & Distribution Boards', 'electrical-systems-switches-sockets-distribution-boards', 'a0000013-0000-0000-0000-000000000013', 'Switches, sockets and distribution boards', true, 3, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000013-0000-0000-0000-000000130004'::uuid, 'Circuit Breakers & Lighting', 'electrical-systems-circuit-breakers-lighting', 'a0000013-0000-0000-0000-000000000013', 'Circuit breakers and lighting', true, 4, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000013-0000-0000-0000-000000130005'::uuid, 'Transformers, Solar Cables & Smart Controls', 'electrical-systems-transformers-solar-cables-smart-controls', 'a0000013-0000-0000-0000-000000000013', 'Transformers, solar cables and smart controls', true, 5, 'MEP', 'material', 'roll', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),

  -- Paints & Coatings subcategories
  ('a0000014-0000-0000-0000-000000140001'::uuid, 'Emulsion, Silk & Matt',    'paints-coatings-emulsion-silk-matt',  'a0000014-0000-0000-0000-000000000014', 'Emulsion, silk and matt', true, 1, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000014-0000-0000-0000-000000140002'::uuid, 'Satin, Gloss & Textured Coatings', 'paints-coatings-satin-gloss-textured-coatings', 'a0000014-0000-0000-0000-000000000014', 'Satin, gloss and textured coatings', true, 2, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000014-0000-0000-0000-000000140003'::uuid, 'Screeding Products & Primers', 'paints-coatings-screeding-products-primers', 'a0000014-0000-0000-0000-000000000014', 'Screeding products and primers', true, 3, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000014-0000-0000-0000-000000140004'::uuid, 'Sealers & Waterproof Coatings', 'paints-coatings-sealers-waterproof-coatings', 'a0000014-0000-0000-0000-000000000014', 'Sealers and waterproof coatings', true, 4, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000014-0000-0000-0000-000000140005'::uuid, 'Wood Finishes & Protective Coatings', 'paints-coatings-wood-finishes-protective-coatings', 'a0000014-0000-0000-0000-000000000014', 'Wood finishes and protective coatings', true, 5, 'Finishes', 'material', 'bucket', 0.00, 5.50, 'percentage', NULL, NOW(), NOW()),

  -- Doors, Windows & Facades subcategories
  ('a0000015-0000-0000-0000-000000150001'::uuid, 'Flush, Panel & Security Doors', 'doors-windows-facades-flush-panel-security-doors', 'a0000015-0000-0000-0000-000000000015', 'Flush, panel and security doors', true, 1, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150002'::uuid, 'Fire Rated & Sliding Doors', 'doors-windows-facades-fire-rated-sliding-doors', 'a0000015-0000-0000-0000-000000000015', 'Fire rated and sliding doors', true, 2, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150003'::uuid, 'Glass, Aluminium & Wooden Doors', 'doors-windows-facades-glass-aluminium-wooden-doors', 'a0000015-0000-0000-0000-000000000015', 'Glass, aluminium and wooden doors', true, 3, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150004'::uuid, 'Garage & Industrial Doors','doors-windows-facades-garage-industrial-doors', 'a0000015-0000-0000-0000-000000000015', 'Garage and industrial doors', true, 4, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150005'::uuid, 'Aluminium Profiles',        'doors-windows-facades-aluminium-profiles', 'a0000015-0000-0000-0000-000000000015', 'Aluminium profiles', true, 5, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150006'::uuid, 'Sliding & Casement Windows','doors-windows-facades-sliding-casement-windows', 'a0000015-0000-0000-0000-000000000015', 'Sliding and casement windows', true, 6, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150007'::uuid, 'Curtain Walls & ACP Cladding', 'doors-windows-facades-curtain-walls-acp-cladding', 'a0000015-0000-0000-0000-000000000015', 'Curtain walls and ACP cladding', true, 7, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150008'::uuid, 'Louvres, Skylights & Shop Fronts', 'doors-windows-facades-louvres-skylights-shop-fronts', 'a0000015-0000-0000-0000-000000000015', 'Louvres, skylights and shop fronts', true, 8, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000015-0000-0000-0000-000000150009'::uuid, 'Mosquito Nets & Installation Hardware', 'doors-windows-facades-mosquito-nets-installation-hardware', 'a0000015-0000-0000-0000-000000000015', 'Mosquito nets and installation hardware', true, 9, 'Building Envelope', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),

  -- Glass subcategories
  ('a0000016-0000-0000-0000-000000160001'::uuid, 'Float & Tempered Glass',   'glass-float-tempered-glass',          'a0000016-0000-0000-0000-000000000016', 'Float and tempered glass', true, 1, 'Building Envelope', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000016-0000-0000-0000-000000160002'::uuid, 'Laminated & Reflective Glass', 'glass-laminated-reflective-glass',    'a0000016-0000-0000-0000-000000000016', 'Laminated and reflective glass', true, 2, 'Building Envelope', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000016-0000-0000-0000-000000160003'::uuid, 'Low-E & Frosted Glass',    'glass-low-e-frosted-glass',           'a0000016-0000-0000-0000-000000000016', 'Low-E and frosted glass', true, 3, 'Building Envelope', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000016-0000-0000-0000-000000160004'::uuid, 'Mirror, Double & Decorative Glazing', 'glass-mirror-double-decorative-glazing', 'a0000016-0000-0000-0000-000000000016', 'Mirror, double and decorative glazing', true, 4, 'Building Envelope', 'material', 'm2', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),

  -- Smart Building Systems subcategories
  ('a0000017-0000-0000-0000-000000170001'::uuid, 'CCTV, Access Control & Intercoms', 'smart-building-systems-cctv-access-control-intercoms', 'a0000017-0000-0000-0000-000000000017', 'CCTV, access control and intercoms', true, 1, 'Building Services', 'material', 'system', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000017-0000-0000-0000-000000170002'::uuid, 'Video Doorbells & Fire Alarms', 'smart-building-systems-video-doorbells-fire-alarms', 'a0000017-0000-0000-0000-000000000017', 'Video doorbells and fire alarms', true, 2, 'Building Services', 'material', 'system', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000017-0000-0000-0000-000000170003'::uuid, 'Smart Lighting & Smart Home Hubs', 'smart-building-systems-smart-lighting-smart-home-hubs', 'a0000017-0000-0000-0000-000000000017', 'Smart lighting and smart home hubs', true, 3, 'Building Services', 'material', 'system', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000017-0000-0000-0000-000000170004'::uuid, 'Network Equipment & Automation Systems', 'smart-building-systems-network-equipment-automation-systems', 'a0000017-0000-0000-0000-000000000017', 'Network equipment and automation systems', true, 4, 'Building Services', 'material', 'system', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),

  -- Solar & Renewable Energy subcategories
  ('a0000018-0000-0000-0000-000000180001'::uuid, 'Solar Panels & Inverters', 'solar-renewable-energy-solar-panels-inverters', 'a0000018-0000-0000-0000-000000000018', 'Solar panels and inverters', true, 1, 'Building Services', 'material', 'system', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000018-0000-0000-0000-000000180002'::uuid, 'Lithium Batteries & Charge Controllers', 'solar-renewable-energy-lithium-batteries-charge-controllers', 'a0000018-0000-0000-0000-000000000018', 'Lithium batteries and charge controllers', true, 2, 'Building Services', 'material', 'system', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000018-0000-0000-0000-000000180003'::uuid, 'Mounting Systems & Solar Street Lights', 'solar-renewable-energy-mounting-systems-solar-street-lights', 'a0000018-0000-0000-0000-000000000018', 'Mounting systems and solar street lights', true, 3, 'Building Services', 'material', 'system', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),
  ('a0000018-0000-0000-0000-000000180004'::uuid, 'Hybrid Systems & Installation Accessories', 'solar-renewable-energy-hybrid-systems-installation-accessories', 'a0000018-0000-0000-0000-000000000018', 'Hybrid systems and installation accessories', true, 4, 'Building Services', 'material', 'system', 0.00, 4.50, 'percentage', NULL, NOW(), NOW()),

  -- Tools & Consumables subcategories
  ('a0000019-0000-0000-0000-000000190001'::uuid, 'Hand Tools',               'tools-consumables-hand-tools',        'a0000019-0000-0000-0000-000000000019', 'Hand tools', true, 1, 'Finishes', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000019-0000-0000-0000-000000190002'::uuid, 'PPE',                      'tools-consumables-ppe',               'a0000019-0000-0000-0000-000000000019', 'PPE', true, 2, 'Finishes', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),
  ('a0000019-0000-0000-0000-000000190003'::uuid, 'Power Tools',              'tools-consumables-power-tools',       'a0000019-0000-0000-0000-000000000019', 'Power tools', true, 3, 'Finishes', 'material', 'unit', 0.00, 6.00, 'percentage', NULL, NOW(), NOW()),

  -- Equipment & Site Services subcategories
  ('a0000020-0000-0000-0000-000000200001'::uuid, 'Excavators',               'equipment-site-services-excavators',  'a0000020-0000-0000-0000-000000000020', 'Excavators', true, 1, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200002'::uuid, 'Rollers',                  'equipment-site-services-rollers',     'a0000020-0000-0000-0000-000000000020', 'Rollers', true, 2, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200003'::uuid, 'Cranes',                   'equipment-site-services-cranes',      'a0000020-0000-0000-0000-000000000020', 'Cranes', true, 3, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200004'::uuid, 'Mixers',                   'equipment-site-services-mixers',      'a0000020-0000-0000-0000-000000000020', 'Mixers', true, 4, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200005'::uuid, 'Haulage Services',         'equipment-site-services-haulage-services', 'a0000020-0000-0000-0000-000000000020', 'Haulage services', true, 5, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200006'::uuid, 'Crane Services',           'equipment-site-services-crane-services','a0000020-0000-0000-0000-000000000020', 'Crane services', true, 6, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200007'::uuid, 'Borehole Services',        'equipment-site-services-borehole-services', 'a0000020-0000-0000-0000-000000000020', 'Borehole services', true, 7, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW()),
  ('a0000020-0000-0000-0000-000000200008'::uuid, 'Testing Services',         'equipment-site-services-testing-services', 'a0000020-0000-0000-0000-000000000020', 'Testing services', true, 8, 'External Works', 'material', 'contract', 0.00, 6.50, 'service', NULL, NOW(), NOW())
) AS s(id, name, slug, parent_id, description, is_active, display_order, division, material_type, default_unit, waste_factor, platform_margin, fee_model, fee_fixed, created_at, updated_at)
WHERE NOT EXISTS (SELECT 1 FROM categories x WHERE x.slug = s.slug);

COMMIT;