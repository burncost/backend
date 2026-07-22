-- =============================================================
-- BURNCOST DATABASE SCHEMA — DDL Script
-- Generated from SQLAlchemy models in Backend/app/models/
-- Run after: alembic upgrade head  (or standalone)
-- =============================================================

CREATE TYPE user_role AS ENUM ('customer', 'vendor', 'admin', 'super_admin');
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
    verified BOOLEAN DEFAULT FALSE
);

-- =============================================================
-- 9. VENDOR DRAFTS
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
-- 10. PRODUCTS
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
-- 11. PRODUCT IMAGES
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
-- 12. PRODUCT SPECIFICATIONS
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
-- 13. PRODUCT VARIANTS
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
-- 14. PRODUCT REVIEWS
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
-- 15. CUSTOMER ADDRESSES
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
-- 16. ORDERS
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
-- 17. ORDER ITEMS
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
-- 18. CART ITEMS
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
-- 19. NOTIFICATIONS
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
-- 20. MATERIAL RATES
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
-- 21. MATERIAL RATE HISTORY
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
-- 22. TOKEN USAGE
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
-- 23. TOKEN TRANSACTIONS
-- =============================================================
CREATE TYPE IF NOT EXISTS transactiontype AS ENUM ('purchase', 'consumption', 'refund', 'free_tier', 'expiry');
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
-- 24. PROMO CODES
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
-- 25. DEMAND ALERTS
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
-- 26. SHIPPING ZONES
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
-- 27. SHIPPING ZONE MAPPINGS
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
-- 28. VENDOR SHIPPING OVERRIDES
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

-- Add deferred FK for product_reviews -> orders (orders created later)
ALTER TABLE product_reviews
  ADD CONSTRAINT product_reviews_order_id_fkey
  FOREIGN KEY (order_id) REFERENCES orders(id);

-- =============================================================
-- DONE
-- =============================================================
