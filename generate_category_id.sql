INSERT INTO categories (
    id,
    name,
    slug,
    description,
    parent_id,
    is_active,
    display_order,
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
    NOW(),
    NOW()
);