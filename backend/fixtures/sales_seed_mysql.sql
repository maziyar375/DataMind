-- MySQL mirror of the Postgres `sales` fixture (backend/fixtures/sales_seed.sql).
-- Same 42-table, deliberately-messy commerce schema, with MySQL-appropriate
-- types (BIGINT AUTO_INCREMENT, DECIMAL, DATETIME, TINYINT(1) for booleans) and
-- MySQL data generation (a `nums` helper table instead of generate_series).
--
-- The design rationale, the messiness inventory, and the five bridge-table
-- question paths are documented in the Postgres file; this file mirrors it so
-- the same golden questions can be evaluated against MySQL. It is self-contained
-- (creates the `sales` database and the read-only `analytics_ro` user), so the
-- eval harness / `make fixtures` can load it into a throwaway container. It does
-- NOT touch the separate Sakila demo under fixtures/mysql/.

SET FOREIGN_KEY_CHECKS = 0;              -- load without worrying about FK order
SET SESSION cte_max_recursion_depth = 1000000;

DROP DATABASE IF EXISTS sales;
CREATE DATABASE sales CHARACTER SET utf8mb4;
USE sales;

-- Numbers helper (dropped before the read-only user is created, so it is not
-- part of the mirrored schema).
CREATE TABLE nums (g INT PRIMARY KEY);
INSERT INTO nums (g)
SELECT g FROM (
  WITH RECURSIVE s (g) AS (SELECT 1 UNION ALL SELECT g + 1 FROM s WHERE g < 20000)
  SELECT g FROM s
) d;

-- ── dimension / reference tables ────────────────────────────────────────────
CREATE TABLE countries (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  iso_code      VARCHAR(10) NOT NULL UNIQUE,
  name          VARCHAR(255) NOT NULL,
  continent     VARCHAR(50) NOT NULL,
  currency_code VARCHAR(10),
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE regions (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  country_id BIGINT,
  name       VARCHAR(255) NOT NULL,
  code       VARCHAR(20),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE categories (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(255) NOT NULL,
  description TEXT,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE subcategories (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  category_id BIGINT NOT NULL,
  name        VARCHAR(255) NOT NULL,
  description TEXT,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE brands (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(255) NOT NULL,
  country_id    BIGINT,
  website       VARCHAR(255),
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  source_system VARCHAR(50),
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE products (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  category       VARCHAR(100) NOT NULL,
  category_id    BIGINT,
  subcategory_id BIGINT,
  brand_id       BIGINT,
  sku            VARCHAR(50) UNIQUE,
  price          DECIMAL(10,2) NOT NULL,
  cost           DECIMAL(10,2),
  weight_kg      DECIMAL(8,3),
  active         TINYINT(1) NOT NULL DEFAULT 1,
  discontinued   TINYINT(1) NOT NULL DEFAULT 0,
  flg_2          TINYINT(1) NOT NULL DEFAULT 0,
  cust_ref       VARCHAR(100),
  launched_at    DATE,
  source_system  VARCHAR(50),
  external_ref   VARCHAR(100),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

-- near-duplicate name (see Postgres header)
CREATE TABLE product (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(255),
  cust_ref   VARCHAR(100),
  flg_2      TINYINT(1),
  old_price  DECIMAL(10,2),
  note       TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE product_variants (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id  BIGINT NOT NULL,
  variant_sku VARCHAR(50) UNIQUE,
  color       VARCHAR(50),
  size        VARCHAR(50),
  extra_price DECIMAL(10,2) NOT NULL DEFAULT 0,
  barcode     VARCHAR(50),
  active      TINYINT(1) NOT NULL DEFAULT 1,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE suppliers (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  region_id      BIGINT,
  contact_email  VARCHAR(255),
  phone          VARCHAR(50),
  lead_time_days INT NOT NULL DEFAULT 7,
  rating         DECIMAL(3,2),
  active         TINYINT(1) NOT NULL DEFAULT 1,
  address_line1  VARCHAR(255),
  city           VARCHAR(100),
  postal_code    VARCHAR(20),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE warehouses (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(255) NOT NULL,
  region_id     BIGINT,
  capacity      INT NOT NULL,
  address_line1 VARCHAR(255),
  city          VARCHAR(100),
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE teams (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(255) NOT NULL,
  region_id  BIGINT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE employees (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  name           VARCHAR(255) NOT NULL,
  title          VARCHAR(100) NOT NULL,
  region_id      BIGINT,
  team_id        BIGINT,
  manager_id     BIGINT,
  email          VARCHAR(255),
  hired_at       DATE NOT NULL,
  terminated_at  DATE,
  salary         DECIMAL(10,2) NOT NULL,
  commission_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  active         TINYINT(1) NOT NULL DEFAULT 1,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE loyalty_tiers (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(100) NOT NULL,
  min_points   INT NOT NULL DEFAULT 0,
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE customers (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  name            VARCHAR(255) NOT NULL,
  email           VARCHAR(255),
  phone           VARCHAR(50),
  region_id       BIGINT,
  loyalty_tier_id BIGINT,
  referred_by_id  BIGINT,
  segment         VARCHAR(50) NOT NULL DEFAULT 'SMB',
  credit_limit    DECIMAL(12,2),
  cust_ref        VARCHAR(100),
  signed_up_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_order_at   DATETIME,
  is_deleted      TINYINT(1) NOT NULL DEFAULT 0,
  deleted_at      DATETIME,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE customer_addresses (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  kind        VARCHAR(20) NOT NULL DEFAULT 'shipping',
  line1       VARCHAR(255) NOT NULL,
  line2       VARCHAR(255),
  city        VARCHAR(100),
  region_id   BIGINT,
  postal_code VARCHAR(20),
  country_id  BIGINT,
  is_primary  TINYINT(1) NOT NULL DEFAULT 0,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE currencies (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(10) NOT NULL UNIQUE,
  name       VARCHAR(100) NOT NULL,
  symbol     VARCHAR(10),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE price_lists (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(255) NOT NULL,
  currency_id BIGINT,
  valid_from  DATE NOT NULL,
  valid_to    DATE,
  is_active   TINYINT(1) NOT NULL DEFAULT 1,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE tax_rates (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  region_id  BIGINT,
  name       VARCHAR(100) NOT NULL,
  rate_pct   DECIMAL(5,2) NOT NULL,
  valid_from DATE NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE promotions (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(255) NOT NULL,
  description  TEXT,
  promo_type   VARCHAR(50) NOT NULL DEFAULT 'percent',
  discount_pct DECIMAL(5,2) NOT NULL DEFAULT 0,
  budget       DECIMAL(12,2),
  starts_on    DATE NOT NULL,
  ends_on      DATE,
  is_active    TINYINT(1) NOT NULL DEFAULT 1,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE coupons (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  promotion_id BIGINT,
  code         VARCHAR(50) NOT NULL UNIQUE,
  max_uses     INT NOT NULL DEFAULT 1,
  times_used   INT NOT NULL DEFAULT 0,
  expires_on   DATE,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE tags (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(100) NOT NULL UNIQUE,
  kind       VARCHAR(50) NOT NULL DEFAULT 'attribute',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE carriers (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(100) NOT NULL,
  tracking_url VARCHAR(255),
  is_active    TINYINT(1) NOT NULL DEFAULT 1,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE payment_methods (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(100) NOT NULL,
  kind       VARCHAR(50) NOT NULL DEFAULT 'card',
  is_active  TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

-- ── junction / bridge tables (kept narrow) ──────────────────────────────────
CREATE TABLE product_suppliers (
  product_id     BIGINT NOT NULL,
  supplier_id    BIGINT NOT NULL,
  cost           DECIMAL(10,2) NOT NULL,
  is_preferred   TINYINT(1) NOT NULL DEFAULT 0,
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
  role_in_team VARCHAR(50) NOT NULL DEFAULT 'member',
  assigned_on  DATE NOT NULL,
  PRIMARY KEY (employee_id, team_id)
);

CREATE TABLE inventory (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id    BIGINT NOT NULL,
  warehouse_id  BIGINT NOT NULL,
  quantity      INT NOT NULL,
  reorder_level INT NOT NULL DEFAULT 10,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (product_id, warehouse_id)
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
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id    BIGINT,
  employee_id    BIGINT,
  coupon_id      BIGINT,
  currency_id    BIGINT,
  order_date     DATE NOT NULL,
  status         VARCHAR(30) NOT NULL,
  channel        VARCHAR(30) NOT NULL DEFAULT 'web',
  subtotal       DECIMAL(12,2) NOT NULL,
  discount_total DECIMAL(12,2) NOT NULL DEFAULT 0,
  tax_total      DECIMAL(12,2) NOT NULL DEFAULT 0,
  shipping_fee   DECIMAL(10,2) NOT NULL DEFAULT 0,
  total_amount   DECIMAL(12,2) NOT NULL,
  cust_ref       VARCHAR(100),
  notes          TEXT,
  placed_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE order_items (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id    BIGINT,
  product_id  BIGINT,
  variant_id  BIGINT,
  quantity    INT NOT NULL,
  unit_price  DECIMAL(10,2) NOT NULL,
  discount    DECIMAL(5,2) NOT NULL DEFAULT 0,
  tax_rate_id BIGINT,
  line_total  DECIMAL(12,2) NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE payments (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id          BIGINT,
  payment_method_id BIGINT,
  amount            DECIMAL(12,2) NOT NULL,
  currency_id       BIGINT,
  status            VARCHAR(30) NOT NULL DEFAULT 'captured',
  paid_at           DATETIME NOT NULL,
  txn_ref           VARCHAR(50),
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE shipments (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id        BIGINT,
  warehouse_id    BIGINT,
  carrier_id      BIGINT,
  tracking_number VARCHAR(50),
  status          VARCHAR(30) NOT NULL DEFAULT 'in_transit',
  shipped_at      DATETIME NOT NULL,
  delivered_at    DATETIME,
  weight_kg       DECIMAL(8,3),
  cost            DECIMAL(10,2),
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE returns (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_item_id BIGINT,
  reason        VARCHAR(100) NOT NULL,
  quantity      INT NOT NULL,
  refund_amount DECIMAL(10,2) NOT NULL,
  status        VARCHAR(30) NOT NULL DEFAULT 'approved',
  returned_at   DATETIME NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE refunds (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  return_id    BIGINT,
  payment_id   BIGINT,
  amount       DECIMAL(10,2) NOT NULL,
  method       VARCHAR(30) NOT NULL DEFAULT 'card',
  processed_at DATETIME NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE reviews (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id  BIGINT,
  customer_id BIGINT,
  order_id    BIGINT,
  rating      INT NOT NULL,
  title       VARCHAR(255),
  body        TEXT,
  is_verified TINYINT(1) NOT NULL DEFAULT 0,
  is_hidden   TINYINT(1) NOT NULL DEFAULT 0,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE support_tickets (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT,
  order_id    BIGINT,
  employee_id BIGINT,
  subject     VARCHAR(255) NOT NULL,
  status      VARCHAR(30) NOT NULL DEFAULT 'open',
  priority    VARCHAR(30) NOT NULL DEFAULT 'normal',
  opened_at   DATETIME NOT NULL,
  closed_at   DATETIME,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE wishlists (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  product_id  BIGINT NOT NULL,
  added_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  note        TEXT,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE order_status_history (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id   BIGINT NOT NULL,
  old_status VARCHAR(30),
  new_status VARCHAR(30) NOT NULL,
  changed_by BIGINT,
  changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  note       TEXT,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE product_price_history (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT NOT NULL,
  old_price  DECIMAL(10,2),
  new_price  DECIMAL(10,2) NOT NULL,
  changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reason     VARCHAR(255),
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

CREATE TABLE sales_daily_rollup (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  day           DATE NOT NULL,
  region_id     BIGINT,
  orders_count  INT NOT NULL DEFAULT 0,
  gross_revenue DECIMAL(14,2) NOT NULL DEFAULT 0,
  units_sold    INT NOT NULL DEFAULT 0,
  refunds_total DECIMAL(14,2) NOT NULL DEFAULT 0,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by BIGINT, updated_by BIGINT, src_system VARCHAR(50),
  src_batch_id VARCHAR(50), ext_ref VARCHAR(100),
  is_archived TINYINT(1) DEFAULT 0, row_version INT DEFAULT 1, audit_notes TEXT
);

-- ══════════════════════════════════════════════════════════════════════════
--  DATA  (nums drives the generated volumes; ELT()/MOD() replace PG arrays)
-- ══════════════════════════════════════════════════════════════════════════

INSERT INTO countries (iso_code, name, continent, currency_code)
SELECT CONCAT('C', LPAD(g,2,'0')), CONCAT('Country ', g),
       ELT((g % 6) + 1,'North America','Europe','Asia','South America','Africa','Oceania'),
       ELT((g % 6) + 1,'USD','EUR','GBP','JPY','BRL','AUD')
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
       ELT((g % 3) + 1,'erp','pim','legacy')
FROM nums WHERE g <= 18;

INSERT INTO products
  (name, category, category_id, subcategory_id, brand_id, sku, price, cost,
   weight_kg, active, discontinued, flg_2, cust_ref, launched_at, source_system, external_ref)
SELECT CONCAT(ELT((g % 6) + 1,'Accessories','Peripherals','Displays','Audio','Networking','Storage'),' Model ', g),
       ELT((g % 6) + 1,'Accessories','Peripherals','Displays','Audio','Networking','Storage'),
       (g % 6) + 1,
       CASE WHEN g % 7 = 0 THEN NULL ELSE (g % 20) + 1 END,
       CASE WHEN g % 9 = 0 THEN NULL ELSE (g % 18) + 1 END,
       CONCAT('SKU-', LPAD(g,5,'0')),
       ROUND(RAND()*380 + 20, 2), ROUND(RAND()*180 + 10, 2), ROUND(RAND()*4 + 0.1, 3),
       (g % 17 <> 0), (g % 23 = 0), (g % 2 = 0),
       CASE WHEN g % 4 = 0 THEN CONCAT('LEG-', LPAD(g,6,'0')) ELSE NULL END,
       CURDATE() - INTERVAL ((g * 13) % 1500) DAY,
       ELT((g % 3) + 1,'erp','pim','legacy'), CONCAT('EXT', LPAD(g,6,'0'))
FROM nums WHERE g <= 120;

INSERT INTO product (name, cust_ref, flg_2, old_price, note) VALUES
  ('Legacy Widget A','OLD-000001',1,19.99,'do not use - see products'),
  ('Legacy Widget B','OLD-000002',0,29.99,'migrated 2019'),
  ('Legacy Widget C',NULL,1,9.99,NULL),
  ('Legacy Widget D','OLD-000004',0,49.99,'kept for the quarterly PDF'),
  ('Legacy Widget E','OLD-000005',NULL,14.99,NULL),
  ('Legacy Widget F','OLD-000006',1,99.99,'ghost row');

INSERT INTO product_variants (product_id, variant_sku, color, size, extra_price, barcode)
SELECT ((g - 1) DIV 3) + 1, CONCAT('VAR-', LPAD(g,6,'0')),
       ELT((g % 5) + 1,'black','white','silver','blue','red'),
       ELT((g % 5) + 1,'S','M','L','XL','one-size'),
       ROUND((g % 4) * 5, 2), CONCAT('BC', LPAD(g,10,'0'))
FROM nums WHERE g <= 360;

INSERT INTO suppliers (name, region_id, contact_email, phone, lead_time_days, rating, address_line1, city, postal_code)
SELECT CONCAT('Supplier ', g),
       CASE WHEN g % 8 = 0 THEN NULL ELSE (g % 8) + 1 END,
       CONCAT('sales@supplier', g, '.example'),
       CONCAT('+1-555-', LPAD(g,4,'0')), (g % 21) + 3, ROUND(RAND()*2 + 3, 2),
       CONCAT(g, ' Industrial Way'), CONCAT('City ', (g % 8) + 1), LPAD((g * 137) % 99999,5,'0')
FROM nums WHERE g <= 30;

INSERT INTO warehouses (name, region_id, capacity, address_line1, city)
SELECT CONCAT(r.name, ' DC'), r.id, 5000 + (r.id * 1500),
       CONCAT(r.id, ' Distribution Blvd'), r.name
FROM regions r;

INSERT INTO teams (name, region_id)
SELECT ELT(g,'Field Sales','Inside Sales','Enterprise','SMB','Partnerships','Renewals','Named Accounts','Growth'),
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 8) + 1 END
FROM nums WHERE g <= 8;

INSERT INTO employees
  (name, title, region_id, team_id, manager_id, email, hired_at, terminated_at, salary, commission_pct, active)
SELECT CONCAT('Employee ', g),
       ELT((g % 6) + 1,'Sales Rep','Sales Rep','Sales Rep','Account Manager','Regional Manager','Director'),
       (g % 8) + 1,
       CASE WHEN g % 6 = 0 THEN NULL ELSE (g % 8) + 1 END,
       CASE WHEN g <= 6 THEN NULL ELSE (g % 6) + 1 END,
       CONCAT('employee', g, '@datamind.example'),
       CURDATE() - INTERVAL ((g * 47) % 2500) DAY,
       CASE WHEN g % 19 = 0 THEN CURDATE() - INTERVAL ((g * 7) % 300) DAY ELSE NULL END,
       ROUND(RAND()*60000 + 45000, 2), ROUND((g % 5) * 1.5, 2), (g % 19 <> 0)
FROM nums WHERE g <= 60;

INSERT INTO loyalty_tiers (name, min_points, discount_pct) VALUES
  ('Bronze',0,0), ('Silver',1000,2.5), ('Gold',5000,5), ('Platinum',20000,10);

INSERT INTO customers
  (name, email, phone, region_id, loyalty_tier_id, referred_by_id, segment,
   credit_limit, cust_ref, signed_up_at, last_order_at, is_deleted, deleted_at)
SELECT CONCAT('Customer ', g), CONCAT('customer', g, '@example.com'),
       CONCAT('+1-555-', LPAD(g % 10000,4,'0')), (g % 8) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE (g % 4) + 1 END,
       CASE WHEN g > 50 AND g % 7 = 0 THEN (g % 50) + 1 ELSE NULL END,
       ELT((g % 4) + 1,'SMB','SMB','Mid-Market','Enterprise'),
       ROUND(RAND()*40000 + 1000, 2),
       CASE WHEN g % 5 = 0 THEN CONCAT('CRM-', LPAD(g,7,'0')) ELSE NULL END,
       NOW() - INTERVAL (g % 1000) DAY,
       CASE WHEN g % 6 = 0 THEN NULL ELSE NOW() - INTERVAL (g % 200) DAY END,
       (g % 13 = 0),
       CASE WHEN g % 13 = 0 THEN NOW() - INTERVAL (g % 90) DAY ELSE NULL END
FROM nums WHERE g <= 1500;

INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'shipping', CONCAT(c.id, ' Main St'), CONCAT('City ', (c.id % 8) + 1),
       c.region_id, LPAD((c.id * 91) % 99999,5,'0'), (c.id % 25) + 1, 1
FROM customers c;
INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'billing', CONCAT(c.id, ' Finance Ave'), CONCAT('City ', (c.id % 8) + 1),
       c.region_id, LPAD((c.id * 57) % 99999,5,'0'), (c.id % 25) + 1, 0
FROM customers c WHERE c.id % 2 = 0;

INSERT INTO currencies (code, name, symbol) VALUES
  ('USD','US Dollar','$'), ('EUR','Euro','€'), ('GBP','Pound Sterling','£'),
  ('JPY','Japanese Yen','¥'), ('BRL','Brazilian Real','R$'),
  ('AUD','Australian Dollar','A$'), ('CAD','Canadian Dollar','C$'), ('INR','Indian Rupee','₹');

INSERT INTO price_lists (name, currency_id, valid_from, valid_to, is_active) VALUES
  ('Standard USD',1, CURDATE() - INTERVAL 400 DAY, NULL, 1),
  ('EU Retail',2, CURDATE() - INTERVAL 400 DAY, NULL, 1),
  ('UK Retail',3, CURDATE() - INTERVAL 400 DAY, NULL, 1),
  ('Legacy 2023',1, CURDATE() - INTERVAL 800 DAY, CURDATE() - INTERVAL 365 DAY, 0),
  ('Enterprise',1, CURDATE() - INTERVAL 200 DAY, NULL, 1);

INSERT INTO tax_rates (region_id, name, rate_pct, valid_from)
SELECT (g % 8) + 1, CONCAT('Standard ', g), ROUND((g % 5) * 2.5 + 5, 2), CURDATE() - INTERVAL 500 DAY
FROM nums WHERE g <= 12;

INSERT INTO promotions (name, description, promo_type, discount_pct, budget, starts_on, ends_on, is_active)
SELECT CONCAT('Promo ', g), CONCAT('Auto promotion ', g),
       ELT((g % 4) + 1,'percent','percent','bogo','fixed'),
       ROUND((g % 6) * 5 + 5, 2), ROUND(RAND()*50000 + 5000, 2),
       CURDATE() - INTERVAL ((g * 30) % 700) DAY,
       CURDATE() - INTERVAL ((g * 30) % 700) DAY + INTERVAL 45 DAY,
       (g % 4 <> 0)
FROM nums WHERE g <= 20;

INSERT INTO coupons (promotion_id, code, max_uses, times_used, expires_on)
SELECT (g % 20) + 1, CONCAT('CPN-', LPAD(g,6,'0')), (g % 5) * 100 + 1, (g % 37),
       CURDATE() + INTERVAL (g % 120) DAY
FROM nums WHERE g <= 60;

INSERT INTO tags (name, kind)
SELECT ELT(g,'bestseller','clearance','new','eco','premium','bulk','fragile',
           'refurb','bundle','limited','gaming','office','travel','wireless',
           'usb-c','4k','rgb','compact','heavy-duty','warranty-3y','warranty-1y',
           'imported','local','seasonal','staff-pick'),
       ELT((g % 4) + 1,'merch','merch','lifecycle','attribute')
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
SELECT g, (g % 30) + 1, ROUND(RAND()*200 + 10, 2), 1, (g % 15) + 3 FROM nums WHERE g <= 120;
INSERT INTO product_suppliers (product_id, supplier_id, cost, is_preferred, lead_time_days)
SELECT g, ((g + 11) % 30) + 1, ROUND(RAND()*200 + 10, 2), 0, (g % 20) + 5
FROM nums WHERE g <= 120 AND g % 2 = 0 AND ((g % 30) + 1) <> (((g + 11) % 30) + 1);

INSERT INTO price_list_items (price_list_id, product_id, unit_price)
SELECT pl.id, p.id, ROUND(p.price * (1 + (pl.id / 20)), 2)
FROM price_lists pl JOIN products p
WHERE pl.id IN (1,2,3,5) OR (pl.id = 4 AND p.id % 3 = 0);

INSERT INTO product_tags (product_id, tag_id)
SELECT DISTINCT p.g, ((p.g * 7 + s.g * 13) % 25) + 1
FROM (SELECT g FROM nums WHERE g <= 120) p
JOIN (SELECT g FROM nums WHERE g <= 4) s;

INSERT INTO employee_teams (employee_id, team_id, role_in_team, assigned_on)
SELECT g, (g % 8) + 1, CASE WHEN g % 6 = 0 THEN 'lead' ELSE 'member' END, CURDATE()
FROM nums WHERE g <= 60;
INSERT INTO employee_teams (employee_id, team_id, role_in_team, assigned_on)
SELECT g, ((g + 3) % 8) + 1, 'member', CURDATE()
FROM nums WHERE g <= 60 AND g % 3 = 0 AND ((g % 8) + 1) <> (((g + 3) % 8) + 1);

INSERT INTO inventory (product_id, warehouse_id, quantity, reorder_level)
SELECT p.id, w.id, FLOOR(RAND()*500), 10 + (p.id % 40)
FROM products p JOIN warehouses w;

-- fact data
INSERT INTO orders
  (customer_id, employee_id, coupon_id, currency_id, order_date, status, channel,
   subtotal, discount_total, tax_total, shipping_fee, total_amount, cust_ref, placed_at)
SELECT (g % 1500) + 1,
       CASE WHEN (g % 5) IN (0,1,2) THEN NULL ELSE (g % 60) + 1 END,
       CASE WHEN g % 6 = 0 THEN (g % 60) + 1 ELSE NULL END,
       ELT((g % 5) + 1,1,1,1,2,3),
       CURDATE() - INTERVAL ((g * 3) % 730) DAY,
       ELT((g % 8) + 1,'completed','completed','completed','completed','shipped','pending','cancelled','returned'),
       ELT((g % 5) + 1,'web','web','web','phone','partner'),
       sub, ROUND(sub * 0.05, 2), ROUND(sub * 0.08, 2), ELT((g % 5) + 1,0,0,5,9.99,19.99),
       ROUND(sub * 1.03 + ELT((g % 5) + 1,0,0,5,9.99,19.99), 2),
       CASE WHEN g % 9 = 0 THEN CONCAT('ORD-', LPAD(g,8,'0')) ELSE NULL END,
       (CURDATE() - INTERVAL ((g * 3) % 730) DAY) + INTERVAL 9 HOUR
FROM (SELECT g, ROUND(RAND()*900 + 60, 2) AS sub FROM nums WHERE g <= 6000) x;

INSERT INTO order_items
  (order_id, product_id, variant_id, quantity, unit_price, discount, tax_rate_id, line_total)
SELECT (g % 6000) + 1, (g % 120) + 1,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 360) + 1 END,
       (g % 5) + 1, up, ELT((g % 6) + 1,0,0,0,5,10,15),
       CASE WHEN g % 7 = 0 THEN NULL ELSE (g % 12) + 1 END,
       ROUND(up * ((g % 5) + 1), 2)
FROM (SELECT g, ROUND(RAND()*250 + 25, 2) AS up FROM nums WHERE g <= 18000) x;

INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
SELECT o.id, (o.id % 20) + 1, ROUND(o.discount_total, 2)
FROM orders o WHERE o.id % 3 = 0;

INSERT INTO payments (order_id, payment_method_id, amount, currency_id, status, paid_at, txn_ref)
SELECT o.id, (o.id % 6) + 1, o.total_amount, o.currency_id, 'captured',
       o.placed_at + INTERVAL (o.id % 3) DAY, CONCAT('TXN', LPAD(o.id,10,'0'))
FROM orders o WHERE o.status IN ('completed','shipped','returned');

INSERT INTO shipments (order_id, warehouse_id, carrier_id, tracking_number, status, shipped_at, delivered_at, weight_kg, cost)
SELECT o.id, (o.id % 8) + 1, (o.id % 6) + 1, CONCAT('TRK', LPAD(o.id,8,'0')),
       CASE WHEN o.status = 'completed' THEN 'delivered' ELSE 'in_transit' END,
       o.placed_at + INTERVAL 1 DAY,
       CASE WHEN o.status = 'completed' THEN o.placed_at + INTERVAL (2 + (o.id % 6)) DAY ELSE NULL END,
       ROUND(RAND()*10 + 0.5, 3), ROUND(RAND()*40 + 5, 2)
FROM orders o WHERE o.status IN ('completed','shipped','returned');

INSERT INTO shipment_items (shipment_id, order_item_id, quantity)
SELECT s.id, oi.id, oi.quantity
FROM shipments s JOIN order_items oi ON oi.order_id = s.order_id;

INSERT INTO returns (order_item_id, reason, quantity, refund_amount, status, returned_at)
SELECT oi.id, ELT((oi.id % 4) + 1,'defective','wrong item','no longer needed','damaged'),
       1, oi.unit_price, ELT((oi.id % 4) + 1,'approved','approved','approved','rejected'),
       NOW() - INTERVAL (oi.id % 200) DAY
FROM order_items oi WHERE oi.id % 33 = 0;

INSERT INTO refunds (return_id, payment_id, amount, method, processed_at)
SELECT r.id,
       (SELECT p.id FROM payments p JOIN order_items oi ON oi.id = r.order_item_id
        WHERE p.order_id = oi.order_id LIMIT 1),
       r.refund_amount, 'card', r.returned_at + INTERVAL 2 DAY
FROM returns r WHERE r.status = 'approved';

INSERT INTO reviews (product_id, customer_id, order_id, rating, title, body, is_verified, is_hidden)
SELECT (g % 120) + 1,
       CASE WHEN g % 5 = 0 THEN NULL ELSE (g % 1500) + 1 END,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 6000) + 1 END,
       (g % 5) + 1, CONCAT('Review ', g), CONCAT('Auto-generated review body ', g),
       (g % 3 = 0), (g % 29 = 0)
FROM nums WHERE g <= 3000;

INSERT INTO support_tickets (customer_id, order_id, employee_id, subject, status, priority, opened_at, closed_at)
SELECT (g % 1500) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE (g % 6000) + 1 END,
       CASE WHEN g % 4 = 0 THEN NULL ELSE (g % 60) + 1 END,
       CONCAT('Ticket ', g),
       ELT((g % 5) + 1,'open','open','pending','resolved','closed'),
       ELT((g % 5) + 1,'low','normal','normal','high','urgent'),
       NOW() - INTERVAL (g % 400) DAY,
       CASE WHEN g % 5 IN (3,4) THEN NOW() - INTERVAL (g % 400) DAY + INTERVAL 2 DAY ELSE NULL END
FROM nums WHERE g <= 1500;

INSERT INTO wishlists (customer_id, product_id, added_at, note)
SELECT (g % 1500) + 1, (g % 120) + 1, NOW() - INTERVAL (g % 300) DAY,
       CASE WHEN g % 8 = 0 THEN 'gift idea' ELSE NULL END
FROM nums WHERE g <= 2000;

INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, NULL, 'pending', (o.id % 60) + 1, o.placed_at, 'created' FROM orders o;
INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, 'pending', o.status, (o.id % 60) + 1, o.placed_at + INTERVAL 1 DAY, 'auto'
FROM orders o WHERE o.status <> 'pending';

INSERT INTO product_price_history (product_id, old_price, new_price, changed_at, reason)
SELECT p.id, ROUND(p.price * 0.9, 2), p.price, NOW() - INTERVAL 180 DAY, 'annual review'
FROM products p WHERE p.id % 3 = 0;

INSERT INTO sales_daily_rollup (day, region_id, orders_count, gross_revenue, units_sold, refunds_total)
SELECT o.order_date, c.region_id, COUNT(DISTINCT o.id),
       ROUND(SUM(oi.line_total), 2), SUM(oi.quantity), 0
FROM orders o JOIN customers c ON c.id = o.customer_id
JOIN order_items oi ON oi.order_id = o.id
WHERE o.order_date >= CURDATE() - INTERVAL 90 DAY
GROUP BY o.order_date, c.region_id;

DROP TABLE nums;

-- Foreign keys, added after load (FOREIGN_KEY_CHECKS is still 0, so no
-- validation pass). They exist so schema introspection reports the same
-- relationship graph the Postgres fixture does.
ALTER TABLE brands ADD CONSTRAINT fk_brands_country_id FOREIGN KEY (country_id) REFERENCES countries(id);
ALTER TABLE coupons ADD CONSTRAINT fk_coupons_promotion_id FOREIGN KEY (promotion_id) REFERENCES promotions(id);
ALTER TABLE customer_addresses ADD CONSTRAINT fk_customer_addresses_country_id FOREIGN KEY (country_id) REFERENCES countries(id);
ALTER TABLE customer_addresses ADD CONSTRAINT fk_customer_addresses_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE customer_addresses ADD CONSTRAINT fk_customer_addresses_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE customers ADD CONSTRAINT fk_customers_loyalty_tier_id FOREIGN KEY (loyalty_tier_id) REFERENCES loyalty_tiers(id);
ALTER TABLE customers ADD CONSTRAINT fk_customers_referred_by_id FOREIGN KEY (referred_by_id) REFERENCES customers(id);
ALTER TABLE customers ADD CONSTRAINT fk_customers_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE employee_teams ADD CONSTRAINT fk_employee_teams_employee_id FOREIGN KEY (employee_id) REFERENCES employees(id);
ALTER TABLE employee_teams ADD CONSTRAINT fk_employee_teams_team_id FOREIGN KEY (team_id) REFERENCES teams(id);
ALTER TABLE employees ADD CONSTRAINT fk_employees_manager_id FOREIGN KEY (manager_id) REFERENCES employees(id);
ALTER TABLE employees ADD CONSTRAINT fk_employees_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE employees ADD CONSTRAINT fk_employees_team_id FOREIGN KEY (team_id) REFERENCES teams(id);
ALTER TABLE inventory ADD CONSTRAINT fk_inventory_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE inventory ADD CONSTRAINT fk_inventory_warehouse_id FOREIGN KEY (warehouse_id) REFERENCES warehouses(id);
ALTER TABLE order_items ADD CONSTRAINT fk_order_items_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE order_items ADD CONSTRAINT fk_order_items_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE order_items ADD CONSTRAINT fk_order_items_tax_rate_id FOREIGN KEY (tax_rate_id) REFERENCES tax_rates(id);
ALTER TABLE order_items ADD CONSTRAINT fk_order_items_variant_id FOREIGN KEY (variant_id) REFERENCES product_variants(id);
ALTER TABLE order_promotions ADD CONSTRAINT fk_order_promotions_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE order_promotions ADD CONSTRAINT fk_order_promotions_promotion_id FOREIGN KEY (promotion_id) REFERENCES promotions(id);
ALTER TABLE order_status_history ADD CONSTRAINT fk_order_status_history_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE orders ADD CONSTRAINT fk_orders_coupon_id FOREIGN KEY (coupon_id) REFERENCES coupons(id);
ALTER TABLE orders ADD CONSTRAINT fk_orders_currency_id FOREIGN KEY (currency_id) REFERENCES currencies(id);
ALTER TABLE orders ADD CONSTRAINT fk_orders_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE orders ADD CONSTRAINT fk_orders_employee_id FOREIGN KEY (employee_id) REFERENCES employees(id);
ALTER TABLE payments ADD CONSTRAINT fk_payments_currency_id FOREIGN KEY (currency_id) REFERENCES currencies(id);
ALTER TABLE payments ADD CONSTRAINT fk_payments_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE payments ADD CONSTRAINT fk_payments_payment_method_id FOREIGN KEY (payment_method_id) REFERENCES payment_methods(id);
ALTER TABLE price_list_items ADD CONSTRAINT fk_price_list_items_price_list_id FOREIGN KEY (price_list_id) REFERENCES price_lists(id);
ALTER TABLE price_list_items ADD CONSTRAINT fk_price_list_items_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE price_lists ADD CONSTRAINT fk_price_lists_currency_id FOREIGN KEY (currency_id) REFERENCES currencies(id);
ALTER TABLE product_price_history ADD CONSTRAINT fk_product_price_history_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE product_suppliers ADD CONSTRAINT fk_product_suppliers_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE product_suppliers ADD CONSTRAINT fk_product_suppliers_supplier_id FOREIGN KEY (supplier_id) REFERENCES suppliers(id);
ALTER TABLE product_tags ADD CONSTRAINT fk_product_tags_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE product_tags ADD CONSTRAINT fk_product_tags_tag_id FOREIGN KEY (tag_id) REFERENCES tags(id);
ALTER TABLE product_variants ADD CONSTRAINT fk_product_variants_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE products ADD CONSTRAINT fk_products_brand_id FOREIGN KEY (brand_id) REFERENCES brands(id);
ALTER TABLE products ADD CONSTRAINT fk_products_category_id FOREIGN KEY (category_id) REFERENCES categories(id);
ALTER TABLE products ADD CONSTRAINT fk_products_subcategory_id FOREIGN KEY (subcategory_id) REFERENCES subcategories(id);
ALTER TABLE refunds ADD CONSTRAINT fk_refunds_payment_id FOREIGN KEY (payment_id) REFERENCES payments(id);
ALTER TABLE refunds ADD CONSTRAINT fk_refunds_return_id FOREIGN KEY (return_id) REFERENCES returns(id);
ALTER TABLE regions ADD CONSTRAINT fk_regions_country_id FOREIGN KEY (country_id) REFERENCES countries(id);
ALTER TABLE returns ADD CONSTRAINT fk_returns_order_item_id FOREIGN KEY (order_item_id) REFERENCES order_items(id);
ALTER TABLE reviews ADD CONSTRAINT fk_reviews_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE reviews ADD CONSTRAINT fk_reviews_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE reviews ADD CONSTRAINT fk_reviews_product_id FOREIGN KEY (product_id) REFERENCES products(id);
ALTER TABLE sales_daily_rollup ADD CONSTRAINT fk_sales_daily_rollup_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE shipment_items ADD CONSTRAINT fk_shipment_items_order_item_id FOREIGN KEY (order_item_id) REFERENCES order_items(id);
ALTER TABLE shipment_items ADD CONSTRAINT fk_shipment_items_shipment_id FOREIGN KEY (shipment_id) REFERENCES shipments(id);
ALTER TABLE shipments ADD CONSTRAINT fk_shipments_carrier_id FOREIGN KEY (carrier_id) REFERENCES carriers(id);
ALTER TABLE shipments ADD CONSTRAINT fk_shipments_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE shipments ADD CONSTRAINT fk_shipments_warehouse_id FOREIGN KEY (warehouse_id) REFERENCES warehouses(id);
ALTER TABLE subcategories ADD CONSTRAINT fk_subcategories_category_id FOREIGN KEY (category_id) REFERENCES categories(id);
ALTER TABLE suppliers ADD CONSTRAINT fk_suppliers_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE support_tickets ADD CONSTRAINT fk_support_tickets_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE support_tickets ADD CONSTRAINT fk_support_tickets_employee_id FOREIGN KEY (employee_id) REFERENCES employees(id);
ALTER TABLE support_tickets ADD CONSTRAINT fk_support_tickets_order_id FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE tax_rates ADD CONSTRAINT fk_tax_rates_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE teams ADD CONSTRAINT fk_teams_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE warehouses ADD CONSTRAINT fk_warehouses_region_id FOREIGN KEY (region_id) REFERENCES regions(id);
ALTER TABLE wishlists ADD CONSTRAINT fk_wishlists_customer_id FOREIGN KEY (customer_id) REFERENCES customers(id);
ALTER TABLE wishlists ADD CONSTRAINT fk_wishlists_product_id FOREIGN KEY (product_id) REFERENCES products(id);

SET FOREIGN_KEY_CHECKS = 1;

-- Read-only role: SELECT only, so the connector's write-probe fails and
-- readonly_confirmed is true. mysql_native_password keeps aiomysql happy.
CREATE USER IF NOT EXISTS 'analytics_ro'@'%'
  IDENTIFIED WITH mysql_native_password BY 'analytics_ro';
GRANT SELECT ON sales.* TO 'analytics_ro'@'%';
FLUSH PRIVILEGES;
