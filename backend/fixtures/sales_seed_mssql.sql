-- SQL Server (T-SQL) mirror of the Postgres `sales` fixture
-- (backend/fixtures/sales_seed.sql). Same 42-table, deliberately-messy commerce
-- schema, with T-SQL types (BIGINT IDENTITY, NVARCHAR, DECIMAL, BIT, DATE,
-- DATETIME2) and T-SQL idioms: a `nums` numbers table (recursive CTE, no
-- generate_series), CHOOSE() for array indexing, DATEADD() for interval math,
-- and deterministic g-arithmetic instead of RAND() (which T-SQL evaluates once
-- per query, not per row).
--
-- The design rationale, messiness inventory, and the five bridge-table question
-- paths are documented in the Postgres file; this mirrors it so the same golden
-- questions can be evaluated against SQL Server. Self-contained: it creates the
-- `sales` database and the read-only `analytics_ro` login (db_datareader), so
-- the eval harness / `make fixtures` can load it into a throwaway container.
-- Foreign keys are added after the data load (all tables exist by then), so
-- introspection reports the same relationship graph the Postgres fixture does.

SET NOCOUNT ON;
IF DB_ID('sales') IS NOT NULL
BEGIN
  ALTER DATABASE sales SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
  DROP DATABASE sales;
END
GO
CREATE DATABASE sales;
GO
USE sales;
GO

-- Numbers helper (dropped before the read-only user is created).
CREATE TABLE nums (g INT PRIMARY KEY);
GO
WITH seq AS (SELECT 1 AS g UNION ALL SELECT g + 1 FROM seq WHERE g < 20000)
INSERT INTO nums (g) SELECT g FROM seq OPTION (MAXRECURSION 0);
GO

-- ── dimension / reference tables ────────────────────────────────────────────
CREATE TABLE countries (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  iso_code      NVARCHAR(10) NOT NULL UNIQUE,
  name          NVARCHAR(255) NOT NULL,
  continent     NVARCHAR(50) NOT NULL,
  currency_code NVARCHAR(10),
  created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE regions (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  country_id BIGINT,
  name       NVARCHAR(255) NOT NULL,
  code       NVARCHAR(20),
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE categories (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  name        NVARCHAR(255) NOT NULL,
  description NVARCHAR(MAX),
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE subcategories (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  category_id BIGINT NOT NULL,
  name        NVARCHAR(255) NOT NULL,
  description NVARCHAR(MAX),
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE brands (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  name          NVARCHAR(255) NOT NULL,
  country_id    BIGINT,
  website       NVARCHAR(255),
  is_active     BIT NOT NULL DEFAULT 1,
  source_system NVARCHAR(50),
  created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE products (
  id             BIGINT IDENTITY(1,1) PRIMARY KEY,
  name           NVARCHAR(255) NOT NULL,
  category       NVARCHAR(100) NOT NULL,
  category_id    BIGINT,
  subcategory_id BIGINT,
  brand_id       BIGINT,
  sku            NVARCHAR(50) UNIQUE,
  price          DECIMAL(10,2) NOT NULL,
  cost           DECIMAL(10,2),
  weight_kg      DECIMAL(8,3),
  active         BIT NOT NULL DEFAULT 1,
  discontinued   BIT NOT NULL DEFAULT 0,
  flg_2          BIT NOT NULL DEFAULT 0,
  cust_ref       NVARCHAR(100),
  launched_at    DATE,
  source_system  NVARCHAR(50),
  external_ref   NVARCHAR(100),
  created_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

-- near-duplicate name (see Postgres header)
CREATE TABLE product (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  name       NVARCHAR(255),
  cust_ref   NVARCHAR(100),
  flg_2      BIT,
  old_price  DECIMAL(10,2),
  note       NVARCHAR(MAX),
  created_at DATETIME2 DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE product_variants (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  product_id  BIGINT NOT NULL,
  variant_sku NVARCHAR(50) UNIQUE,
  color       NVARCHAR(50),
  size        NVARCHAR(50),
  extra_price DECIMAL(10,2) NOT NULL DEFAULT 0,
  barcode     NVARCHAR(50),
  active      BIT NOT NULL DEFAULT 1,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE suppliers (
  id             BIGINT IDENTITY(1,1) PRIMARY KEY,
  name           NVARCHAR(255) NOT NULL,
  region_id      BIGINT,
  contact_email  NVARCHAR(255),
  phone          NVARCHAR(50),
  lead_time_days INT NOT NULL DEFAULT 7,
  rating         DECIMAL(3,2),
  active         BIT NOT NULL DEFAULT 1,
  address_line1  NVARCHAR(255),
  city           NVARCHAR(100),
  postal_code    NVARCHAR(20),
  created_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE warehouses (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  name          NVARCHAR(255) NOT NULL,
  region_id     BIGINT,
  capacity      INT NOT NULL,
  address_line1 NVARCHAR(255),
  city          NVARCHAR(100),
  is_active     BIT NOT NULL DEFAULT 1,
  created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE teams (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  name       NVARCHAR(255) NOT NULL,
  region_id  BIGINT,
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE employees (
  id             BIGINT IDENTITY(1,1) PRIMARY KEY,
  name           NVARCHAR(255) NOT NULL,
  title          NVARCHAR(100) NOT NULL,
  region_id      BIGINT,
  team_id        BIGINT,
  manager_id     BIGINT,
  email          NVARCHAR(255),
  hired_at       DATE NOT NULL,
  terminated_at  DATE,
  salary         DECIMAL(10,2) NOT NULL,
  commission_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  active         BIT NOT NULL DEFAULT 1,
  created_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE loyalty_tiers (
  id           BIGINT IDENTITY(1,1) PRIMARY KEY,
  name         NVARCHAR(100) NOT NULL,
  min_points   INT NOT NULL DEFAULT 0,
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  created_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE customers (
  id              BIGINT IDENTITY(1,1) PRIMARY KEY,
  name            NVARCHAR(255) NOT NULL,
  email           NVARCHAR(255),
  phone           NVARCHAR(50),
  region_id       BIGINT,
  loyalty_tier_id BIGINT,
  referred_by_id  BIGINT,
  segment         NVARCHAR(50) NOT NULL DEFAULT 'SMB',
  credit_limit    DECIMAL(12,2),
  cust_ref        NVARCHAR(100),
  signed_up_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  last_order_at   DATETIME2,
  is_deleted      BIT NOT NULL DEFAULT 0,
  deleted_at      DATETIME2,
  created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE customer_addresses (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  kind        NVARCHAR(20) NOT NULL DEFAULT 'shipping',
  line1       NVARCHAR(255) NOT NULL,
  line2       NVARCHAR(255),
  city        NVARCHAR(100),
  region_id   BIGINT,
  postal_code NVARCHAR(20),
  country_id  BIGINT,
  is_primary  BIT NOT NULL DEFAULT 0,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE currencies (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  code       NVARCHAR(10) NOT NULL UNIQUE,
  name       NVARCHAR(100) NOT NULL,
  symbol     NVARCHAR(10),
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE price_lists (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  name        NVARCHAR(255) NOT NULL,
  currency_id BIGINT,
  valid_from  DATE NOT NULL,
  valid_to    DATE,
  is_active   BIT NOT NULL DEFAULT 1,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE tax_rates (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  region_id  BIGINT,
  name       NVARCHAR(100) NOT NULL,
  rate_pct   DECIMAL(5,2) NOT NULL,
  valid_from DATE NOT NULL,
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE promotions (
  id           BIGINT IDENTITY(1,1) PRIMARY KEY,
  name         NVARCHAR(255) NOT NULL,
  description  NVARCHAR(MAX),
  promo_type   NVARCHAR(50) NOT NULL DEFAULT 'percent',
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  budget       DECIMAL(12,2),
  starts_on    DATE NOT NULL,
  ends_on      DATE,
  is_active    BIT NOT NULL DEFAULT 1,
  created_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE coupons (
  id           BIGINT IDENTITY(1,1) PRIMARY KEY,
  promotion_id BIGINT,
  code         NVARCHAR(50) NOT NULL UNIQUE,
  max_uses     INT NOT NULL DEFAULT 1,
  times_used   INT NOT NULL DEFAULT 0,
  expires_on   DATE,
  created_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE tags (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  name       NVARCHAR(100) NOT NULL UNIQUE,
  kind       NVARCHAR(50) NOT NULL DEFAULT 'attribute',
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE carriers (
  id           BIGINT IDENTITY(1,1) PRIMARY KEY,
  name         NVARCHAR(100) NOT NULL,
  tracking_url NVARCHAR(255),
  is_active    BIT NOT NULL DEFAULT 1,
  created_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE payment_methods (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  name       NVARCHAR(100) NOT NULL,
  kind       NVARCHAR(50) NOT NULL DEFAULT 'card',
  is_active  BIT NOT NULL DEFAULT 1,
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

-- ── junction / bridge tables (kept narrow) ──────────────────────────────────
CREATE TABLE product_suppliers (
  product_id     BIGINT NOT NULL,
  supplier_id    BIGINT NOT NULL,
  cost           DECIMAL(10,2) NOT NULL,
  is_preferred   BIT NOT NULL DEFAULT 0,
  lead_time_days INT NOT NULL DEFAULT 7,
  PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE price_list_items (
  price_list_id BIGINT NOT NULL,
  product_id    BIGINT NOT NULL,
  unit_price    DECIMAL(10,2) NOT NULL,
  PRIMARY KEY (price_list_id, product_id)
);

CREATE TABLE product_tags (
  product_id BIGINT NOT NULL,
  tag_id     BIGINT NOT NULL,
  PRIMARY KEY (product_id, tag_id)
);

CREATE TABLE employee_teams (
  employee_id  BIGINT NOT NULL,
  team_id      BIGINT NOT NULL,
  role_in_team NVARCHAR(50) NOT NULL DEFAULT 'member',
  assigned_on  DATE NOT NULL,
  PRIMARY KEY (employee_id, team_id)
);

CREATE TABLE inventory (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  product_id    BIGINT NOT NULL,
  warehouse_id  BIGINT NOT NULL,
  quantity      INT NOT NULL,
  reorder_level INT NOT NULL DEFAULT 10,
  updated_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  CONSTRAINT uq_inventory UNIQUE (product_id, warehouse_id)
);

CREATE TABLE shipment_items (
  shipment_id   BIGINT NOT NULL,
  order_item_id BIGINT NOT NULL,
  quantity      INT NOT NULL,
  PRIMARY KEY (shipment_id, order_item_id)
);

CREATE TABLE order_promotions (
  order_id        BIGINT NOT NULL,
  promotion_id    BIGINT NOT NULL,
  discount_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (order_id, promotion_id)
);

-- ── fact / transaction tables ───────────────────────────────────────────────
CREATE TABLE orders (
  id             BIGINT IDENTITY(1,1) PRIMARY KEY,
  customer_id    BIGINT,
  employee_id    BIGINT,
  coupon_id      BIGINT,
  currency_id    BIGINT,
  order_date     DATE NOT NULL,
  status         NVARCHAR(30) NOT NULL,
  channel        NVARCHAR(30) NOT NULL DEFAULT 'web',
  subtotal       DECIMAL(12,2) NOT NULL,
  discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  tax_total      DECIMAL(12,2) NOT NULL DEFAULT 0,
  shipping_fee   DECIMAL(10,2) NOT NULL DEFAULT 0,
  total_amount   DECIMAL(12,2) NOT NULL,
  cust_ref       NVARCHAR(100),
  notes          NVARCHAR(MAX),
  placed_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  updated_at     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE order_items (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  order_id    BIGINT,
  product_id  BIGINT,
  variant_id  BIGINT,
  quantity    INT NOT NULL,
  unit_price  DECIMAL(10,2) NOT NULL,
  discount    DECIMAL(5,2) NOT NULL DEFAULT 0,
  tax_rate_id BIGINT,
  line_total  DECIMAL(12,2) NOT NULL,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE payments (
  id                BIGINT IDENTITY(1,1) PRIMARY KEY,
  order_id          BIGINT,
  payment_method_id BIGINT,
  amount            DECIMAL(12,2) NOT NULL,
  currency_id       BIGINT,
  status            NVARCHAR(30) NOT NULL DEFAULT 'captured',
  paid_at           DATETIME2 NOT NULL,
  txn_ref           NVARCHAR(50),
  created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE shipments (
  id              BIGINT IDENTITY(1,1) PRIMARY KEY,
  order_id        BIGINT,
  warehouse_id    BIGINT,
  carrier_id      BIGINT,
  tracking_number NVARCHAR(50),
  status          NVARCHAR(30) NOT NULL DEFAULT 'in_transit',
  shipped_at      DATETIME2 NOT NULL,
  delivered_at    DATETIME2,
  weight_kg       DECIMAL(8,3),
  cost            DECIMAL(10,2),
  created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE [returns] (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  order_item_id BIGINT,
  reason        NVARCHAR(100) NOT NULL,
  quantity      INT NOT NULL,
  refund_amount DECIMAL(10,2) NOT NULL,
  status        NVARCHAR(30) NOT NULL DEFAULT 'approved',
  returned_at   DATETIME2 NOT NULL,
  created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE refunds (
  id           BIGINT IDENTITY(1,1) PRIMARY KEY,
  return_id    BIGINT,
  payment_id   BIGINT,
  amount       DECIMAL(10,2) NOT NULL,
  method       NVARCHAR(30) NOT NULL DEFAULT 'card',
  processed_at DATETIME2 NOT NULL,
  created_at   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE reviews (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  product_id  BIGINT,
  customer_id BIGINT,
  order_id    BIGINT,
  rating      INT NOT NULL,
  title       NVARCHAR(255),
  body        NVARCHAR(MAX),
  is_verified BIT NOT NULL DEFAULT 0,
  is_hidden   BIT NOT NULL DEFAULT 0,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE support_tickets (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  customer_id BIGINT,
  order_id    BIGINT,
  employee_id BIGINT,
  subject     NVARCHAR(255) NOT NULL,
  status      NVARCHAR(30) NOT NULL DEFAULT 'open',
  priority    NVARCHAR(30) NOT NULL DEFAULT 'normal',
  opened_at   DATETIME2 NOT NULL,
  closed_at   DATETIME2,
  created_at  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE wishlists (
  id          BIGINT IDENTITY(1,1) PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  product_id  BIGINT NOT NULL,
  added_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  note        NVARCHAR(MAX),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE order_status_history (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  order_id   BIGINT NOT NULL,
  old_status NVARCHAR(30),
  new_status NVARCHAR(30) NOT NULL,
  changed_by BIGINT,
  changed_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  note       NVARCHAR(MAX),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE product_price_history (
  id         BIGINT IDENTITY(1,1) PRIMARY KEY,
  product_id BIGINT NOT NULL,
  old_price  DECIMAL(10,2),
  new_price  DECIMAL(10,2) NOT NULL,
  changed_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  reason     NVARCHAR(255),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);

CREATE TABLE sales_daily_rollup (
  id            BIGINT IDENTITY(1,1) PRIMARY KEY,
  [day]         DATE NOT NULL,
  region_id     BIGINT,
  orders_count  INT NOT NULL DEFAULT 0,
  gross_revenue DECIMAL(14,2) NOT NULL DEFAULT 0,
  units_sold    INT NOT NULL DEFAULT 0,
  refunds_total DECIMAL(14,2) NOT NULL DEFAULT 0,
  created_at    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by BIGINT, updated_by BIGINT, src_system NVARCHAR(50),
  src_batch_id NVARCHAR(50), ext_ref NVARCHAR(100),
  is_archived BIT DEFAULT 0, row_version INT DEFAULT 1, audit_notes NVARCHAR(MAX)
);
GO

-- ══════════════════════════════════════════════════════════════════════════
--  DATA  (nums drives volumes; CHOOSE() replaces PG arrays; values are
--  deterministic g-arithmetic because T-SQL RAND() is per-query, not per-row)
-- ══════════════════════════════════════════════════════════════════════════

INSERT INTO countries (iso_code, name, continent, currency_code)
SELECT CONCAT('C', RIGHT('00' + CAST(g AS VARCHAR(20)), 2)), CONCAT('Country ', g),
       CHOOSE((g % 6) + 1,'North America','Europe','Asia','South America','Africa','Oceania'),
       CHOOSE((g % 6) + 1,'USD','EUR','GBP','JPY','BRL','AUD')
FROM nums WHERE g <= 25;

INSERT INTO regions (country_id, name, code) VALUES
  (1,'North America','NA'), (2,'Europe','EU'), (3,'Asia Pacific','APAC'),
  (4,'Latin America','LATAM'), (5,'Middle East','ME'), (6,'Africa','AF'),
  (7,'Nordics','NORD'), (NULL,'Unassigned',NULL);

INSERT INTO categories (name, description) VALUES
  ('Accessories','Hubs, stands, cables and small add-ons'),
  ('Peripherals','Keyboards, mice, webcams'),
  ('Displays','Monitors and screens'),
  ('Audio','Headsets and speakers'),
  ('Networking','Routers, switches, adapters'),
  ('Storage','Drives and memory');

INSERT INTO subcategories (category_id, name, description)
SELECT (g % 6) + 1, CONCAT('Subcategory ', g), CONCAT('Auto-generated subcategory ', g)
FROM nums WHERE g <= 20;

INSERT INTO brands (name, country_id, website, source_system)
SELECT CONCAT('Brand ', g),
       CASE WHEN g % 5 = 0 THEN NULL ELSE (g % 25) + 1 END,
       CONCAT('https://brand', g, '.example'),
       CHOOSE((g % 3) + 1,'erp','pim','legacy')
FROM nums WHERE g <= 18;

INSERT INTO products
  (name, category, category_id, subcategory_id, brand_id, sku, price, cost,
   weight_kg, active, discontinued, flg_2, cust_ref, launched_at, source_system, external_ref)
SELECT CONCAT(CHOOSE((g % 6) + 1,'Accessories','Peripherals','Displays','Audio','Networking','Storage'),' Model ', g),
       CHOOSE((g % 6) + 1,'Accessories','Peripherals','Displays','Audio','Networking','Storage'),
       (g % 6) + 1,
       CASE WHEN g % 7 = 0 THEN NULL ELSE (g % 20) + 1 END,
       CASE WHEN g % 9 = 0 THEN NULL ELSE (g % 18) + 1 END,
       CONCAT('SKU-', RIGHT('00000' + CAST(g AS VARCHAR(20)), 5)),
       CAST(20 + (g * 37 % 380) + (g % 100) / 100.0 AS DECIMAL(10,2)),
       CAST(10 + (g * 23 % 180) + (g % 100) / 100.0 AS DECIMAL(10,2)),
       CAST(0.1 + (g % 40) / 10.0 AS DECIMAL(8,3)),
       CASE WHEN g % 17 <> 0 THEN 1 ELSE 0 END,
       CASE WHEN g % 23 = 0 THEN 1 ELSE 0 END,
       CASE WHEN g % 2 = 0 THEN 1 ELSE 0 END,
       CASE WHEN g % 4 = 0 THEN CONCAT('LEG-', RIGHT('000000' + CAST(g AS VARCHAR(20)), 6)) ELSE NULL END,
       DATEADD(DAY, -((g * 13) % 1500), CAST(SYSUTCDATETIME() AS DATE)),
       CHOOSE((g % 3) + 1,'erp','pim','legacy'), CONCAT('EXT', RIGHT('000000' + CAST(g AS VARCHAR(20)), 6))
FROM nums WHERE g <= 120;

INSERT INTO product (name, cust_ref, flg_2, old_price, note) VALUES
  ('Legacy Widget A','OLD-000001',1,19.99,'do not use - see products'),
  ('Legacy Widget B','OLD-000002',0,29.99,'migrated 2019'),
  ('Legacy Widget C',NULL,1,9.99,NULL),
  ('Legacy Widget D','OLD-000004',0,49.99,'kept for the quarterly PDF'),
  ('Legacy Widget E','OLD-000005',NULL,14.99,NULL),
  ('Legacy Widget F','OLD-000006',1,99.99,'ghost row');

INSERT INTO product_variants (product_id, variant_sku, color, size, extra_price, barcode)
SELECT ((g - 1) / 3) + 1, CONCAT('VAR-', RIGHT('000000' + CAST(g AS VARCHAR(20)), 6)),
       CHOOSE((g % 5) + 1,'black','white','silver','blue','red'),
       CHOOSE((g % 5) + 1,'S','M','L','XL','one-size'),
       CAST((g % 4) * 5 AS DECIMAL(10,2)), CONCAT('BC', RIGHT('0000000000' + CAST(g AS VARCHAR(20)), 10))
FROM nums WHERE g <= 360;

INSERT INTO suppliers (name, region_id, contact_email, phone, lead_time_days, rating, address_line1, city, postal_code)
SELECT CONCAT('Supplier ', g),
       CASE WHEN g % 8 = 0 THEN NULL ELSE (g % 8) + 1 END,
       CONCAT('sales@supplier', g, '.example'),
       CONCAT('+1-555-', RIGHT('0000' + CAST(g AS VARCHAR(20)), 4)), (g % 21) + 3,
       CAST(3 + (g % 20) / 10.0 AS DECIMAL(3,2)),
       CONCAT(g, ' Industrial Way'), CONCAT('City ', (g % 8) + 1),
       RIGHT('00000' + CAST((g * 137) % 99999 AS VARCHAR(20)), 5)
FROM nums WHERE g <= 30;

INSERT INTO warehouses (name, region_id, capacity, address_line1, city)
SELECT CONCAT(r.name, ' DC'), r.id, 5000 + (r.id * 1500),
       CONCAT(r.id, ' Distribution Blvd'), r.name
FROM regions r;

INSERT INTO teams (name, region_id)
SELECT CHOOSE(g,'Field Sales','Inside Sales','Enterprise','SMB','Partnerships','Renewals','Named Accounts','Growth'),
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 8) + 1 END
FROM nums WHERE g <= 8;

INSERT INTO employees
  (name, title, region_id, team_id, manager_id, email, hired_at, terminated_at, salary, commission_pct, active)
SELECT CONCAT('Employee ', g),
       CHOOSE((g % 6) + 1,'Sales Rep','Sales Rep','Sales Rep','Account Manager','Regional Manager','Director'),
       (g % 8) + 1,
       CASE WHEN g % 6 = 0 THEN NULL ELSE (g % 8) + 1 END,
       CASE WHEN g <= 6 THEN NULL ELSE (g % 6) + 1 END,
       CONCAT('employee', g, '@datamind.example'),
       DATEADD(DAY, -((g * 47) % 2500), CAST(SYSUTCDATETIME() AS DATE)),
       CASE WHEN g % 19 = 0 THEN DATEADD(DAY, -((g * 7) % 300), CAST(SYSUTCDATETIME() AS DATE)) ELSE NULL END,
       CAST(45000 + (g * 137 % 60000) AS DECIMAL(10,2)), CAST((g % 5) * 1.5 AS DECIMAL(5,2)),
       CASE WHEN g % 19 <> 0 THEN 1 ELSE 0 END
FROM nums WHERE g <= 60;

INSERT INTO loyalty_tiers (name, min_points, discount_pct) VALUES
  ('Bronze',0,0), ('Silver',1000,2.5), ('Gold',5000,5), ('Platinum',20000,10);

INSERT INTO customers
  (name, email, phone, region_id, loyalty_tier_id, referred_by_id, segment,
   credit_limit, cust_ref, signed_up_at, last_order_at, is_deleted, deleted_at)
SELECT CONCAT('Customer ', g), CONCAT('customer', g, '@example.com'),
       CONCAT('+1-555-', RIGHT('0000' + CAST(g % 10000 AS VARCHAR(20)), 4)), (g % 8) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE (g % 4) + 1 END,
       CASE WHEN g > 50 AND g % 7 = 0 THEN (g % 50) + 1 ELSE NULL END,
       CHOOSE((g % 4) + 1,'SMB','SMB','Mid-Market','Enterprise'),
       CAST(1000 + (g * 271 % 40000) AS DECIMAL(12,2)),
       CASE WHEN g % 5 = 0 THEN CONCAT('CRM-', RIGHT('0000000' + CAST(g AS VARCHAR(20)), 7)) ELSE NULL END,
       DATEADD(DAY, -(g % 1000), SYSUTCDATETIME()),
       CASE WHEN g % 6 = 0 THEN NULL ELSE DATEADD(DAY, -(g % 200), SYSUTCDATETIME()) END,
       CASE WHEN g % 13 = 0 THEN 1 ELSE 0 END,
       CASE WHEN g % 13 = 0 THEN DATEADD(DAY, -(g % 90), SYSUTCDATETIME()) ELSE NULL END
FROM nums WHERE g <= 1500;

INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'shipping', CONCAT(c.id, ' Main St'), CONCAT('City ', (c.id % 8) + 1),
       c.region_id, RIGHT('00000' + CAST((c.id * 91) % 99999 AS VARCHAR(20)), 5), (c.id % 25) + 1, 1
FROM customers c;
INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'billing', CONCAT(c.id, ' Finance Ave'), CONCAT('City ', (c.id % 8) + 1),
       c.region_id, RIGHT('00000' + CAST((c.id * 57) % 99999 AS VARCHAR(20)), 5), (c.id % 25) + 1, 0
FROM customers c WHERE c.id % 2 = 0;

INSERT INTO currencies (code, name, symbol) VALUES
  ('USD','US Dollar','$'), ('EUR','Euro',N'€'), ('GBP','Pound Sterling',N'£'),
  ('JPY','Japanese Yen',N'¥'), ('BRL','Brazilian Real','R$'),
  ('AUD','Australian Dollar','A$'), ('CAD','Canadian Dollar','C$'), ('INR','Indian Rupee',N'₹');

INSERT INTO price_lists (name, currency_id, valid_from, valid_to, is_active) VALUES
  ('Standard USD',1, DATEADD(DAY,-400,CAST(SYSUTCDATETIME() AS DATE)), NULL, 1),
  ('EU Retail',2, DATEADD(DAY,-400,CAST(SYSUTCDATETIME() AS DATE)), NULL, 1),
  ('UK Retail',3, DATEADD(DAY,-400,CAST(SYSUTCDATETIME() AS DATE)), NULL, 1),
  ('Legacy 2023',1, DATEADD(DAY,-800,CAST(SYSUTCDATETIME() AS DATE)), DATEADD(DAY,-365,CAST(SYSUTCDATETIME() AS DATE)), 0),
  ('Enterprise',1, DATEADD(DAY,-200,CAST(SYSUTCDATETIME() AS DATE)), NULL, 1);

INSERT INTO tax_rates (region_id, name, rate_pct, valid_from)
SELECT (g % 8) + 1, CONCAT('Standard ', g), CAST((g % 5) * 2.5 + 5 AS DECIMAL(5,2)),
       DATEADD(DAY,-500,CAST(SYSUTCDATETIME() AS DATE))
FROM nums WHERE g <= 12;

INSERT INTO promotions (name, description, promo_type, discount_pct, budget, starts_on, ends_on, is_active)
SELECT CONCAT('Promo ', g), CONCAT('Auto promotion ', g),
       CHOOSE((g % 4) + 1,'percent','percent','bogo','fixed'),
       CAST((g % 6) * 5 + 5 AS DECIMAL(5,2)), CAST(5000 + (g * 331 % 50000) AS DECIMAL(12,2)),
       DATEADD(DAY, -((g * 30) % 700), CAST(SYSUTCDATETIME() AS DATE)),
       DATEADD(DAY, 45 - ((g * 30) % 700), CAST(SYSUTCDATETIME() AS DATE)),
       CASE WHEN g % 4 <> 0 THEN 1 ELSE 0 END
FROM nums WHERE g <= 20;

INSERT INTO coupons (promotion_id, code, max_uses, times_used, expires_on)
SELECT (g % 20) + 1, CONCAT('CPN-', RIGHT('000000' + CAST(g AS VARCHAR(20)), 6)),
       (g % 5) * 100 + 1, (g % 37), DATEADD(DAY, g % 120, CAST(SYSUTCDATETIME() AS DATE))
FROM nums WHERE g <= 60;

INSERT INTO tags (name, kind)
SELECT CHOOSE(g,'bestseller','clearance','new','eco','premium','bulk','fragile',
           'refurb','bundle','limited','gaming','office','travel','wireless',
           'usb-c','4k','rgb','compact','heavy-duty','warranty-3y','warranty-1y',
           'imported','local','seasonal','staff-pick'),
       CHOOSE((g % 4) + 1,'merch','merch','lifecycle','attribute')
FROM nums WHERE g <= 25;

INSERT INTO carriers (name, tracking_url) VALUES
  ('UPS','https://ups.example/track?n='), ('FedEx','https://fedex.example/track?n='),
  ('DHL','https://dhl.example/track?n='), ('USPS','https://usps.example/track?n='),
  ('Aramex','https://aramex.example/track?n='), ('Local Courier',NULL);

INSERT INTO payment_methods (name, kind) VALUES
  ('Visa','card'), ('Mastercard','card'), ('Amex','card'),
  ('PayPal','wallet'), ('Wire Transfer','bank'), ('Store Credit','credit');

-- junction data
INSERT INTO product_suppliers (product_id, supplier_id, cost, is_preferred, lead_time_days)
SELECT g, (g % 30) + 1, CAST(10 + (g * 17 % 200) AS DECIMAL(10,2)), 1, (g % 15) + 3 FROM nums WHERE g <= 120;
INSERT INTO product_suppliers (product_id, supplier_id, cost, is_preferred, lead_time_days)
SELECT g, ((g + 11) % 30) + 1, CAST(10 + (g * 29 % 200) AS DECIMAL(10,2)), 0, (g % 20) + 5
FROM nums WHERE g <= 120 AND g % 2 = 0 AND ((g % 30) + 1) <> (((g + 11) % 30) + 1);

INSERT INTO price_list_items (price_list_id, product_id, unit_price)
SELECT pl.id, p.id, CAST(p.price * (1.0 + pl.id / 20.0) AS DECIMAL(10,2))
FROM price_lists pl CROSS JOIN products p
WHERE pl.id IN (1,2,3,5) OR (pl.id = 4 AND p.id % 3 = 0);

INSERT INTO product_tags (product_id, tag_id)
SELECT DISTINCT p.g, ((p.g * 7 + s.g * 13) % 25) + 1
FROM (SELECT g FROM nums WHERE g <= 120) p
CROSS JOIN (SELECT g FROM nums WHERE g <= 4) s;

INSERT INTO employee_teams (employee_id, team_id, role_in_team, assigned_on)
SELECT g, (g % 8) + 1, CASE WHEN g % 6 = 0 THEN 'lead' ELSE 'member' END, CAST(SYSUTCDATETIME() AS DATE)
FROM nums WHERE g <= 60;
INSERT INTO employee_teams (employee_id, team_id, role_in_team, assigned_on)
SELECT g, ((g + 3) % 8) + 1, 'member', CAST(SYSUTCDATETIME() AS DATE)
FROM nums WHERE g <= 60 AND g % 3 = 0 AND ((g % 8) + 1) <> (((g + 3) % 8) + 1);

INSERT INTO inventory (product_id, warehouse_id, quantity, reorder_level)
SELECT p.id, w.id, (p.id * 7 + w.id * 13) % 500, 10 + (p.id % 40)
FROM products p CROSS JOIN warehouses w;

-- fact data
INSERT INTO orders
  (customer_id, employee_id, coupon_id, currency_id, order_date, status, channel,
   subtotal, discount_total, tax_total, shipping_fee, total_amount, cust_ref, placed_at)
SELECT (g % 1500) + 1,
       CASE WHEN (g % 5) IN (0,1,2) THEN NULL ELSE (g % 60) + 1 END,
       CASE WHEN g % 6 = 0 THEN (g % 60) + 1 ELSE NULL END,
       CHOOSE((g % 5) + 1,1,1,1,2,3),
       DATEADD(DAY, -((g * 3) % 730), CAST(SYSUTCDATETIME() AS DATE)),
       CHOOSE((g % 8) + 1,'completed','completed','completed','completed','shipped','pending','cancelled','returned'),
       CHOOSE((g % 5) + 1,'web','web','web','phone','partner'),
       x.sub, CAST(x.sub * 0.05 AS DECIMAL(12,2)), CAST(x.sub * 0.08 AS DECIMAL(12,2)),
       CAST(CHOOSE((g % 5) + 1,0,0,5,9.99,19.99) AS DECIMAL(10,2)),
       CAST(x.sub * 1.03 + CHOOSE((g % 5) + 1,0,0,5,9.99,19.99) AS DECIMAL(12,2)),
       CASE WHEN g % 9 = 0 THEN CONCAT('ORD-', RIGHT('00000000' + CAST(g AS VARCHAR(20)), 8)) ELSE NULL END,
       DATEADD(HOUR, 9, CAST(DATEADD(DAY, -((g * 3) % 730), CAST(SYSUTCDATETIME() AS DATE)) AS DATETIME2))
FROM nums
CROSS APPLY (VALUES (CAST(60 + (g * 97 % 900) + (g % 100) / 100.0 AS DECIMAL(12,2)))) x(sub)
WHERE g <= 6000;

INSERT INTO order_items
  (order_id, product_id, variant_id, quantity, unit_price, discount, tax_rate_id, line_total)
SELECT (g % 6000) + 1, (g % 120) + 1,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 360) + 1 END,
       (g % 5) + 1, x.up, CAST(CHOOSE((g % 6) + 1,0,0,0,5,10,15) AS DECIMAL(5,2)),
       CASE WHEN g % 7 = 0 THEN NULL ELSE (g % 12) + 1 END,
       CAST(x.up * ((g % 5) + 1) AS DECIMAL(12,2))
FROM nums
CROSS APPLY (VALUES (CAST(25 + (g * 53 % 250) + (g % 100) / 100.0 AS DECIMAL(10,2)))) x(up)
WHERE g <= 18000;

INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
SELECT o.id, (o.id % 20) + 1, CAST(o.discount_total AS DECIMAL(12,2))
FROM orders o WHERE o.id % 3 = 0;

INSERT INTO payments (order_id, payment_method_id, amount, currency_id, status, paid_at, txn_ref)
SELECT o.id, (o.id % 6) + 1, o.total_amount, o.currency_id, 'captured',
       DATEADD(DAY, o.id % 3, o.placed_at), CONCAT('TXN', RIGHT('0000000000' + CAST(o.id AS VARCHAR(20)), 10))
FROM orders o WHERE o.status IN ('completed','shipped','returned');

INSERT INTO shipments (order_id, warehouse_id, carrier_id, tracking_number, status, shipped_at, delivered_at, weight_kg, cost)
SELECT o.id, (o.id % 8) + 1, (o.id % 6) + 1, CONCAT('TRK', RIGHT('00000000' + CAST(o.id AS VARCHAR(20)), 8)),
       CASE WHEN o.status = 'completed' THEN 'delivered' ELSE 'in_transit' END,
       DATEADD(DAY, 1, o.placed_at),
       CASE WHEN o.status = 'completed' THEN DATEADD(DAY, 2 + (o.id % 6), o.placed_at) ELSE NULL END,
       CAST(0.5 + (o.id % 100) / 10.0 AS DECIMAL(8,3)), CAST(5 + (o.id % 40) AS DECIMAL(10,2))
FROM orders o WHERE o.status IN ('completed','shipped','returned');

INSERT INTO shipment_items (shipment_id, order_item_id, quantity)
SELECT s.id, oi.id, oi.quantity
FROM shipments s JOIN order_items oi ON oi.order_id = s.order_id;

INSERT INTO [returns] (order_item_id, reason, quantity, refund_amount, status, returned_at)
SELECT oi.id, CHOOSE((oi.id % 4) + 1,'defective','wrong item','no longer needed','damaged'),
       1, oi.unit_price, CHOOSE((oi.id % 4) + 1,'approved','approved','approved','rejected'),
       DATEADD(DAY, -(oi.id % 200), SYSUTCDATETIME())
FROM order_items oi WHERE oi.id % 33 = 0;

INSERT INTO refunds (return_id, payment_id, amount, method, processed_at)
SELECT r.id,
       (SELECT TOP 1 p.id FROM payments p JOIN order_items oi ON oi.id = r.order_item_id
        WHERE p.order_id = oi.order_id),
       r.refund_amount, 'card', DATEADD(DAY, 2, r.returned_at)
FROM [returns] r WHERE r.status = 'approved';

INSERT INTO reviews (product_id, customer_id, order_id, rating, title, body, is_verified, is_hidden)
SELECT (g % 120) + 1,
       CASE WHEN g % 5 = 0 THEN NULL ELSE (g % 1500) + 1 END,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 6000) + 1 END,
       (g % 5) + 1, CONCAT('Review ', g), CONCAT('Auto-generated review body ', g),
       CASE WHEN g % 3 = 0 THEN 1 ELSE 0 END, CASE WHEN g % 29 = 0 THEN 1 ELSE 0 END
FROM nums WHERE g <= 3000;

INSERT INTO support_tickets (customer_id, order_id, employee_id, subject, status, priority, opened_at, closed_at)
SELECT (g % 1500) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE (g % 6000) + 1 END,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 60) + 1 END,
       CONCAT('Ticket ', g),
       CHOOSE((g % 5) + 1,'open','open','pending','resolved','closed'),
       CHOOSE((g % 5) + 1,'low','normal','normal','high','urgent'),
       DATEADD(DAY, -(g % 400), SYSUTCDATETIME()),
       CASE WHEN g % 5 IN (3,4) THEN DATEADD(DAY, 2 - (g % 400), SYSUTCDATETIME()) ELSE NULL END
FROM nums WHERE g <= 1500;

INSERT INTO wishlists (customer_id, product_id, added_at, note)
SELECT (g % 1500) + 1, (g % 120) + 1, DATEADD(DAY, -(g % 300), SYSUTCDATETIME()),
       CASE WHEN g % 8 = 0 THEN 'gift idea' ELSE NULL END
FROM nums WHERE g <= 2000;

INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, NULL, 'pending', (o.id % 60) + 1, o.placed_at, 'created' FROM orders o;
INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, 'pending', o.status, (o.id % 60) + 1, DATEADD(DAY, 1, o.placed_at), 'auto'
FROM orders o WHERE o.status <> 'pending';

INSERT INTO product_price_history (product_id, old_price, new_price, changed_at, reason)
SELECT p.id, CAST(p.price * 0.9 AS DECIMAL(10,2)), p.price, DATEADD(DAY, -180, SYSUTCDATETIME()), 'annual review'
FROM products p WHERE p.id % 3 = 0;

INSERT INTO sales_daily_rollup ([day], region_id, orders_count, gross_revenue, units_sold, refunds_total)
SELECT o.order_date, c.region_id, COUNT(DISTINCT o.id),
       CAST(SUM(oi.line_total) AS DECIMAL(14,2)), SUM(oi.quantity), 0
FROM orders o JOIN customers c ON c.id = o.customer_id
JOIN order_items oi ON oi.order_id = o.id
WHERE o.order_date >= DATEADD(DAY, -90, CAST(SYSUTCDATETIME() AS DATE))
GROUP BY o.order_date, c.region_id;

DROP TABLE nums;
GO

-- ══════════════════════════════════════════════════════════════════════════
--  FOREIGN KEYS (added after load; mirror the Postgres relationship graph)
-- ══════════════════════════════════════════════════════════════════════════
ALTER TABLE [brands] ADD CONSTRAINT fk_brands_country_id FOREIGN KEY (country_id) REFERENCES [countries](id);
ALTER TABLE [coupons] ADD CONSTRAINT fk_coupons_promotion_id FOREIGN KEY (promotion_id) REFERENCES [promotions](id);
ALTER TABLE [customer_addresses] ADD CONSTRAINT fk_customer_addresses_country_id FOREIGN KEY (country_id) REFERENCES [countries](id);
ALTER TABLE [customer_addresses] ADD CONSTRAINT fk_customer_addresses_customer_id FOREIGN KEY (customer_id) REFERENCES [customers](id);
ALTER TABLE [customer_addresses] ADD CONSTRAINT fk_customer_addresses_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [customers] ADD CONSTRAINT fk_customers_loyalty_tier_id FOREIGN KEY (loyalty_tier_id) REFERENCES [loyalty_tiers](id);
ALTER TABLE [customers] ADD CONSTRAINT fk_customers_referred_by_id FOREIGN KEY (referred_by_id) REFERENCES [customers](id);
ALTER TABLE [customers] ADD CONSTRAINT fk_customers_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [employee_teams] ADD CONSTRAINT fk_employee_teams_employee_id FOREIGN KEY (employee_id) REFERENCES [employees](id);
ALTER TABLE [employee_teams] ADD CONSTRAINT fk_employee_teams_team_id FOREIGN KEY (team_id) REFERENCES [teams](id);
ALTER TABLE [employees] ADD CONSTRAINT fk_employees_manager_id FOREIGN KEY (manager_id) REFERENCES [employees](id);
ALTER TABLE [employees] ADD CONSTRAINT fk_employees_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [employees] ADD CONSTRAINT fk_employees_team_id FOREIGN KEY (team_id) REFERENCES [teams](id);
ALTER TABLE [inventory] ADD CONSTRAINT fk_inventory_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [inventory] ADD CONSTRAINT fk_inventory_warehouse_id FOREIGN KEY (warehouse_id) REFERENCES [warehouses](id);
ALTER TABLE [order_items] ADD CONSTRAINT fk_order_items_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [order_items] ADD CONSTRAINT fk_order_items_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [order_items] ADD CONSTRAINT fk_order_items_tax_rate_id FOREIGN KEY (tax_rate_id) REFERENCES [tax_rates](id);
ALTER TABLE [order_items] ADD CONSTRAINT fk_order_items_variant_id FOREIGN KEY (variant_id) REFERENCES [product_variants](id);
ALTER TABLE [order_promotions] ADD CONSTRAINT fk_order_promotions_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [order_promotions] ADD CONSTRAINT fk_order_promotions_promotion_id FOREIGN KEY (promotion_id) REFERENCES [promotions](id);
ALTER TABLE [order_status_history] ADD CONSTRAINT fk_order_status_history_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [orders] ADD CONSTRAINT fk_orders_coupon_id FOREIGN KEY (coupon_id) REFERENCES [coupons](id);
ALTER TABLE [orders] ADD CONSTRAINT fk_orders_currency_id FOREIGN KEY (currency_id) REFERENCES [currencies](id);
ALTER TABLE [orders] ADD CONSTRAINT fk_orders_customer_id FOREIGN KEY (customer_id) REFERENCES [customers](id);
ALTER TABLE [orders] ADD CONSTRAINT fk_orders_employee_id FOREIGN KEY (employee_id) REFERENCES [employees](id);
ALTER TABLE [payments] ADD CONSTRAINT fk_payments_currency_id FOREIGN KEY (currency_id) REFERENCES [currencies](id);
ALTER TABLE [payments] ADD CONSTRAINT fk_payments_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [payments] ADD CONSTRAINT fk_payments_payment_method_id FOREIGN KEY (payment_method_id) REFERENCES [payment_methods](id);
ALTER TABLE [price_list_items] ADD CONSTRAINT fk_price_list_items_price_list_id FOREIGN KEY (price_list_id) REFERENCES [price_lists](id);
ALTER TABLE [price_list_items] ADD CONSTRAINT fk_price_list_items_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [price_lists] ADD CONSTRAINT fk_price_lists_currency_id FOREIGN KEY (currency_id) REFERENCES [currencies](id);
ALTER TABLE [product_price_history] ADD CONSTRAINT fk_product_price_history_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [product_suppliers] ADD CONSTRAINT fk_product_suppliers_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [product_suppliers] ADD CONSTRAINT fk_product_suppliers_supplier_id FOREIGN KEY (supplier_id) REFERENCES [suppliers](id);
ALTER TABLE [product_tags] ADD CONSTRAINT fk_product_tags_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [product_tags] ADD CONSTRAINT fk_product_tags_tag_id FOREIGN KEY (tag_id) REFERENCES [tags](id);
ALTER TABLE [product_variants] ADD CONSTRAINT fk_product_variants_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [products] ADD CONSTRAINT fk_products_brand_id FOREIGN KEY (brand_id) REFERENCES [brands](id);
ALTER TABLE [products] ADD CONSTRAINT fk_products_category_id FOREIGN KEY (category_id) REFERENCES [categories](id);
ALTER TABLE [products] ADD CONSTRAINT fk_products_subcategory_id FOREIGN KEY (subcategory_id) REFERENCES [subcategories](id);
ALTER TABLE [refunds] ADD CONSTRAINT fk_refunds_payment_id FOREIGN KEY (payment_id) REFERENCES [payments](id);
ALTER TABLE [refunds] ADD CONSTRAINT fk_refunds_return_id FOREIGN KEY (return_id) REFERENCES [returns](id);
ALTER TABLE [regions] ADD CONSTRAINT fk_regions_country_id FOREIGN KEY (country_id) REFERENCES [countries](id);
ALTER TABLE [returns] ADD CONSTRAINT fk_returns_order_item_id FOREIGN KEY (order_item_id) REFERENCES [order_items](id);
ALTER TABLE [reviews] ADD CONSTRAINT fk_reviews_customer_id FOREIGN KEY (customer_id) REFERENCES [customers](id);
ALTER TABLE [reviews] ADD CONSTRAINT fk_reviews_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [reviews] ADD CONSTRAINT fk_reviews_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
ALTER TABLE [sales_daily_rollup] ADD CONSTRAINT fk_sales_daily_rollup_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [shipment_items] ADD CONSTRAINT fk_shipment_items_order_item_id FOREIGN KEY (order_item_id) REFERENCES [order_items](id);
ALTER TABLE [shipment_items] ADD CONSTRAINT fk_shipment_items_shipment_id FOREIGN KEY (shipment_id) REFERENCES [shipments](id);
ALTER TABLE [shipments] ADD CONSTRAINT fk_shipments_carrier_id FOREIGN KEY (carrier_id) REFERENCES [carriers](id);
ALTER TABLE [shipments] ADD CONSTRAINT fk_shipments_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [shipments] ADD CONSTRAINT fk_shipments_warehouse_id FOREIGN KEY (warehouse_id) REFERENCES [warehouses](id);
ALTER TABLE [subcategories] ADD CONSTRAINT fk_subcategories_category_id FOREIGN KEY (category_id) REFERENCES [categories](id);
ALTER TABLE [suppliers] ADD CONSTRAINT fk_suppliers_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [support_tickets] ADD CONSTRAINT fk_support_tickets_customer_id FOREIGN KEY (customer_id) REFERENCES [customers](id);
ALTER TABLE [support_tickets] ADD CONSTRAINT fk_support_tickets_employee_id FOREIGN KEY (employee_id) REFERENCES [employees](id);
ALTER TABLE [support_tickets] ADD CONSTRAINT fk_support_tickets_order_id FOREIGN KEY (order_id) REFERENCES [orders](id);
ALTER TABLE [tax_rates] ADD CONSTRAINT fk_tax_rates_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [teams] ADD CONSTRAINT fk_teams_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [warehouses] ADD CONSTRAINT fk_warehouses_region_id FOREIGN KEY (region_id) REFERENCES [regions](id);
ALTER TABLE [wishlists] ADD CONSTRAINT fk_wishlists_customer_id FOREIGN KEY (customer_id) REFERENCES [customers](id);
ALTER TABLE [wishlists] ADD CONSTRAINT fk_wishlists_product_id FOREIGN KEY (product_id) REFERENCES [products](id);
GO

-- ══════════════════════════════════════════════════════════════════════════
--  READ-ONLY LOGIN  (db_datareader => SELECT only, so the write-probe fails)
-- ══════════════════════════════════════════════════════════════════════════
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'analytics_ro')
  CREATE LOGIN analytics_ro WITH PASSWORD = 'analytics_ro', CHECK_POLICY = OFF;
CREATE USER analytics_ro FOR LOGIN analytics_ro;
ALTER ROLE db_datareader ADD MEMBER analytics_ro;
GO
