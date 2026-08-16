-- =============================================================
-- BURNCOST DATABASE SCHEMA — DDL Script
-- Generated from SQLAlchemy models in Backend/app/models/
-- Run after: alembic upgrade head  (or standalone)
-- =============================================================

-- user_role must include all roles in Backend/app/models/user.py (UserRole):
-- customer, vendor, admin, super_admin, manager, support, marketing
CREATE TYPE user_role AS ENUM ('customer', 'vendor', 'admin', 'super_admin', 'manager', 'support', 'marketing');
-- wait for success, then:
CREATE TYPE user_status AS ENUM ('active', 'suspended', 'pending_verification', 'deactivated');
-- wait for success, then:
CREATE TYPE transactiontype AS ENUM ('purchase', 'consumption', 'refund', 'free_tier', 'expiry');
-- then run the rest of the script (table creation)

-- =============================================================
-- 1. USERS
-- =============================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role user_role NOT NULL DEFAULT 'customer',
    status user_status NOT NULL DEFAULT 'pending_verification',
    email_verified BOOLEAN DEFAULT FALSE,
    phone_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_phone_number ON users (phone_number);

-- =============================================================
-- 2. USER PROFILES
-- =============================================================
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    first_name VARCHAR(100) NOT NULL,
    other_name VARCHAR(100),
    last_name VARCHAR(100) NOT NULL,
    business_name VARCHAR(255),
    location VARCHAR(100),
    avatar_url TEXT,
    date_of_birth DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 3. CATEGORIES (self-referencing hierarchy)
-- =============================================================
CREATE TABLE IF NOT EXISTS categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    parent_id UUID REFERENCES categories(id) ON DELETE CASCADE,
    description TEXT,
    image_url TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    display_order INTEGER DEFAULT 0,
    division VARCHAR(100),
    material_type VARCHAR(50) DEFAULT 'material',
    default_unit VARCHAR(50),
    waste_factor NUMERIC(5,2) DEFAULT 0.00,
    platform_margin NUMERIC(5,2) DEFAULT 5.00,
    fee_model VARCHAR(20) DEFAULT 'percentage',
    fee_fixed NUMERIC(12,2),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_categories_slug ON categories (slug);
CREATE INDEX IF NOT EXISTS ix_categories_is_active ON categories (is_active);
CREATE INDEX IF NOT EXISTS ix_categories_division ON categories (division);

-- =============================================================
-- 4. BRANDS
-- =============================================================
CREATE TABLE IF NOT EXISTS brands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) UNIQUE NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    logo_url TEXT,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_brands_slug ON brands (slug);

-- =============================================================
-- 5. VENDORS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_name VARCHAR(255) NOT NULL,
    business_type VARCHAR(255) NOT NULL,
    city VARCHAR(255) NOT NULL,
    state VARCHAR(255) NOT NULL,
    business_address VARCHAR(255) NOT NULL,
    cac_business_registration_number VARCHAR(100) UNIQUE,
    tax_identification_number VARCHAR(50),
    verification_status VARCHAR(20) DEFAULT 'pending',
    verification_tier VARCHAR(20) NOT NULL DEFAULT 'cac_only',
    transaction_volume NUMERIC(15,2) DEFAULT 0.00,
    verification_date TIMESTAMP,
    verified_by UUID REFERENCES users(id),
    commission_rate NUMERIC(5,2) DEFAULT 10.00,
    rating NUMERIC(3,2) DEFAULT 0.00,
    total_reviews INTEGER DEFAULT 0,
    total_sales NUMERIC(15,2) DEFAULT 0.00,
    is_featured BOOLEAN DEFAULT FALSE,
    business_image VARCHAR(500),
    delivery_time VARCHAR(100) DEFAULT '1-3 Days',
    response_time VARCHAR(100) DEFAULT '< 1 hour',
    specializations TEXT[] DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 6. VENDOR ADDRESSES
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    address_type VARCHAR(20) NOT NULL,
    address_line1 TEXT NOT NULL,
    address_line2 TEXT,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(100) NOT NULL,
    lga VARCHAR(100),
    postal_code VARCHAR(20),
    landmark TEXT,
    latitude NUMERIC(10,8),
    longitude NUMERIC(11,8),
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 7. VENDOR BANK ACCOUNTS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_bank_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    bank_name VARCHAR(100) NOT NULL,
    account_number VARCHAR(20) NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    bank_code VARCHAR(10),
    is_primary BOOLEAN DEFAULT FALSE,
    verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 8. VENDOR DOCUMENTS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    document_type VARCHAR(50) NOT NULL,
    document_url TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT NOW(),
    verified BOOLEAN DEFAULT FALSE,
    tier VARCHAR(20) DEFAULT 'cac_only',
    review_status VARCHAR(20) DEFAULT 'pending',
    reviewed_by UUID REFERENCES users(id),
    reviewed_at TIMESTAMP
);

-- =============================================================
-- 9. VENDOR VERIFICATION TIERS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_verification_tiers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tier_code VARCHAR(20) UNIQUE NOT NULL,
    display_name VARCHAR(50) NOT NULL,
    sort_order INTEGER DEFAULT 1,
    transaction_cap NUMERIC(16,2) NOT NULL DEFAULT 5000000,
    commission_rate NUMERIC(5,2) NOT NULL DEFAULT 10.00,
    required_document_types JSONB DEFAULT '[]'::jsonb,
    requires_manual_review BOOLEAN DEFAULT FALSE,
    perks JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_vendor_verification_tiers_tier_code ON vendor_verification_tiers (tier_code);

-- =============================================================
-- 10. VENDOR DRAFTS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_step VARCHAR(50) DEFAULT 'business-info',
    business_info JSONB DEFAULT '{}',
    banking_info JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 11. PRODUCTS
-- =============================================================
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    category_id UUID NOT NULL REFERENCES categories(id),
    brand_id UUID REFERENCES brands(id),
    name VARCHAR(500) NOT NULL,
    slug VARCHAR(500) UNIQUE NOT NULL,
    sku VARCHAR(100) UNIQUE NOT NULL,
    description TEXT,
    short_description TEXT,
    status VARCHAR(20) DEFAULT 'draft',
    base_price NUMERIC(15,2) NOT NULL,
    discount_price NUMERIC(15,2),
    discount_percentage NUMERIC(5,2),
    cost_price NUMERIC(15,2),
    quantity INTEGER DEFAULT 0,
    low_stock_threshold INTEGER DEFAULT 10,
    allow_backorder BOOLEAN DEFAULT FALSE,
    weight NUMERIC(10,2),
    length NUMERIC(10,2),
    width NUMERIC(10,2),
    height NUMERIC(10,2),
    unit_of_measure VARCHAR(50) DEFAULT 'piece',
    minimum_order_quantity INTEGER DEFAULT 1,
    shipping_fee NUMERIC(15,2) DEFAULT 0.00,
    estimated_delivery_days INTEGER DEFAULT 5,
    free_shipping_threshold INTEGER DEFAULT 0,
    meta_title VARCHAR(255),
    meta_description TEXT,
    meta_keywords TEXT,
    view_count INTEGER DEFAULT 0,
    sales_count INTEGER DEFAULT 0,
    rating NUMERIC(3,2) DEFAULT 0.00,
    review_count INTEGER DEFAULT 0,
    is_featured BOOLEAN DEFAULT FALSE,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    published_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_products_name ON products (name);
CREATE INDEX IF NOT EXISTS ix_products_slug ON products (slug);
CREATE INDEX IF NOT EXISTS ix_products_sku ON products (sku);
CREATE INDEX IF NOT EXISTS ix_products_status ON products (status);
CREATE INDEX IF NOT EXISTS ix_products_created_at ON products (created_at);

-- =============================================================
-- 12. PRODUCT IMAGES
-- =============================================================
CREATE TABLE IF NOT EXISTS product_images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    image_url TEXT NOT NULL,
    alt_text VARCHAR(255),
    display_order INTEGER DEFAULT 0,
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_product_images_product_id ON product_images (product_id);

-- =============================================================
-- 13. PRODUCT SPECIFICATIONS
-- =============================================================
CREATE TABLE IF NOT EXISTS product_specifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    spec_name VARCHAR(255) NOT NULL,
    spec_value TEXT NOT NULL,
    display_order INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_product_specifications_product_id ON product_specifications (product_id);

-- =============================================================
-- 14. PRODUCT VARIANTS
-- =============================================================
CREATE TABLE IF NOT EXISTS product_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku VARCHAR(100) UNIQUE NOT NULL,
    variant_name VARCHAR(255) NOT NULL,
    price_adjustment NUMERIC(15,2) DEFAULT 0.00,
    quantity INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_product_variants_product_id ON product_variants (product_id);

-- =============================================================
-- 15. PRODUCT REVIEWS
-- =============================================================
CREATE TABLE IF NOT EXISTS product_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    order_id UUID,  -- FK added below after orders table exists
    reviewer_name VARCHAR(100),
    rating INTEGER NOT NULL,
    title VARCHAR(255),
    comment TEXT,
    is_verified_purchase BOOLEAN DEFAULT FALSE,
    is_approved BOOLEAN DEFAULT FALSE,
    approved_by UUID REFERENCES users(id),
    approved_at TIMESTAMP,
    helpful_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_product_reviews_product_id ON product_reviews (product_id);
CREATE INDEX IF NOT EXISTS ix_product_reviews_user_id ON product_reviews (user_id);

-- =============================================================
-- 16. CUSTOMER ADDRESSES
-- =============================================================
CREATE TABLE IF NOT EXISTS customer_addresses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    address_type VARCHAR(10) DEFAULT 'home',
    contact_name VARCHAR(255),
    contact_phone VARCHAR(20),
    address_line1 TEXT,
    address_line2 TEXT,
    city VARCHAR(100),
    state VARCHAR(100),
    lga VARCHAR(100),
    postal_code VARCHAR(20),
    landmark TEXT,
    latitude NUMERIC(10,8),
    longitude NUMERIC(11,8),
    is_default BOOLEAN DEFAULT FALSE,
    delivery_instructions TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_customer_addresses_user_id ON customer_addresses (user_id);

-- =============================================================
-- 17. ORDERS
-- =============================================================
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(50) UNIQUE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'pending_payment',
    shipping_address_id UUID REFERENCES customer_addresses(id),
    billing_address_id UUID REFERENCES customer_addresses(id),
    subtotal NUMERIC(15,2) NOT NULL,
    shipping_fee NUMERIC(15,2) DEFAULT 0.00,
    tax_amount NUMERIC(15,2) DEFAULT 0.00,
    discount_amount NUMERIC(15,2) DEFAULT 0.00,
    total_amount NUMERIC(15,2) NOT NULL,
    payment_status VARCHAR(25) DEFAULT 'pending',
    payment_method VARCHAR(20),
    customer_notes TEXT,
    admin_notes TEXT,
    driver_name VARCHAR(255),
    driver_phone VARCHAR(20),
    estimated_delivery_date TIMESTAMP,
    delivered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_orders_order_number ON orders (order_number);
CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS ix_orders_created_at ON orders (created_at);

-- =============================================================
-- 18. ORDER ITEMS
-- =============================================================
CREATE TABLE IF NOT EXISTS order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id),
    variant_id UUID,
    vendor_id UUID NOT NULL REFERENCES vendors(id),
    product_name VARCHAR(500) NOT NULL,
    sku VARCHAR(100) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(15,2) NOT NULL,
    total_price NUMERIC(15,2) NOT NULL,
    vendor_status VARCHAR(20) DEFAULT 'confirmed',
    vendor_notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_order_items_order_id ON order_items (order_id);
CREATE INDEX IF NOT EXISTS ix_order_items_vendor_id ON order_items (vendor_id);

-- =============================================================
-- 19. CART ITEMS
-- =============================================================
CREATE TABLE IF NOT EXISTS cart_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_id UUID,
    quantity INTEGER NOT NULL DEFAULT 1,
    price_at_addition NUMERIC(15,2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (user_id, product_id, variant_id)
);
CREATE INDEX IF NOT EXISTS ix_cart_items_user_id ON cart_items (user_id);

-- =============================================================
-- 20. NOTIFICATIONS
-- =============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) DEFAULT 'system',
    title VARCHAR(255) NOT NULL,
    message TEXT,
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id);
CREATE INDEX IF NOT EXISTS ix_notifications_read ON notifications (read);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at ON notifications (created_at);

-- =============================================================
-- 21. MATERIAL RATES
-- =============================================================
CREATE TABLE IF NOT EXISTS material_rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID NOT NULL REFERENCES categories(id),
    material_name VARCHAR(255) NOT NULL,
    specification VARCHAR(500),
    unit VARCHAR(50) NOT NULL,
    current_price NUMERIC(15,2) NOT NULL,
    previous_price NUMERIC(15,2),
    currency VARCHAR(10) DEFAULT 'NGN',
    state VARCHAR(100),
    lga VARCHAR(100),
    supplier_id UUID REFERENCES vendors(id),
    trend VARCHAR(10) DEFAULT 'stable',
    source VARCHAR(50) DEFAULT 'manual',
    verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_material_rates_category_id ON material_rates (category_id);
CREATE INDEX IF NOT EXISTS ix_material_rates_state ON material_rates (state);

-- =============================================================
-- 22. MATERIAL RATE HISTORY
-- =============================================================
CREATE TABLE IF NOT EXISTS material_rate_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rate_id UUID NOT NULL REFERENCES material_rates(id) ON DELETE CASCADE,
    price NUMERIC(15,2) NOT NULL,
    recorded_at TIMESTAMP DEFAULT NOW(),
    source VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS ix_material_rate_history_rate_id ON material_rate_history (rate_id);

-- =============================================================
-- 23. TOKEN USAGE
-- =============================================================
CREATE TABLE IF NOT EXISTS token_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    lifetime_purchased INTEGER NOT NULL DEFAULT 0,
    lifetime_consumed INTEGER NOT NULL DEFAULT 0,
    free_tier_used_this_month INTEGER NOT NULL DEFAULT 0,
    free_tier_month VARCHAR(7),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_token_usage_user_id ON token_usage (user_id);

-- =============================================================
-- 24. TOKEN TRANSACTIONS
-- =============================================================
CREATE TABLE IF NOT EXISTS token_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_type transactiontype NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    action_type VARCHAR(50),
    boq_id VARCHAR(50),
    reference VARCHAR(100),
    description VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_token_transactions_user_id ON token_transactions (user_id);
CREATE INDEX IF NOT EXISTS ix_token_transactions_created_at ON token_transactions (created_at);

-- =============================================================
-- 25. PROMO CODES
-- =============================================================
CREATE TABLE IF NOT EXISTS promo_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    discount_percent NUMERIC(5,2) NOT NULL,
    max_uses INTEGER DEFAULT 0,
    current_uses INTEGER DEFAULT 0,
    min_order_amount NUMERIC(15,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    vendor_id UUID REFERENCES vendors(id) ON DELETE CASCADE,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_promo_codes_code ON promo_codes (code);

-- =============================================================
-- 26. DEMAND ALERTS
-- =============================================================
CREATE TABLE IF NOT EXISTS demand_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_description VARCHAR(500) NOT NULL,
    city VARCHAR(100) NOT NULL,
    quantity_needed NUMERIC(15,2),
    unit VARCHAR(50),
    project_title VARCHAR(500),
    requested_by UUID REFERENCES users(id),
    status VARCHAR(50) DEFAULT 'pending',
    notified_vendors TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_demand_alerts_city ON demand_alerts (city);
CREATE INDEX IF NOT EXISTS ix_demand_alerts_status ON demand_alerts (status);
CREATE INDEX IF NOT EXISTS ix_demand_alerts_requested_by ON demand_alerts (requested_by);

-- =============================================================
-- 27. SHIPPING ZONES
-- =============================================================
CREATE TABLE IF NOT EXISTS shipping_zones (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    code VARCHAR(20) UNIQUE NOT NULL,
    base_rate NUMERIC(15,2) NOT NULL,
    rate_per_kg NUMERIC(10,2) DEFAULT 0,
    free_weight_kg NUMERIC(10,2) DEFAULT 10,
    handling_fee NUMERIC(15,2) DEFAULT 0,
    estimated_days_min INTEGER DEFAULT 1,
    estimated_days_max INTEGER DEFAULT 3,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 28. SHIPPING ZONE MAPPINGS
-- =============================================================
CREATE TABLE IF NOT EXISTS shipping_zone_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    origin_state VARCHAR(100) NOT NULL,
    origin_city VARCHAR(100),
    destination_state VARCHAR(100) NOT NULL,
    destination_city VARCHAR(100),
    zone_id UUID NOT NULL REFERENCES shipping_zones(id)
);

-- =============================================================
-- 29. VENDOR SHIPPING OVERRIDES
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_shipping_overrides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    zone_id UUID NOT NULL REFERENCES shipping_zones(id),
    custom_base_rate NUMERIC(15,2),
    custom_rate_per_kg NUMERIC(10,2),
    free_shipping_threshold NUMERIC(15,2),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- =============================================================
-- 30. AUDIT LOGS
-- =============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100),
    resource_id VARCHAR(255),
    method VARCHAR(10),
    path VARCHAR(500),
    status_code VARCHAR(10),
    ip_address VARCHAR(45),
    details TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs (action);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at);

-- =============================================================
-- 31. VENDOR REVIEWS
-- =============================================================
CREATE TABLE IF NOT EXISTS vendor_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewer_name VARCHAR(100),
    rating INTEGER NOT NULL,
    comment TEXT,
    is_verified_purchase BOOLEAN DEFAULT FALSE,
    is_approved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_vendor_reviews_vendor_id ON vendor_reviews (vendor_id);
CREATE INDEX IF NOT EXISTS ix_vendor_reviews_user_id ON vendor_reviews (user_id);

-- Add deferred FK for product_reviews -> orders (orders created later)
ALTER TABLE product_reviews
  ADD CONSTRAINT product_reviews_order_id_fkey
  FOREIGN KEY (order_id) REFERENCES orders(id);

-- =============================================================
-- LEGACY ENUM NORMALIZATION + CHECK CONSTRAINTS
-- =============================================================
-- Normalize any historical uppercase values in the VARCHAR-backed
-- (non-native) enum columns, then enforce lowercase-valid values.
-- The SQLAlchemy models expose these as enums whose allowed values
-- are lowercase; stray uppercase values cause LookupError at read.

-- 1) Normalize existing rows (safe no-op when no legacy rows exist)
UPDATE products SET status = LOWER(status)
WHERE status IS NOT NULL AND status <> LOWER(status);

UPDATE orders SET status = LOWER(status)
WHERE status IS NOT NULL AND status <> LOWER(status);

UPDATE orders SET payment_status = LOWER(payment_status)
WHERE payment_status IS NOT NULL AND payment_status <> LOWER(payment_status);

UPDATE orders SET payment_method = LOWER(payment_method)
WHERE payment_method IS NOT NULL AND payment_method <> LOWER(payment_method);

UPDATE order_items SET vendor_status = LOWER(vendor_status)
WHERE vendor_status IS NOT NULL AND vendor_status <> LOWER(vendor_status);

UPDATE vendors SET verification_status = LOWER(verification_status)
WHERE verification_status IS NOT NULL AND verification_status <> LOWER(verification_status);

UPDATE customer_addresses SET address_type = LOWER(address_type)
WHERE address_type IS NOT NULL AND address_type <> LOWER(address_type);

UPDATE material_rates SET trend = LOWER(trend)
WHERE trend IS NOT NULL AND trend <> LOWER(trend);

-- 2) Enforce lowercase, valid enum values going forward
ALTER TABLE products
  ADD CONSTRAINT chk_products_status CHECK (
    status IS NULL OR (
      status IN ('draft','active','out_of_stock','discontinued','pending_approval')
      AND status = LOWER(status)
    )
  );

ALTER TABLE orders
  ADD CONSTRAINT chk_orders_status CHECK (
    status IS NULL OR (
      status IN ('pending_payment','payment_failed','confirmed','processing',
                 'ready_for_pickup','shipped','in_transit','delivered',
                 'cancelled','refunded')
      AND status = LOWER(status)
    )
  );

ALTER TABLE orders
  ADD CONSTRAINT chk_orders_payment_status CHECK (
    payment_status IS NULL OR (
      payment_status IN ('pending','completed','failed','refunded','partially_refunded')
      AND payment_status = LOWER(payment_status)
    )
  );

ALTER TABLE orders
  ADD CONSTRAINT chk_orders_payment_method CHECK (
    payment_method IS NULL OR (
      payment_method IN ('card','bank_transfer','ussd','wallet','pay_on_delivery')
      AND payment_method = LOWER(payment_method)
    )
  );

ALTER TABLE order_items
  ADD CONSTRAINT chk_order_items_vendor_status CHECK (
    vendor_status IS NULL OR (
      vendor_status IN ('pending_payment','payment_failed','confirmed','processing',
                        'ready_for_pickup','shipped','in_transit','delivered',
                        'cancelled','refunded')
      AND vendor_status = LOWER(vendor_status)
    )
  );

ALTER TABLE vendors
  ADD CONSTRAINT chk_vendors_verification_status CHECK (
    verification_status IS NULL OR (
      verification_status IN ('pending','verified','rejected','suspended','deactivated')
      AND verification_status = LOWER(verification_status)
    )
  );

ALTER TABLE customer_addresses
  ADD CONSTRAINT chk_customer_addresses_address_type CHECK (
    address_type IS NULL OR (
      address_type IN ('home','office','site','other')
      AND address_type = LOWER(address_type)
    )
  );

ALTER TABLE material_rates
  ADD CONSTRAINT chk_material_rates_trend CHECK (
    trend IS NULL OR (
      trend IN ('up','down','stable')
      AND trend = LOWER(trend)
    )
  );

-- NOTE: users.role, users.status, token_transactions.transaction_type are
-- native PG ENUM columns; Postgres rejects invalid casing at insert, so
-- they need no CHECK constraint.

-- =============================================================
-- DEFAULT USERS SEED (incl. ADMIN USERS)
-- =============================================================
-- Seeds default system + admin accounts so the platform/admin app can be
-- used immediately. All default admins share the default password below
-- and MUST change it on first login.
--
--   Default password : Admin@123
--   bcrypt hash      : $2b$12$NOvAelP3Llez4cvww7gCr.5EABB/AoZZESt1RraTCeeE9bK0dG5Ra
--
-- support@burncost.com (support) and marketing@burncost.com (marketing)
-- can be created later via the admin User Management page.

-- Super Admin
INSERT INTO users (email, phone_number, password_hash, role, status, email_verified)
VALUES (
  'superadmin@burncost.com',
  '08000000001',
  '$2b$12$NOvAelP3Llez4cvww7gCr.5EABB/AoZZESt1RraTCeeE9bK0dG5Ra',
  'super_admin',
  'active',
  TRUE
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO user_profiles (user_id, first_name, other_name, last_name, business_name)
SELECT id, 'Super', 'Admin', 'Burncost', 'Burncost'
FROM users WHERE email = 'superadmin@burncost.com'
ON CONFLICT (user_id) DO NOTHING;

-- Admin
INSERT INTO users (email, phone_number, password_hash, role, status, email_verified)
VALUES (
  'admin@burncost.com',
  '08000000002',
  '$2b$12$NOvAelP3Llez4cvww7gCr.5EABB/AoZZESt1RraTCeeE9bK0dG5Ra',
  'admin',
  'active',
  TRUE
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO user_profiles (user_id, first_name, other_name, last_name, business_name)
SELECT id, 'Admin', 'User', 'Burncost', 'Burncost'
FROM users WHERE email = 'admin@burncost.com'
ON CONFLICT (user_id) DO NOTHING;

-- Manager
INSERT INTO users (email, phone_number, password_hash, role, status, email_verified)
VALUES (
  'manager@burncost.com',
  '08000000003',
  '$2b$12$NOvAelP3Llez4cvww7gCr.5EABB/AoZZESt1RraTCeeE9bK0dG5Ra',
  'manager',
  'active',
  TRUE
)
ON CONFLICT (email) DO NOTHING;

INSERT INTO user_profiles (user_id, first_name, other_name, last_name, business_name)
SELECT id, 'Manager', 'User', 'Burncost', 'Burncost'
FROM users WHERE email = 'manager@burncost.com'
ON CONFLICT (user_id) DO NOTHING;

-- =============================================================
-- DONE
-- =============================================================
