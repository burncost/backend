-- =============================================================
-- BURNCOST CATEGORY SEED (BPCS v1.0)
-- Truncates existing data and inserts 20 strategic categories
-- with subcategories and platform margins.
-- =============================================================
BEGIN;

-- Wipe existing categories (CASCADE handles children)
TRUNCATE TABLE categories RESTART IDENTITY CASCADE;

-- =============================================================
-- PARENT CATEGORIES
-- =============================================================
INSERT INTO categories (id, name, slug, division, default_unit, platform_margin, fee_model, fee_fixed, display_order, description)
VALUES
('a0000001-0000-0000-0000-000000000001', 'Cement', 'cement', 'Structure', 'bag', 1.50, 'fixed', 200.00, 1, 'Cement - CQI, Fee model: fixed'),
('a0000002-0000-0000-0000-000000000002', 'Reinforcement Steel', 'reinforcement-steel', 'Structure', 'tonne', 1.25, 'percentage', NULL, 2, 'Steel - SQI, Fee model: percentage (1-1.5%)'),
('a0000003-0000-0000-0000-000000000003', 'Fine Aggregates', 'fine-aggregates', 'Structure', 'trip', 5.00, 'fixed', 10000.00, 3, 'Sand, laterite - Fixed fee per trip'),
('a0000004-0000-0000-0000-000000000004', 'Coarse Aggregates', 'coarse-aggregates', 'Structure', 'trip', 5.00, 'fixed', 19000.00, 4, 'Granite, stone - Fixed fee per trip'),
('a0000005-0000-0000-0000-000000000005', 'Masonry Products', 'masonry-products', 'Structure', 'piece', 3.00, 'fixed', 55.00, 5, 'Blocks - BQI, Per unit fee'),
('a0000006-0000-0000-0000-000000000006', 'Burnt Bricks', 'burnt-bricks', 'Structure', 'piece', 5.00, 'percentage', NULL, 6, 'Premium bricks - Percentage (3-7%)'),
('a0000007-0000-0000-0000-000000000007', 'Ceiling Systems', 'ceiling-systems', 'Finishes', 'sheet', 5.50, 'percentage', NULL, 7, 'PVC, POP, Gypsum - Percentage (3-8%)'),
('a0000008-0000-0000-0000-000000000008', 'Tiles & Flooring', 'tiles-flooring', 'Finishes', 'm2', 6.00, 'percentage', NULL, 8, 'FQI - Percentage (4-8%)'),
('a0000009-0000-0000-0000-000000000009', 'Timber & Engineered Wood', 'timber-engineered-wood', 'Finishes', 'sheet', 4.00, 'percentage', NULL, 9, 'WQI - Percentage (2-6%)'),
('a0000010-0000-0000-0000-000000000010', 'Roofing Systems', 'roofing-systems', 'Building Envelope', 'sheet', 5.00, 'percentage', NULL, 10, 'RQI - Percentage (3-7%)'),
('a0000011-0000-0000-0000-000000000011', 'Plumbing Systems', 'plumbing-systems', 'MEP', 'piece', 4.50, 'percentage', NULL, 11, 'PQI - Percentage (3-6%)'),
('a0000012-0000-0000-0000-000000000012', 'Sanitary Ware', 'sanitary-ware', 'MEP', 'unit', 5.50, 'percentage', NULL, 12, 'WCs, basins - Percentage (4-7%)'),
('a0000013-0000-0000-0000-000000000013', 'Electrical Systems', 'electrical-systems', 'MEP', 'roll', 4.50, 'percentage', NULL, 13, 'EQI - Percentage (3-6%)'),
('a0000014-0000-0000-0000-000000000014', 'Paints & Coatings', 'paints-coatings', 'Finishes', 'bucket', 5.50, 'percentage', NULL, 14, 'PQI - Percentage (3-8%)'),
('a0000015-0000-0000-0000-000000000015', 'Doors, Windows & Facades', 'doors-windows-facades', 'Building Envelope', 'unit', 6.00, 'percentage', NULL, 15, 'DFQI - Percentage (4-8%)'),
('a0000016-0000-0000-0000-000000000016', 'Glass', 'glass', 'Building Envelope', 'm2', 6.00, 'percentage', NULL, 16, 'Percentage (cluster: 4-8%)'),
('a0000017-0000-0000-0000-000000000017', 'Smart Building Systems', 'smart-building-systems', 'Building Services', 'system', 6.00, 'percentage', NULL, 17, 'CCTV, access - Percentage (4-8%)'),
('a0000018-0000-0000-0000-000000000018', 'Solar & Renewable Energy', 'solar-renewable-energy', 'Building Services', 'system', 4.50, 'percentage', NULL, 18, 'SQI - Percentage (3-6%)'),
('a0000019-0000-0000-0000-000000000019', 'Tools & Consumables', 'tools-consumables', 'Finishes', 'unit', 6.00, 'percentage', NULL, 19, 'Percentage (4-8%)'),
('a0000020-0000-0000-0000-000000000020', 'Equipment & Site Services', 'equipment-site-services', 'External Works', 'contract', 6.50, 'service', NULL, 20, 'Service fee (5-8%)');

-- =============================================================
-- SUBCATEGORIES
-- =============================================================

-- CAT-001 Cement
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000001-0000-0000-0000-000000000001', 'General Purpose Cement', 'cement-general-purpose', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000002', 'High Strength Cement', 'cement-high-strength', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000003', 'Sulphate Resistant Cement', 'cement-sulphate-resistant', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000004', 'White Cement', 'cement-white', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000005', 'Masonry Cement', 'cement-masonry', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000006', 'Oil Well Cement', 'cement-oil-well', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000007', 'Bulk Cement', 'cement-bulk', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000008', 'Ready-Mix Cement', 'cement-ready-mix', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50),
('b0000001-0000-0000-0000-000000000009', 'Cement Additives', 'cement-additives', 'a0000001-0000-0000-0000-000000000001', 'Structure', 'bag', 1.50);

-- CAT-002 Reinforcement Steel
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000002-0000-0000-0000-000000000001', 'TMT Rebars', 'steel-tmt-rebars', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000002', 'Mild Steel Bars', 'steel-mild-steel-bars', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000003', 'High Yield Bars', 'steel-high-yield-bars', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000004', 'Wire Rods', 'steel-wire-rods', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000005', 'Binding Wire', 'steel-binding-wire', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000006', 'Steel Mesh', 'steel-mesh', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000007', 'Steel Plates', 'steel-plates', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000008', 'Structural Steel', 'steel-structural', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000009', 'Hollow Sections', 'steel-hollow-sections', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25),
('b0000002-0000-0000-0000-000000000010', 'Angles & Channels', 'steel-angles-channels', 'a0000002-0000-0000-0000-000000000002', 'Structure', 'tonne', 1.25);

-- CAT-003 Fine Aggregates
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000003-0000-0000-0000-000000000001', 'Sharp Sand', 'aggregates-sharp-sand', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000002', 'Plaster Sand', 'aggregates-plaster-sand', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000003', 'Filling Sand', 'aggregates-filling-sand', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000004', 'White Sand', 'aggregates-white-sand', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000005', 'River Sand', 'aggregates-river-sand', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000006', 'Laterite', 'aggregates-laterite', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000007', 'Top Soil', 'aggregates-top-soil', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000008', 'Hardcore', 'aggregates-hardcore', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00),
('b0000003-0000-0000-0000-000000000009', 'Quarry Dust', 'aggregates-quarry-dust', 'a0000003-0000-0000-0000-000000000003', 'Structure', 'trip', 5.00);

-- CAT-004 Coarse Aggregates
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000004-0000-0000-0000-000000000001', 'Granite 1/2"', 'granite-12', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000002', 'Granite 3/4"', 'granite-34', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000003', 'Granite 1"', 'granite-1', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000004', 'Granite 1-1/2"', 'granite-1-12', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000005', 'Stone Dust', 'granite-stone-dust', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000006', 'Crusher Run', 'granite-crusher-run', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000007', 'Gravel', 'granite-gravel', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000008', 'Chippings', 'granite-chippings', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00),
('b0000004-0000-0000-0000-000000000009', 'Base Course', 'granite-base-course', 'a0000004-0000-0000-0000-000000000004', 'Structure', 'trip', 5.00);

-- CAT-005 Masonry Products
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000005-0000-0000-0000-000000000001', '9" Hollow Blocks', 'blocks-9-hollow', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000002', '6" Hollow Blocks', 'blocks-6-hollow', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000003', 'Solid Blocks', 'blocks-solid', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000004', 'Interlocking Blocks', 'blocks-interlocking', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000005', 'Paving Blocks', 'blocks-paving', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000006', 'Kerbs', 'blocks-kerbs', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000007', 'Concrete Bricks', 'blocks-concrete-bricks', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000008', 'Burnt Bricks', 'blocks-burnt-bricks', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00),
('b0000005-0000-0000-0000-000000000009', 'Sandcrete Bricks', 'blocks-sandcrete', 'a0000005-0000-0000-0000-000000000005', 'Structure', 'piece', 3.00);

-- CAT-006 Burnt Bricks
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000006-0000-0000-0000-000000000001', 'Premium Bricks', 'bricks-premium', 'a0000006-0000-0000-0000-000000000006', 'Structure', 'piece', 5.00);

-- CAT-007 Ceiling Systems
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000007-0000-0000-0000-000000000001', 'PVC Ceiling', 'ceiling-pvc', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000002', 'POP', 'ceiling-pop', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000003', 'Gypsum Board', 'ceiling-gypsum', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000004', 'Acoustic Ceiling', 'ceiling-acoustic', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000005', 'Suspended Ceiling', 'ceiling-suspended', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000006', 'Wooden Ceiling', 'ceiling-wooden', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000007', 'Mineral Fibre Ceiling', 'ceiling-mineral-fibre', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50),
('b0000007-0000-0000-0000-000000000008', 'Ceiling Accessories', 'ceiling-accessories', 'a0000007-0000-0000-0000-000000000007', 'Finishes', 'sheet', 5.50);

-- CAT-008 Tiles & Flooring
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000008-0000-0000-0000-000000000001', 'Ceramic Tiles', 'tiles-ceramic', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000002', 'Porcelain Tiles', 'tiles-porcelain', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000003', 'Marble', 'tiles-marble', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000004', 'Granite Tiles', 'tiles-granite', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000005', 'Terrazzo', 'tiles-terrazzo', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000006', 'Vinyl Flooring', 'tiles-vinyl', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000007', 'SPC Flooring', 'tiles-spc', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000008', 'Laminate Flooring', 'tiles-laminate', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000009', 'Engineered Wood Flooring', 'tiles-engineered-wood', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000010', 'Solid Hardwood Flooring', 'tiles-hardwood', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000011', 'Bamboo Flooring', 'tiles-bamboo', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000012', 'Outdoor Decking', 'tiles-outdoor-decking', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00),
('b0000008-0000-0000-0000-000000000013', 'Tile Adhesive, Grout & Spacers', 'tiles-adhesive-grout', 'a0000008-0000-0000-0000-000000000008', 'Finishes', 'm2', 6.00);

-- CAT-009 Timber
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000009-0000-0000-0000-000000000001', 'Hardwood & Softwood', 'timber-hardwood-softwood', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000002', 'Marine Plywood & Commercial Plywood', 'timber-plywood', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000003', 'MDF & HDF', 'timber-mdf-hdf', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000004', 'MFC & OSB', 'timber-mfc-osb', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000005', 'Particle Board & LVL', 'timber-particle-board-lvl', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000006', 'Laminated & Finger Joint Timber', 'timber-laminated', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00),
('b0000009-0000-0000-0000-000000000007', 'Bamboo Boards & Veneers', 'timber-bamboo-veneers', 'a0000009-0000-0000-0000-000000000009', 'Finishes', 'sheet', 4.00);

-- CAT-010 Roofing
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000010-0000-0000-0000-000000000001', 'Long Span & Step Tile Aluminium', 'roofing-long-span', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00),
('b0000010-0000-0000-0000-000000000002', 'Stone-Coated & Zinc Roofing', 'roofing-stone-coated', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00),
('b0000010-0000-0000-0000-000000000003', 'Galvanized & Fibre Cement Roofing', 'roofing-galvanized', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00),
('b0000010-0000-0000-0000-000000000004', 'Polycarbonate Roofing & Trusses', 'roofing-polycarbonate', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00),
('b0000010-0000-0000-0000-000000000005', 'Flashings, Gutters & Downpipes', 'roofing-flashings', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00),
('b0000010-0000-0000-0000-000000000006', 'Roofing Screws, Ridge Caps & Valleys', 'roofing-accessories', 'a0000010-0000-0000-0000-000000000010', 'Building Envelope', 'sheet', 5.00);

-- CAT-011 Plumbing
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000011-0000-0000-0000-000000000001', 'PVC, PPR & HDPE Pipes', 'plumbing-pipes', 'a0000011-0000-0000-0000-000000000011', 'MEP', 'piece', 4.50),
('b0000011-0000-0000-0000-000000000002', 'UPVC, CPVC & Drainage Pipes', 'plumbing-drainage', 'a0000011-0000-0000-0000-000000000011', 'MEP', 'piece', 4.50),
('b0000011-0000-0000-0000-000000000003', 'Sewer & Pressure Pipes', 'plumbing-sewer', 'a0000011-0000-0000-0000-000000000011', 'MEP', 'piece', 4.50),
('b0000011-0000-0000-0000-000000000004', 'Valves & Fittings', 'plumbing-valves', 'a0000011-0000-0000-0000-000000000011', 'MEP', 'piece', 4.50),
('b0000011-0000-0000-0000-000000000005', 'Water Pumps, Tanks & Borehole Accessories', 'plumbing-pumps-tanks', 'a0000011-0000-0000-0000-000000000011', 'MEP', 'piece', 4.50);

-- CAT-012 Sanitary Ware
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000012-0000-0000-0000-000000000001', 'Water Closets & Wash Basins', 'sanitary-wc-basins', 'a0000012-0000-0000-0000-000000000012', 'MEP', 'unit', 5.50),
('b0000012-0000-0000-0000-000000000002', 'Urinals & Bathtubs', 'sanitary-urinals-bathtubs', 'a0000012-0000-0000-0000-000000000012', 'MEP', 'unit', 5.50),
('b0000012-0000-0000-0000-000000000003', 'Shower Systems & Cabinets', 'sanitary-showers', 'a0000012-0000-0000-0000-000000000012', 'MEP', 'unit', 5.50),
('b0000012-0000-0000-0000-000000000004', 'Mixers, Faucets & Sinks', 'sanitary-mixers-faucets', 'a0000012-0000-0000-0000-000000000012', 'MEP', 'unit', 5.50);

-- CAT-013 Electrical
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000013-0000-0000-0000-000000000001', 'Cables & Wires', 'electrical-cables', 'a0000013-0000-0000-0000-000000000013', 'MEP', 'roll', 4.50),
('b0000013-0000-0000-0000-000000000002', 'Conduits & Trunking', 'electrical-conduits', 'a0000013-0000-0000-0000-000000000013', 'MEP', 'roll', 4.50),
('b0000013-0000-0000-0000-000000000003', 'Switches, Sockets & Distribution Boards', 'electrical-switches', 'a0000013-0000-0000-0000-000000000013', 'MEP', 'roll', 4.50),
('b0000013-0000-0000-0000-000000000004', 'Circuit Breakers & Lighting', 'electrical-breakers-lighting', 'a0000013-0000-0000-0000-000000000013', 'MEP', 'roll', 4.50),
('b0000013-0000-0000-0000-000000000005', 'Transformers, Solar Cables & Smart Controls', 'electrical-transformers', 'a0000013-0000-0000-0000-000000000013', 'MEP', 'roll', 4.50);

-- CAT-014 Paints
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000014-0000-0000-0000-000000000001', 'Emulsion, Silk & Matt', 'paints-emulsion', 'a0000014-0000-0000-0000-000000000014', 'Finishes', 'bucket', 5.50),
('b0000014-0000-0000-0000-000000000002', 'Satin, Gloss & Textured Coatings', 'paints-satin-gloss', 'a0000014-0000-0000-0000-000000000014', 'Finishes', 'bucket', 5.50),
('b0000014-0000-0000-0000-000000000003', 'Screeding Products & Primers', 'paints-screeding', 'a0000014-0000-0000-0000-000000000014', 'Finishes', 'bucket', 5.50),
('b0000014-0000-0000-0000-000000000004', 'Sealers & Waterproof Coatings', 'paints-sealers', 'a0000014-0000-0000-0000-000000000014', 'Finishes', 'bucket', 5.50),
('b0000014-0000-0000-0000-000000000005', 'Wood Finishes & Protective Coatings', 'paints-wood-finishes', 'a0000014-0000-0000-0000-000000000014', 'Finishes', 'bucket', 5.50);

-- CAT-015 Doors, Windows, Facades
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000015-0000-0000-0000-000000000001', 'Flush, Panel & Security Doors', 'doors-flush-panel', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000002', 'Fire Rated & Sliding Doors', 'doors-fire-sliding', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000003', 'Glass, Aluminium & Wooden Doors', 'doors-glass-aluminium', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000004', 'Garage & Industrial Doors', 'doors-garage', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000005', 'Aluminium Profiles', 'facade-aluminium', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000006', 'Sliding & Casement Windows', 'windows-sliding', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000007', 'Curtain Walls & ACP Cladding', 'facade-curtain-walls', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000008', 'Louvres, Skylights & Shop Fronts', 'windows-louvres', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00),
('b0000015-0000-0000-0000-000000000009', 'Mosquito Nets & Installation Hardware', 'windows-mosquito-nets', 'a0000015-0000-0000-0000-000000000015', 'Building Envelope', 'unit', 6.00);

-- CAT-016 Glass
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000016-0000-0000-0000-000000000001', 'Float & Tempered Glass', 'glass-float-tempered', 'a0000016-0000-0000-0000-000000000016', 'Building Envelope', 'm2', 6.00),
('b0000016-0000-0000-0000-000000000002', 'Laminated & Reflective Glass', 'glass-laminated', 'a0000016-0000-0000-0000-000000000016', 'Building Envelope', 'm2', 6.00),
('b0000016-0000-0000-0000-000000000003', 'Low-E & Frosted Glass', 'glass-low-e', 'a0000016-0000-0000-0000-000000000016', 'Building Envelope', 'm2', 6.00),
('b0000016-0000-0000-0000-000000000004', 'Mirror, Double & Decorative Glazing', 'glass-mirror', 'a0000016-0000-0000-0000-000000000016', 'Building Envelope', 'm2', 6.00);

-- CAT-017 Smart Building Systems
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000017-0000-0000-0000-000000000001', 'CCTV, Access Control & Intercoms', 'smart-cctv', 'a0000017-0000-0000-0000-000000000017', 'Building Services', 'system', 6.00),
('b0000017-0000-0000-0000-000000000002', 'Video Doorbells & Fire Alarms', 'smart-doorbells-alarms', 'a0000017-0000-0000-0000-000000000017', 'Building Services', 'system', 6.00),
('b0000017-0000-0000-0000-000000000003', 'Smart Lighting & Smart Home Hubs', 'smart-lighting-hubs', 'a0000017-0000-0000-0000-000000000017', 'Building Services', 'system', 6.00),
('b0000017-0000-0000-0000-000000000004', 'Network Equipment & Automation Systems', 'smart-network', 'a0000017-0000-0000-0000-000000000017', 'Building Services', 'system', 6.00);

-- CAT-018 Solar
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000018-0000-0000-0000-000000000001', 'Solar Panels & Inverters', 'solar-panels', 'a0000018-0000-0000-0000-000000000018', 'Building Services', 'system', 4.50),
('b0000018-0000-0000-0000-000000000002', 'Lithium Batteries & Charge Controllers', 'solar-batteries', 'a0000018-0000-0000-0000-000000000018', 'Building Services', 'system', 4.50),
('b0000018-0000-0000-0000-000000000003', 'Mounting Systems & Solar Street Lights', 'solar-mounting', 'a0000018-0000-0000-0000-000000000018', 'Building Services', 'system', 4.50),
('b0000018-0000-0000-0000-000000000004', 'Hybrid Systems & Installation Accessories', 'solar-hybrid', 'a0000018-0000-0000-0000-000000000018', 'Building Services', 'system', 4.50);

-- CAT-019 Tools
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000019-0000-0000-0000-000000000001', 'Hand Tools', 'tools-hand', 'a0000019-0000-0000-0000-000000000019', 'Finishes', 'unit', 6.00),
('b0000019-0000-0000-0000-000000000002', 'PPE', 'tools-ppe', 'a0000019-0000-0000-0000-000000000019', 'Finishes', 'unit', 6.00),
('b0000019-0000-0000-0000-000000000003', 'Power Tools', 'tools-power', 'a0000019-0000-0000-0000-000000000019', 'Finishes', 'unit', 6.00);

-- CAT-020 Equipment & Site Services
INSERT INTO categories (id, name, slug, parent_id, division, default_unit, platform_margin) VALUES
('b0000020-0000-0000-0000-000000000001', 'Excavators', 'equipment-excavators', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000002', 'Rollers', 'equipment-rollers', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000003', 'Cranes', 'equipment-cranes', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000004', 'Mixers', 'equipment-mixers', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000005', 'Haulage Services', 'equipment-haulage', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000006', 'Crane Services', 'equipment-crane-services', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000007', 'Borehole Services', 'equipment-borehole', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50),
('b0000020-0000-0000-0000-000000000008', 'Testing Services', 'equipment-testing', 'a0000020-0000-0000-0000-000000000020', 'External Works', 'contract', 6.50);

COMMIT;