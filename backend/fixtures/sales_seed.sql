-- Seeded "sales" fixture — the wide, deliberately messy commerce schema the
-- evaluation harness runs against. It replaces the earlier ~14-table toy.
--
-- WHY IT IS THIS SHAPE
-- The retrieve node sends the entire schema snapshot to the generator whenever
-- it fits a ~24k-character budget (roughly `sum(60 + 40*ncols)` over tables).
-- At ~14 narrow tables that estimate is ~3.7k, so the whole snapshot is always
-- sent and retrieval is never exercised — an eval on it would score generation
-- only and predict nothing about a real customer schema. This fixture is 42
-- tables wide enough (audit columns, address blocks, legacy cruft — the width a
-- real ERP carries) that the estimate clears 24k, so retrieval must actually
-- select a subset and the bridge-table questions below can genuinely fail.
--
-- DELIBERATE MESSINESS (each item exists so a specific eval question bites):
--   * Legacy unhelpful columns:  products.flg_2, products.cust_ref,
--     customers.cust_ref, orders.cust_ref, and the whole `product` table.
--   * Two tables with near-duplicate names:  `products` (the real one) and
--     `product` (a deprecated singular leftover with a few stale rows). Fuzzy
--     top-k retrieval on "product" can grab the wrong one.
--   * Junction / bridge tables (7):  product_suppliers, price_list_items,
--     product_tags, employee_teams, order_promotions, shipment_items,
--     inventory.
--   * A denormalized reporting table:  sales_daily_rollup pre-aggregates the
--     orders -> order_items -> payments path, but only for the last ~90 days,
--     so a full-history question answered from it is WRONG — the gold path is
--     the normalized one.
--   * Nullable foreign keys:  orders.employee_id / orders.coupon_id,
--     customers.referred_by_id, order_items.variant_id, support_tickets.order_id
--     and more — an INNER join silently drops rows.
--   * A soft-delete column that must be filtered for correctness:
--     customers.is_deleted (~8% of customers). "How many active customers…"
--     is wrong unless it filters is_deleted = false.
--
-- FOUR-PLUS BRIDGE-TABLE PATHS (a join to a table the question never names):
--   1. Revenue by supplier  -> suppliers . product_suppliers . products .
--      order_items . orders            (bridges: product_suppliers, order_items)
--   2. Revenue by brand in a region -> brands . products . order_items .
--      orders . customers . regions    (bridge: order_items)
--   3. Revenue by promotion -> promotions . order_promotions . orders .
--      order_items                     (bridge: order_promotions)
--   4. Sales by product tag -> tags . product_tags . products . order_items
--                                       (bridge: product_tags)
--   5. Revenue by sales team -> teams . employee_teams . employees . orders
--                                       (bridge: employee_teams)
--
-- The read-only role (analytics_ro) is defined at the end so its blanket SELECT
-- grant covers every table. Milestone item 3 asserts both directions:
-- readonly_confirmed is true for this role and false for a superuser.

SET client_min_messages = warning;
SELECT setseed(0.4242);   -- reproducible "random" values -> stable aggregates

-- ══════════════════════════════════════════════════════════════════════════
--  DIMENSION / REFERENCE TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE countries (
  id            bigserial PRIMARY KEY,
  iso_code      text NOT NULL UNIQUE,
  name          text NOT NULL,
  continent     text NOT NULL,
  currency_code text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE regions (
  id         bigserial PRIMARY KEY,
  country_id bigint REFERENCES countries(id),   -- nullable: legacy regions
  name       text NOT NULL,
  code       text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE categories (
  id          bigserial PRIMARY KEY,
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE subcategories (
  id          bigserial PRIMARY KEY,
  category_id bigint NOT NULL REFERENCES categories(id),
  name        text NOT NULL,
  description text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE brands (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  country_id   bigint REFERENCES countries(id),   -- nullable FK
  website      text,
  is_active    boolean NOT NULL DEFAULT true,
  source_system text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
  id             bigserial PRIMARY KEY,
  name           text NOT NULL,
  category       text NOT NULL,                     -- denormalized legacy label
  category_id    bigint REFERENCES categories(id),
  subcategory_id bigint REFERENCES subcategories(id),  -- nullable FK
  brand_id       bigint REFERENCES brands(id),          -- nullable FK
  sku            text UNIQUE,
  price          numeric(10,2) NOT NULL,
  cost           numeric(10,2),
  weight_kg      numeric(8,3),
  active         boolean NOT NULL DEFAULT true,
  discontinued   boolean NOT NULL DEFAULT false,
  flg_2          boolean NOT NULL DEFAULT false,    -- LEGACY: meaning lost
  cust_ref       text,                              -- LEGACY: external ref
  launched_at    date,
  source_system  text,
  external_ref   text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- NEAR-DUPLICATE NAME, on purpose. `product` (singular) is a deprecated table
-- kept alive by one crusty report. A retriever matching "product" may pick it.
CREATE TABLE product (
  id         bigserial PRIMARY KEY,
  name       text,
  cust_ref   text,          -- LEGACY
  flg_2      boolean,       -- LEGACY
  old_price  numeric(10,2),
  note       text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE product_variants (
  id          bigserial PRIMARY KEY,
  product_id  bigint NOT NULL REFERENCES products(id),
  variant_sku text UNIQUE,
  color       text,
  size        text,
  extra_price numeric(10,2) NOT NULL DEFAULT 0,
  barcode     text,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE suppliers (
  id             bigserial PRIMARY KEY,
  name           text NOT NULL,
  region_id      bigint REFERENCES regions(id),   -- nullable FK
  contact_email  text,
  phone          text,
  lead_time_days int NOT NULL DEFAULT 7,
  rating         numeric(3,2),
  active         boolean NOT NULL DEFAULT true,
  address_line1  text,
  city           text,
  postal_code    text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE warehouses (
  id            bigserial PRIMARY KEY,
  name          text NOT NULL,
  region_id     bigint REFERENCES regions(id),
  capacity      int NOT NULL,
  address_line1 text,
  city          text,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE teams (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL,
  region_id  bigint REFERENCES regions(id),   -- nullable FK
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE employees (
  id            bigserial PRIMARY KEY,
  name          text NOT NULL,
  title         text NOT NULL,
  region_id     bigint REFERENCES regions(id),
  team_id       bigint REFERENCES teams(id),          -- nullable FK
  manager_id    bigint REFERENCES employees(id),      -- self-referencing
  email         text,
  hired_at      date NOT NULL,
  terminated_at date,                                 -- nullable
  salary        numeric(10,2) NOT NULL,
  commission_pct numeric(5,2) NOT NULL DEFAULT 0,
  active        boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE loyalty_tiers (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  min_points   int NOT NULL DEFAULT 0,
  discount_pct numeric(5,2) NOT NULL DEFAULT 0,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE customers (
  id              bigserial PRIMARY KEY,
  name            text NOT NULL,
  email           text,
  phone           text,
  region_id       bigint REFERENCES regions(id),
  loyalty_tier_id bigint REFERENCES loyalty_tiers(id),   -- nullable FK
  referred_by_id  bigint REFERENCES customers(id),       -- nullable self-ref
  segment         text NOT NULL DEFAULT 'SMB',
  credit_limit    numeric(12,2),
  cust_ref        text,                                  -- LEGACY external ref
  signed_up_at    timestamptz NOT NULL DEFAULT now(),
  last_order_at   timestamptz,
  is_deleted      boolean NOT NULL DEFAULT false,        -- SOFT DELETE
  deleted_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE customer_addresses (
  id          bigserial PRIMARY KEY,
  customer_id bigint NOT NULL REFERENCES customers(id),
  kind        text NOT NULL DEFAULT 'shipping',   -- shipping | billing
  line1       text NOT NULL,
  line2       text,
  city        text,
  region_id   bigint REFERENCES regions(id),      -- nullable FK
  postal_code text,
  country_id  bigint REFERENCES countries(id),    -- nullable FK
  is_primary  boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE currencies (
  id         bigserial PRIMARY KEY,
  code       text NOT NULL UNIQUE,
  name       text NOT NULL,
  symbol     text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE price_lists (
  id          bigserial PRIMARY KEY,
  name        text NOT NULL,
  currency_id bigint REFERENCES currencies(id),
  valid_from  date NOT NULL,
  valid_to    date,                               -- nullable: open-ended
  is_active   boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tax_rates (
  id         bigserial PRIMARY KEY,
  region_id  bigint REFERENCES regions(id),
  name       text NOT NULL,
  rate_pct   numeric(5,2) NOT NULL,
  valid_from date NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE promotions (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  description  text,
  promo_type   text NOT NULL DEFAULT 'percent',
  discount_pct numeric(5,2) NOT NULL DEFAULT 0,
  budget       numeric(12,2),
  starts_on    date NOT NULL,
  ends_on      date,
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE coupons (
  id           bigserial PRIMARY KEY,
  promotion_id bigint REFERENCES promotions(id),
  code         text NOT NULL UNIQUE,
  max_uses     int NOT NULL DEFAULT 1,
  times_used   int NOT NULL DEFAULT 0,
  expires_on   date,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tags (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL UNIQUE,
  kind       text NOT NULL DEFAULT 'attribute',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE carriers (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  tracking_url text,
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payment_methods (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL,
  kind       text NOT NULL DEFAULT 'card',
  is_active  boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ══════════════════════════════════════════════════════════════════════════
--  JUNCTION / BRIDGE TABLES
-- ══════════════════════════════════════════════════════════════════════════

-- Which suppliers can source which products (bridge for supplier revenue).
CREATE TABLE product_suppliers (
  product_id     bigint NOT NULL REFERENCES products(id),
  supplier_id    bigint NOT NULL REFERENCES suppliers(id),
  cost           numeric(10,2) NOT NULL,
  is_preferred   boolean NOT NULL DEFAULT false,
  lead_time_days int NOT NULL DEFAULT 7,
  PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE price_list_items (
  price_list_id bigint NOT NULL REFERENCES price_lists(id),
  product_id    bigint NOT NULL REFERENCES products(id),
  unit_price    numeric(10,2) NOT NULL,
  PRIMARY KEY (price_list_id, product_id)
);

-- Tag membership (bridge for "sales by tag").
CREATE TABLE product_tags (
  product_id bigint NOT NULL REFERENCES products(id),
  tag_id     bigint NOT NULL REFERENCES tags(id),
  PRIMARY KEY (product_id, tag_id)
);

-- Employee <-> team assignment history (bridge for "revenue by sales team").
CREATE TABLE employee_teams (
  employee_id  bigint NOT NULL REFERENCES employees(id),
  team_id      bigint NOT NULL REFERENCES teams(id),
  role_in_team text NOT NULL DEFAULT 'member',
  assigned_on  date NOT NULL DEFAULT current_date,
  PRIMARY KEY (employee_id, team_id)
);

-- Inventory: product x warehouse (also a bridge for stock questions).
CREATE TABLE inventory (
  id           bigserial PRIMARY KEY,
  product_id   bigint NOT NULL REFERENCES products(id),
  warehouse_id bigint NOT NULL REFERENCES warehouses(id),
  quantity     int NOT NULL,
  reorder_level int NOT NULL DEFAULT 10,
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (product_id, warehouse_id)
);

-- ══════════════════════════════════════════════════════════════════════════
--  FACT / TRANSACTION TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE orders (
  id             bigserial PRIMARY KEY,
  customer_id    bigint REFERENCES customers(id),
  employee_id    bigint REFERENCES employees(id),   -- nullable: web has no rep
  coupon_id      bigint REFERENCES coupons(id),      -- nullable FK
  currency_id    bigint REFERENCES currencies(id),
  order_date     date NOT NULL,
  status         text NOT NULL,
  channel        text NOT NULL DEFAULT 'web',
  subtotal       numeric(12,2) NOT NULL,
  discount_total numeric(12,2) NOT NULL DEFAULT 0,
  tax_total      numeric(12,2) NOT NULL DEFAULT 0,
  shipping_fee   numeric(10,2) NOT NULL DEFAULT 0,
  total_amount   numeric(12,2) NOT NULL,
  cust_ref       text,                              -- LEGACY external ref
  notes          text,
  placed_at      timestamptz NOT NULL DEFAULT now(),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Order lines. The single most-used BRIDGE: it is what connects products (and
-- everything reachable only through products) to orders/customers/revenue.
CREATE TABLE order_items (
  id          bigserial PRIMARY KEY,
  order_id    bigint REFERENCES orders(id),
  product_id  bigint REFERENCES products(id),
  variant_id  bigint REFERENCES product_variants(id),  -- nullable FK
  quantity    int NOT NULL,
  unit_price  numeric(10,2) NOT NULL,
  discount    numeric(5,2) NOT NULL DEFAULT 0,
  tax_rate_id bigint REFERENCES tax_rates(id),          -- nullable FK
  line_total  numeric(12,2) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Which promotions applied to which orders (bridge for promotion revenue).
CREATE TABLE order_promotions (
  order_id        bigint NOT NULL REFERENCES orders(id),
  promotion_id    bigint NOT NULL REFERENCES promotions(id),
  discount_amount numeric(12,2) NOT NULL DEFAULT 0,
  PRIMARY KEY (order_id, promotion_id)
);

CREATE TABLE payments (
  id                bigserial PRIMARY KEY,
  order_id          bigint REFERENCES orders(id),
  payment_method_id bigint REFERENCES payment_methods(id),
  amount            numeric(12,2) NOT NULL,
  currency_id       bigint REFERENCES currencies(id),
  status            text NOT NULL DEFAULT 'captured',
  paid_at           timestamptz NOT NULL,
  txn_ref           text,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE shipments (
  id              bigserial PRIMARY KEY,
  order_id        bigint REFERENCES orders(id),
  warehouse_id    bigint REFERENCES warehouses(id),
  carrier_id      bigint REFERENCES carriers(id),
  tracking_number text,
  status          text NOT NULL DEFAULT 'in_transit',
  shipped_at      timestamptz NOT NULL,
  delivered_at    timestamptz,                     -- nullable
  weight_kg       numeric(8,3),
  cost            numeric(10,2),
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Which order lines are in which shipment (junction).
CREATE TABLE shipment_items (
  shipment_id   bigint NOT NULL REFERENCES shipments(id),
  order_item_id bigint NOT NULL REFERENCES order_items(id),
  quantity      int NOT NULL,
  PRIMARY KEY (shipment_id, order_item_id)
);

CREATE TABLE returns (
  id            bigserial PRIMARY KEY,
  order_item_id bigint REFERENCES order_items(id),
  reason        text NOT NULL,
  quantity      int NOT NULL,
  refund_amount numeric(10,2) NOT NULL,
  status        text NOT NULL DEFAULT 'approved',
  returned_at   timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refunds (
  id           bigserial PRIMARY KEY,
  return_id    bigint REFERENCES returns(id),
  payment_id   bigint REFERENCES payments(id),   -- nullable FK
  amount       numeric(10,2) NOT NULL,
  method       text NOT NULL DEFAULT 'card',
  processed_at timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
  id          bigserial PRIMARY KEY,
  product_id  bigint REFERENCES products(id),
  customer_id bigint REFERENCES customers(id),   -- nullable FK (anonymous)
  order_id    bigint REFERENCES orders(id),       -- nullable FK
  rating      int NOT NULL,
  title       text,
  body        text,
  is_verified boolean NOT NULL DEFAULT false,
  is_hidden   boolean NOT NULL DEFAULT false,     -- soft hide
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE support_tickets (
  id          bigserial PRIMARY KEY,
  customer_id bigint REFERENCES customers(id),
  order_id    bigint REFERENCES orders(id),       -- nullable FK
  employee_id bigint REFERENCES employees(id),    -- nullable FK (assignee)
  subject     text NOT NULL,
  status      text NOT NULL DEFAULT 'open',
  priority    text NOT NULL DEFAULT 'normal',
  opened_at   timestamptz NOT NULL,
  closed_at   timestamptz,                         -- nullable
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wishlists (
  id          bigserial PRIMARY KEY,
  customer_id bigint NOT NULL REFERENCES customers(id),
  product_id  bigint NOT NULL REFERENCES products(id),
  added_at    timestamptz NOT NULL DEFAULT now(),
  note        text
);

CREATE TABLE order_status_history (
  id         bigserial PRIMARY KEY,
  order_id   bigint NOT NULL REFERENCES orders(id),
  old_status text,
  new_status text NOT NULL,
  changed_by bigint,           -- LEGACY: an untyped user id, no FK
  changed_at timestamptz NOT NULL DEFAULT now(),
  note       text
);

CREATE TABLE product_price_history (
  id         bigserial PRIMARY KEY,
  product_id bigint NOT NULL REFERENCES products(id),
  old_price  numeric(10,2),
  new_price  numeric(10,2) NOT NULL,
  changed_at timestamptz NOT NULL DEFAULT now(),
  reason     text
);

-- DENORMALIZED REPORTING TABLE. Pre-aggregates revenue per region per day, but
-- ONLY for the last ~90 days (see the WHERE below). It partially duplicates the
-- orders -> order_items -> payments path; a full-history question answered from
-- here is wrong. The gold query for such a question must use the normalized
-- tables, not this rollup.
CREATE TABLE sales_daily_rollup (
  id            bigserial PRIMARY KEY,
  day           date NOT NULL,
  region_id     bigint REFERENCES regions(id),
  orders_count  int NOT NULL DEFAULT 0,
  gross_revenue numeric(14,2) NOT NULL DEFAULT 0,
  units_sold    int NOT NULL DEFAULT 0,
  refunds_total numeric(14,2) NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ══════════════════════════════════════════════════════════════════════════
--  DATA
-- ══════════════════════════════════════════════════════════════════════════

INSERT INTO countries (iso_code, name, continent, currency_code)
SELECT 'C' || lpad(g::text, 2, '0'),
       'Country ' || g,
       (ARRAY['North America','Europe','Asia','South America','Africa','Oceania'])[((g % 6) + 1)],
       (ARRAY['USD','EUR','GBP','JPY','BRL','AUD'])[((g % 6) + 1)]
FROM generate_series(1, 25) g;

INSERT INTO regions (country_id, name, code) VALUES
  (1, 'North America', 'NA'),
  (2, 'Europe',        'EU'),
  (3, 'Asia Pacific',  'APAC'),
  (4, 'Latin America', 'LATAM'),
  (5, 'Middle East',   'ME'),
  (6, 'Africa',        'AF'),
  (7, 'Nordics',       'NORD'),
  (NULL, 'Unassigned', NULL);   -- nullable country_id in the wild

INSERT INTO categories (name, description) VALUES
  ('Accessories', 'Hubs, stands, cables and small add-ons'),
  ('Peripherals', 'Keyboards, mice, webcams'),
  ('Displays',    'Monitors and screens'),
  ('Audio',       'Headsets and speakers'),
  ('Networking',  'Routers, switches, adapters'),
  ('Storage',     'Drives and memory');

INSERT INTO subcategories (category_id, name, description)
SELECT ((g % 6) + 1),
       'Subcategory ' || g,
       'Auto-generated subcategory ' || g
FROM generate_series(1, 20) g;

INSERT INTO brands (name, country_id, website, source_system)
SELECT 'Brand ' || g,
       CASE WHEN g % 5 = 0 THEN NULL ELSE ((g % 25) + 1) END,   -- nullable FK
       'https://brand' || g || '.example',
       (ARRAY['erp','pim','legacy'])[((g % 3) + 1)]
FROM generate_series(1, 18) g;

-- 120 products across the six categories/brands, with legacy junk populated.
INSERT INTO products
  (name, category, category_id, subcategory_id, brand_id, sku, price, cost,
   weight_kg, active, discontinued, flg_2, cust_ref, launched_at,
   source_system, external_ref)
SELECT
  (ARRAY['Accessories','Peripherals','Displays','Audio','Networking','Storage'])[((g % 6) + 1)]
    || ' Model ' || g,
  (ARRAY['Accessories','Peripherals','Displays','Audio','Networking','Storage'])[((g % 6) + 1)],
  (g % 6) + 1,
  CASE WHEN g % 7 = 0 THEN NULL ELSE ((g % 20) + 1) END,   -- nullable FK
  CASE WHEN g % 9 = 0 THEN NULL ELSE ((g % 18) + 1) END,   -- nullable FK
  'SKU-' || lpad(g::text, 5, '0'),
  round((random() * 380 + 20)::numeric, 2),
  round((random() * 180 + 10)::numeric, 2),
  round((random() * 4 + 0.1)::numeric, 3),
  (g % 17 <> 0),                     -- a few inactive
  (g % 23 = 0),                      -- a few discontinued
  (g % 2 = 0),                       -- flg_2: meaningless legacy flag
  CASE WHEN g % 4 = 0 THEN 'LEG-' || lpad(g::text, 6, '0') ELSE NULL END,
  current_date - ((g * 13) % 1500),
  (ARRAY['erp','pim','legacy'])[((g % 3) + 1)],
  'EXT' || lpad(g::text, 6, '0')
FROM generate_series(1, 120) g;

-- The deprecated singular `product` table: a few stale rows only.
INSERT INTO product (name, cust_ref, flg_2, old_price, note) VALUES
  ('Legacy Widget A', 'OLD-000001', true,  19.99, 'do not use - see products'),
  ('Legacy Widget B', 'OLD-000002', false, 29.99, 'migrated 2019'),
  ('Legacy Widget C', NULL,          true,  9.99,  NULL),
  ('Legacy Widget D', 'OLD-000004', false, 49.99, 'kept for the quarterly PDF'),
  ('Legacy Widget E', 'OLD-000005', NULL,  14.99, NULL),
  ('Legacy Widget F', 'OLD-000006', true,  99.99, 'ghost row');

-- ~3 variants per product.
INSERT INTO product_variants (product_id, variant_sku, color, size, extra_price, barcode)
SELECT ((g - 1) / 3) + 1,
       'VAR-' || lpad(g::text, 6, '0'),
       (ARRAY['black','white','silver','blue','red'])[((g % 5) + 1)],
       (ARRAY['S','M','L','XL','one-size'])[((g % 5) + 1)],
       round(((g % 4) * 5)::numeric, 2),
       'BC' || lpad(g::text, 10, '0')
FROM generate_series(1, 360) g;

INSERT INTO suppliers (name, region_id, contact_email, phone, lead_time_days, rating, address_line1, city, postal_code)
SELECT 'Supplier ' || g,
       CASE WHEN g % 8 = 0 THEN NULL ELSE ((g % 8) + 1) END,   -- nullable FK
       'sales@supplier' || g || '.example',
       '+1-555-' || lpad(g::text, 4, '0'),
       (g % 21) + 3,
       round((random() * 2 + 3)::numeric, 2),
       g || ' Industrial Way',
       'City ' || ((g % 8) + 1),
       lpad(((g * 137) % 99999)::text, 5, '0')
FROM generate_series(1, 30) g;

INSERT INTO warehouses (name, region_id, capacity, address_line1, city)
SELECT r.name || ' DC',
       r.id,
       (5000 + (r.id * 1500)),
       r.id || ' Distribution Blvd',
       r.name
FROM regions r;

INSERT INTO teams (name, region_id)
SELECT (ARRAY['Field Sales','Inside Sales','Enterprise','SMB','Partnerships','Renewals','Named Accounts','Growth'])[g],
       CASE WHEN g % 4 = 0 THEN NULL ELSE ((g % 8) + 1) END
FROM generate_series(1, 8) g;

-- 60 employees; first 6 are managers (no manager of their own).
INSERT INTO employees
  (name, title, region_id, team_id, manager_id, email, hired_at, terminated_at,
   salary, commission_pct, active)
SELECT
  'Employee ' || g,
  (ARRAY['Sales Rep','Sales Rep','Sales Rep','Account Manager','Regional Manager','Director'])[((g % 6) + 1)],
  (g % 8) + 1,
  CASE WHEN g % 6 = 0 THEN NULL ELSE ((g % 8) + 1) END,   -- nullable FK
  CASE WHEN g <= 6 THEN NULL ELSE ((g % 6) + 1) END,
  'employee' || g || '@datamind.example',
  current_date - ((g * 47) % 2500),
  CASE WHEN g % 19 = 0 THEN current_date - ((g * 7) % 300) ELSE NULL END,
  round((random() * 60000 + 45000)::numeric, 2),
  round(((g % 5) * 1.5)::numeric, 2),
  (g % 19 <> 0)
FROM generate_series(1, 60) g;

INSERT INTO loyalty_tiers (name, min_points, discount_pct) VALUES
  ('Bronze', 0,     0),
  ('Silver', 1000,  2.5),
  ('Gold',   5000,  5),
  ('Platinum', 20000, 10);

-- 1500 customers; ~8% soft-deleted; nullable loyalty tier and referrer.
INSERT INTO customers
  (name, email, phone, region_id, loyalty_tier_id, referred_by_id, segment,
   credit_limit, cust_ref, signed_up_at, last_order_at, is_deleted, deleted_at)
SELECT 'Customer ' || g,
       'customer' || g || '@example.com',
       '+1-555-' || lpad((g % 10000)::text, 4, '0'),
       (g % 8) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE ((g % 4) + 1) END,       -- nullable
       CASE WHEN g > 50 AND g % 7 = 0 THEN ((g % 50) + 1) ELSE NULL END,  -- nullable self-ref
       (ARRAY['SMB','SMB','Mid-Market','Enterprise'])[((g % 4) + 1)],
       round((random() * 40000 + 1000)::numeric, 2),
       CASE WHEN g % 5 = 0 THEN 'CRM-' || lpad(g::text, 7, '0') ELSE NULL END,
       now() - ((g % 1000) * interval '1 day'),
       CASE WHEN g % 6 = 0 THEN NULL ELSE now() - ((g % 200) * interval '1 day') END,
       (g % 13 = 0),                                               -- ~8% deleted
       CASE WHEN g % 13 = 0 THEN now() - ((g % 90) * interval '1 day') ELSE NULL END
FROM generate_series(1, 1500) g;

-- Primary address for every customer, plus a billing address for even ones.
INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'shipping', c.id || ' Main St', 'City ' || ((c.id % 8) + 1),
       c.region_id, lpad(((c.id * 91) % 99999)::text, 5, '0'),
       ((c.id % 25) + 1), true
FROM customers c;
INSERT INTO customer_addresses (customer_id, kind, line1, city, region_id, postal_code, country_id, is_primary)
SELECT c.id, 'billing', c.id || ' Finance Ave', 'City ' || ((c.id % 8) + 1),
       c.region_id, lpad(((c.id * 57) % 99999)::text, 5, '0'),
       ((c.id % 25) + 1), false
FROM customers c WHERE c.id % 2 = 0;

INSERT INTO currencies (code, name, symbol) VALUES
  ('USD','US Dollar','$'), ('EUR','Euro','€'), ('GBP','Pound Sterling','£'),
  ('JPY','Japanese Yen','¥'), ('BRL','Brazilian Real','R$'),
  ('AUD','Australian Dollar','A$'), ('CAD','Canadian Dollar','C$'),
  ('INR','Indian Rupee','₹');

INSERT INTO price_lists (name, currency_id, valid_from, valid_to, is_active) VALUES
  ('Standard USD', 1, current_date - 400, NULL, true),
  ('EU Retail',    2, current_date - 400, NULL, true),
  ('UK Retail',    3, current_date - 400, NULL, true),
  ('Legacy 2023',  1, current_date - 800, current_date - 365, false),
  ('Enterprise',   1, current_date - 200, NULL, true);

INSERT INTO tax_rates (region_id, name, rate_pct, valid_from)
SELECT ((g % 8) + 1),
       'Standard ' || g,
       round(((g % 5) * 2.5 + 5)::numeric, 2),
       current_date - 500
FROM generate_series(1, 12) g;

INSERT INTO promotions (name, description, promo_type, discount_pct, budget, starts_on, ends_on, is_active)
SELECT 'Promo ' || g,
       'Auto promotion ' || g,
       (ARRAY['percent','percent','bogo','fixed'])[((g % 4) + 1)],
       round(((g % 6) * 5 + 5)::numeric, 2),
       round((random() * 50000 + 5000)::numeric, 2),
       current_date - ((g * 30) % 700),
       current_date - ((g * 30) % 700) + 45,
       (g % 4 <> 0)
FROM generate_series(1, 20) g;

INSERT INTO coupons (promotion_id, code, max_uses, times_used, expires_on)
SELECT ((g % 20) + 1),
       'CPN-' || lpad(g::text, 6, '0'),
       (g % 5) * 100 + 1,
       (g % 37),
       current_date + ((g % 120))
FROM generate_series(1, 60) g;

INSERT INTO tags (name, kind)
SELECT (ARRAY['bestseller','clearance','new','eco','premium','bulk','fragile',
              'refurb','bundle','limited','gaming','office','travel','wireless',
              'usb-c','4k','rgb','compact','heavy-duty','warranty-3y','warranty-1y',
              'imported','local','seasonal','staff-pick'])[g],
       (ARRAY['merch','merch','lifecycle','attribute'])[((g % 4) + 1)]
FROM generate_series(1, 25) g;

INSERT INTO carriers (name, tracking_url) VALUES
  ('UPS',   'https://ups.example/track?n='),
  ('FedEx', 'https://fedex.example/track?n='),
  ('DHL',   'https://dhl.example/track?n='),
  ('USPS',  'https://usps.example/track?n='),
  ('Aramex','https://aramex.example/track?n='),
  ('Local Courier', NULL);

INSERT INTO payment_methods (name, kind) VALUES
  ('Visa','card'), ('Mastercard','card'), ('Amex','card'),
  ('PayPal','wallet'), ('Wire Transfer','bank'), ('Store Credit','credit');

-- ── junction data ──────────────────────────────────────────────────────────

-- Every product has a primary supplier; even products get a second source.
INSERT INTO product_suppliers (product_id, supplier_id, cost, is_preferred, lead_time_days)
SELECT g, ((g % 30) + 1), round((random() * 200 + 10)::numeric, 2), true, (g % 15) + 3
FROM generate_series(1, 120) g;
INSERT INTO product_suppliers (product_id, supplier_id, cost, is_preferred, lead_time_days)
SELECT g, (((g + 11) % 30) + 1), round((random() * 200 + 10)::numeric, 2), false, (g % 20) + 5
FROM generate_series(1, 120) g
WHERE g % 2 = 0 AND ((g % 30) + 1) <> (((g + 11) % 30) + 1);

-- Active price lists carry every product; the legacy list carries a subset.
INSERT INTO price_list_items (price_list_id, product_id, unit_price)
SELECT pl.id, p.id, round((p.price * (1 + (pl.id::numeric / 20)))::numeric, 2)
FROM price_lists pl CROSS JOIN products p
WHERE pl.id IN (1,2,3,5) OR (pl.id = 4 AND p.id % 3 = 0);

-- ~4 tags per product.
INSERT INTO product_tags (product_id, tag_id)
SELECT p, t
FROM generate_series(1, 120) p
CROSS JOIN LATERAL (
  SELECT DISTINCT ((p * 7 + s * 13) % 25) + 1 AS t
  FROM generate_series(1, 4) s
) tg;

-- Each employee belongs to 1-2 teams.
INSERT INTO employee_teams (employee_id, team_id, role_in_team)
SELECT g, ((g % 8) + 1), CASE WHEN g % 6 = 0 THEN 'lead' ELSE 'member' END
FROM generate_series(1, 60) g;
INSERT INTO employee_teams (employee_id, team_id, role_in_team)
SELECT g, (((g + 3) % 8) + 1), 'member'
FROM generate_series(1, 60) g
WHERE g % 3 = 0 AND ((g % 8) + 1) <> (((g + 3) % 8) + 1);

-- Inventory: every product in every warehouse.
INSERT INTO inventory (product_id, warehouse_id, quantity, reorder_level)
SELECT p.id, w.id, (random() * 500)::int, (10 + (p.id % 40))
FROM products p CROSS JOIN warehouses w;

-- ── fact data ───────────────────────────────────────────────────────────────

-- 6000 orders over ~2 years. employee_id null on web orders; coupon on ~1/6.
INSERT INTO orders
  (customer_id, employee_id, coupon_id, currency_id, order_date, status, channel,
   subtotal, discount_total, tax_total, shipping_fee, total_amount, cust_ref, placed_at)
SELECT
  (g % 1500) + 1,
  CASE WHEN (g % 5) IN (0,1,2) THEN NULL ELSE ((g % 60) + 1) END,   -- nullable
  CASE WHEN g % 6 = 0 THEN ((g % 60) + 1) ELSE NULL END,            -- nullable
  (ARRAY[1,1,1,2,3])[((g % 5) + 1)],
  current_date - ((g * 3) % 730),
  (ARRAY['completed','completed','completed','completed','shipped',
         'pending','cancelled','returned'])[((g % 8) + 1)],
  (ARRAY['web','web','web','phone','partner'])[((g % 5) + 1)],
  s.subtotal,
  round((s.subtotal * 0.05)::numeric, 2),
  round((s.subtotal * 0.08)::numeric, 2),
  (ARRAY[0,0,5,9.99,19.99])[((g % 5) + 1)],
  round((s.subtotal * 1.03 + (ARRAY[0,0,5,9.99,19.99])[((g % 5) + 1)])::numeric, 2),
  CASE WHEN g % 9 = 0 THEN 'ORD-' || lpad(g::text, 8, '0') ELSE NULL END,
  (current_date - ((g * 3) % 730))::timestamptz + interval '9 hours'
FROM generate_series(1, 6000) g
CROSS JOIN LATERAL (SELECT round((random() * 900 + 60)::numeric, 2) AS subtotal) s;

-- ~3 order lines per order (18000 lines). variant/tax FKs sometimes null.
INSERT INTO order_items
  (order_id, product_id, variant_id, quantity, unit_price, discount, tax_rate_id, line_total)
SELECT
  (g % 6000) + 1,
  (g % 120) + 1,
  CASE WHEN g % 4 = 0 THEN NULL ELSE ((g % 360) + 1) END,   -- nullable FK
  (g % 5) + 1,
  li.unit_price,
  (ARRAY[0,0,0,5,10,15])[((g % 6) + 1)],
  CASE WHEN g % 7 = 0 THEN NULL ELSE ((g % 12) + 1) END,    -- nullable FK
  round((li.unit_price * ((g % 5) + 1))::numeric, 2)
FROM generate_series(1, 18000) g
CROSS JOIN LATERAL (SELECT round((random() * 250 + 25)::numeric, 2) AS unit_price) li;

-- Promotions applied to ~30% of orders (bridge for promotion revenue).
INSERT INTO order_promotions (order_id, promotion_id, discount_amount)
SELECT o.id, ((o.id % 20) + 1), round((o.discount_total)::numeric, 2)
FROM orders o
WHERE o.id % 3 = 0;

-- Payments for fulfilled orders.
INSERT INTO payments (order_id, payment_method_id, amount, currency_id, status, paid_at, txn_ref)
SELECT o.id,
       ((o.id % 6) + 1),
       o.total_amount,
       o.currency_id,
       'captured',
       o.placed_at + ((o.id % 3) * interval '1 day'),
       'TXN' || lpad(o.id::text, 10, '0')
FROM orders o
WHERE o.status IN ('completed','shipped','returned');

-- Shipments for fulfilled orders.
INSERT INTO shipments (order_id, warehouse_id, carrier_id, tracking_number, status, shipped_at, delivered_at, weight_kg, cost)
SELECT o.id,
       ((o.id % 8) + 1),
       ((o.id % 6) + 1),
       'TRK' || lpad(o.id::text, 8, '0'),
       CASE WHEN o.status = 'completed' THEN 'delivered' ELSE 'in_transit' END,
       o.placed_at + interval '1 day',
       CASE WHEN o.status = 'completed'
            THEN o.placed_at + ((2 + (o.id % 6)) * interval '1 day') ELSE NULL END,
       round((random() * 10 + 0.5)::numeric, 3),
       round((random() * 40 + 5)::numeric, 2)
FROM orders o
WHERE o.status IN ('completed','shipped','returned');

-- One shipment_items row per order line of a shipped order (junction).
INSERT INTO shipment_items (shipment_id, order_item_id, quantity)
SELECT s.id, oi.id, oi.quantity
FROM shipments s
JOIN order_items oi ON oi.order_id = s.order_id;

-- Returns on ~3% of order lines.
INSERT INTO returns (order_item_id, reason, quantity, refund_amount, status, returned_at)
SELECT oi.id,
       (ARRAY['defective','wrong item','no longer needed','damaged'])[((oi.id % 4) + 1)],
       1,
       oi.unit_price,
       (ARRAY['approved','approved','approved','rejected'])[((oi.id % 4) + 1)],
       now() - ((oi.id % 200) * interval '1 day')
FROM order_items oi
WHERE oi.id % 33 = 0;

-- Refunds for approved returns.
INSERT INTO refunds (return_id, payment_id, amount, method, processed_at)
SELECT r.id,
       (SELECT p.id FROM payments p
        JOIN order_items oi ON oi.id = r.order_item_id
        WHERE p.order_id = oi.order_id LIMIT 1),
       r.refund_amount,
       'card',
       r.returned_at + interval '2 days'
FROM returns r
WHERE r.status = 'approved';

-- 3000 product reviews; customer/order FKs sometimes null (anonymous).
INSERT INTO reviews (product_id, customer_id, order_id, rating, title, body, is_verified, is_hidden)
SELECT (g % 120) + 1,
       CASE WHEN g % 5 = 0 THEN NULL ELSE ((g % 1500) + 1) END,   -- nullable
       CASE WHEN g % 4 = 0 THEN NULL ELSE ((g % 6000) + 1) END,   -- nullable
       (g % 5) + 1,
       'Review ' || g,
       'Auto-generated review body ' || g,
       (g % 3 = 0),
       (g % 29 = 0)                                                -- a few hidden
FROM generate_series(1, 3000) g;

-- 1500 support tickets.
INSERT INTO support_tickets (customer_id, order_id, employee_id, subject, status, priority, opened_at, closed_at)
SELECT (g % 1500) + 1,
       CASE WHEN g % 3 = 0 THEN NULL ELSE ((g % 6000) + 1) END,   -- nullable
       CASE WHEN g % 4 = 0 THEN NULL ELSE ((g % 60) + 1) END,     -- nullable
       'Ticket ' || g,
       (ARRAY['open','open','pending','resolved','closed'])[((g % 5) + 1)],
       (ARRAY['low','normal','normal','high','urgent'])[((g % 5) + 1)],
       now() - ((g % 400) * interval '1 day'),
       CASE WHEN g % 5 IN (3,4) THEN now() - ((g % 400) * interval '1 day') + interval '2 days' ELSE NULL END
FROM generate_series(1, 1500) g;

-- 2000 wishlist entries.
INSERT INTO wishlists (customer_id, product_id, added_at, note)
SELECT (g % 1500) + 1, (g % 120) + 1,
       now() - ((g % 300) * interval '1 day'),
       CASE WHEN g % 8 = 0 THEN 'gift idea' ELSE NULL END
FROM generate_series(1, 2000) g;

-- Order status history: one initial + one transition per order.
INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, NULL, 'pending', ((o.id % 60) + 1), o.placed_at, 'created'
FROM orders o;
INSERT INTO order_status_history (order_id, old_status, new_status, changed_by, changed_at, note)
SELECT o.id, 'pending', o.status, ((o.id % 60) + 1), o.placed_at + interval '1 day', 'auto'
FROM orders o WHERE o.status <> 'pending';

-- Product price history: two revisions for a third of products.
INSERT INTO product_price_history (product_id, old_price, new_price, changed_at, reason)
SELECT p.id, round((p.price * 0.9)::numeric, 2), p.price,
       now() - interval '180 days', 'annual review'
FROM products p WHERE p.id % 3 = 0;

-- DENORMALIZED rollup: last ~90 days only (deliberately partial). Computed from
-- the normalized tables so its numbers agree there, but it does NOT cover older
-- history — a full-period question must not be answered from this table.
INSERT INTO sales_daily_rollup (day, region_id, orders_count, gross_revenue, units_sold, refunds_total)
SELECT o.order_date,
       c.region_id,
       count(DISTINCT o.id),
       round(sum(oi.line_total)::numeric, 2),
       sum(oi.quantity),
       0
FROM orders o
JOIN customers c ON c.id = o.customer_id
JOIN order_items oi ON oi.order_id = o.id
WHERE o.order_date >= current_date - 90
GROUP BY o.order_date, c.region_id;

-- ══════════════════════════════════════════════════════════════════════════
--  WIDE AUDIT / LINEAGE COLUMNS
--  A real ERP carries created_by / updated_by / source-system / batch /
--  archive / version columns on nearly every table; they are typically sparse
--  (mostly NULL here, as they are in the wild). They exist for that realism
--  AND because their width is what pushes the schema-snapshot estimate past the
--  retrieve node's ~24k budget — so retrieval must select a subset instead of
--  sending everything, which is the whole point of this fixture (see header).
--  Junction tables are intentionally left narrow.
-- ══════════════════════════════════════════════════════════════════════════
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'countries','regions','categories','subcategories','brands','products',
    'product','product_variants','suppliers','warehouses','teams','employees',
    'loyalty_tiers','customers','customer_addresses','currencies','price_lists',
    'tax_rates','promotions','coupons','tags','carriers','payment_methods',
    'orders','order_items','payments','shipments','returns','refunds','reviews',
    'support_tickets','wishlists','order_status_history','product_price_history',
    'sales_daily_rollup'
  ] LOOP
    EXECUTE format(
      'ALTER TABLE %I
         ADD COLUMN created_by   bigint,
         ADD COLUMN updated_by   bigint,
         ADD COLUMN src_system   text,
         ADD COLUMN src_batch_id text,
         ADD COLUMN ext_ref      text,
         ADD COLUMN is_archived  boolean DEFAULT false,
         ADD COLUMN row_version  int DEFAULT 1,
         ADD COLUMN audit_notes  text', t);
  END LOOP;
END $$;

ANALYZE;

-- ══════════════════════════════════════════════════════════════════════════
--  READ-ONLY ROLE
--  Defined after the tables so the blanket SELECT grant covers all of them.
-- ══════════════════════════════════════════════════════════════════════════
CREATE ROLE analytics_ro LOGIN PASSWORD 'analytics_ro';
GRANT CONNECT ON DATABASE sales TO analytics_ro;
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_ro;
REVOKE CREATE ON SCHEMA public FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM PUBLIC;
