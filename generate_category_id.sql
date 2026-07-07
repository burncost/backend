INSERT INTO categories (
    id,
    name,
    slug,
    description,
    parent_id,
    is_active,
    display_order,
    division,
    material_type,
    default_unit,
    waste_factor,
    created_at,
    updated_at
)
VALUES

(
    gen_random_uuid(),
    'Cement',
    'cement',
    'Cement products for construction, concrete works and masonry.',
    NULL,
    TRUE,
    1,
    'Structure',
    'material',
    'bag',
    5.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Steel & Iron',
    'steel-iron',
    'Steel rods, reinforcement bars, structural steel and iron products.',
    NULL,
    TRUE,
    2,
    'Structure',
    'material',
    'tonne',
    3.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Blocks & Bricks',
    'blocks-bricks',
    'Concrete blocks, clay bricks, interlocking blocks and masonry products.',
    NULL,
    TRUE,
    3,
    'Structure',
    'material',
    'piece',
    5.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Roofing Materials',
    'roofing-materials',
    'Roofing sheets, tiles, ridge caps and related roofing accessories.',
    NULL,
    TRUE,
    4,
    'Structure',
    'material',
    'm²',
    10.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Plumbing Supplies',
    'plumbing-supplies',
    'Pipes, fittings, sanitary wares, valves and plumbing accessories.',
    NULL,
    TRUE,
    5,
    'MEP',
    'material',
    'piece',
    5.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Electrical Supplies',
    'electrical-supplies',
    'Electrical cables, switches, sockets, lighting and electrical accessories.',
    NULL,
    TRUE,
    6,
    'MEP',
    'material',
    'piece',
    5.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Paint & Coating',
    'paint-coating',
    'Paints, primers, coatings, varnishes and finishing products.',
    NULL,
    TRUE,
    7,
    'Finishes',
    'material',
    'litre',
    10.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Wood & Timber',
    'wood-timber',
    'Timber, plywood, boards, lumber and wood-based construction materials.',
    NULL,
    TRUE,
    8,
    'Finishes',
    'material',
    'm³',
    5.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Sand & Gravel',
    'sand-gravel',
    'Sharp sand, plaster sand, granite, gravel and aggregates.',
    NULL,
    TRUE,
    9,
    'Structure',
    'material',
    'tonne',
    10.00,
    NOW(),
    NOW()
),

(
    gen_random_uuid(),
    'Hardware & Tools',
    'hardware-tools',
    'Construction tools, fasteners, hardware and site equipment.',
    NULL,
    TRUE,
    10,
    'General',
    'material',
    'piece',
    3.00,
    NOW(),
    NOW()
);
