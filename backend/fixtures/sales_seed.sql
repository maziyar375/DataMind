-- Seeded "sales" fixture: a richer relational model — 14 related tables with
-- primary and foreign keys, plus a genuinely read-only role.
--
-- The read-only role is the point. Milestone item 3 asserts both directions:
-- readonly_confirmed must be true here and false for a superuser. The extra
-- tables and volume exist so the app can be exercised with real JOINs,
-- aggregations, a foreign-key graph, and time-series questions.

-- ── reference / dimension tables ───────────────────────────────────────────
CREATE TABLE regions (
  id   bigserial PRIMARY KEY,
  name text NOT NULL
);

CREATE TABLE categories (
  id          bigserial PRIMARY KEY,
  name        text NOT NULL,
  description text
);

CREATE TABLE products (
  id          bigserial PRIMARY KEY,
  name        text NOT NULL,
  category    text NOT NULL,
  category_id bigint REFERENCES categories(id),
  sku         text UNIQUE,
  price       numeric(10,2) NOT NULL,
  active      boolean NOT NULL DEFAULT true
);

CREATE TABLE suppliers (
  id             bigserial PRIMARY KEY,
  name           text NOT NULL,
  region_id      bigint REFERENCES regions(id),
  email          text,
  lead_time_days int NOT NULL DEFAULT 7
);

-- Many-to-many: which suppliers can source which products, and at what cost.
CREATE TABLE product_suppliers (
  product_id  bigint NOT NULL REFERENCES products(id),
  supplier_id bigint NOT NULL REFERENCES suppliers(id),
  cost        numeric(10,2) NOT NULL,
  PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE employees (
  id         bigserial PRIMARY KEY,
  name       text NOT NULL,
  title      text NOT NULL,
  region_id  bigint REFERENCES regions(id),
  manager_id bigint REFERENCES employees(id),   -- self-referencing hierarchy
  hired_at   date NOT NULL,
  salary     numeric(10,2) NOT NULL
);

CREATE TABLE warehouses (
  id        bigserial PRIMARY KEY,
  name      text NOT NULL,
  region_id bigint REFERENCES regions(id),
  capacity  int NOT NULL
);

CREATE TABLE customers (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  email        text,
  region_id    bigint REFERENCES regions(id),
  segment      text NOT NULL DEFAULT 'SMB',
  signed_up_at timestamptz NOT NULL DEFAULT now()
);

-- ── fact / transaction tables ──────────────────────────────────────────────
CREATE TABLE orders (
  id           bigserial PRIMARY KEY,
  customer_id  bigint REFERENCES customers(id),
  employee_id  bigint REFERENCES employees(id),   -- sales rep
  order_date   date NOT NULL,
  status       text NOT NULL,
  channel      text NOT NULL DEFAULT 'web',
  total_amount numeric(12,2) NOT NULL
);

CREATE TABLE order_items (
  id         bigserial PRIMARY KEY,
  order_id   bigint REFERENCES orders(id),
  product_id bigint REFERENCES products(id),
  quantity   int NOT NULL,
  unit_price numeric(10,2) NOT NULL,
  discount   numeric(5,2) NOT NULL DEFAULT 0
);

CREATE TABLE inventory (
  id           bigserial PRIMARY KEY,
  product_id   bigint REFERENCES products(id),
  warehouse_id bigint REFERENCES warehouses(id),
  quantity     int NOT NULL,
  UNIQUE (product_id, warehouse_id)
);

CREATE TABLE payments (
  id       bigserial PRIMARY KEY,
  order_id bigint REFERENCES orders(id),
  method   text NOT NULL,
  amount   numeric(12,2) NOT NULL,
  paid_at  timestamptz NOT NULL
);

CREATE TABLE shipments (
  id              bigserial PRIMARY KEY,
  order_id        bigint REFERENCES orders(id),
  warehouse_id    bigint REFERENCES warehouses(id),
  carrier         text NOT NULL,
  tracking_number text,
  shipped_at      timestamptz NOT NULL,
  delivered_at    timestamptz
);

CREATE TABLE returns (
  id            bigserial PRIMARY KEY,
  order_item_id bigint REFERENCES order_items(id),
  reason        text NOT NULL,
  quantity      int NOT NULL,
  refund_amount numeric(10,2) NOT NULL,
  returned_at   timestamptz NOT NULL
);

-- ── data ───────────────────────────────────────────────────────────────────
INSERT INTO regions (name) VALUES
  ('North America'), ('Europe'), ('Asia Pacific'),
  ('Latin America'), ('Middle East'), ('Africa');

INSERT INTO categories (name, description) VALUES
  ('Accessories', 'Hubs, stands, cables and small add-ons'),
  ('Peripherals', 'Keyboards, mice, webcams'),
  ('Displays',    'Monitors and screens'),
  ('Audio',       'Headsets and speakers'),
  ('Networking',  'Routers, switches, adapters'),
  ('Storage',     'Drives and memory');

-- 40 products spread across the six categories.
INSERT INTO products (name, category, category_id, sku, price)
SELECT
  (ARRAY['Accessories','Peripherals','Displays','Audio','Networking','Storage'])[((g % 6) + 1)]
    || ' Model ' || g,
  (ARRAY['Accessories','Peripherals','Displays','Audio','Networking','Storage'])[((g % 6) + 1)],
  (g % 6) + 1,
  'SKU-' || lpad(g::text, 5, '0'),
  round((random() * 380 + 20)::numeric, 2)
FROM generate_series(1, 40) g;

INSERT INTO suppliers (name, region_id, email, lead_time_days)
SELECT 'Supplier ' || g, (g % 6) + 1,
       'sales@supplier' || g || '.example', (g % 21) + 3
FROM generate_series(1, 12) g;

INSERT INTO product_suppliers (product_id, supplier_id, cost)
SELECT g, ((g % 12) + 1), round((random() * 200 + 10)::numeric, 2)
FROM generate_series(1, 40) g;
-- A second source for even-numbered products.
INSERT INTO product_suppliers (product_id, supplier_id, cost)
SELECT g, (((g + 5) % 12) + 1), round((random() * 200 + 10)::numeric, 2)
FROM generate_series(1, 40) g
WHERE g % 2 = 0 AND ((g % 12) + 1) <> (((g + 5) % 12) + 1);

-- 30 employees; the first five are managers (no manager of their own).
INSERT INTO employees (name, title, region_id, manager_id, hired_at, salary)
SELECT
  'Employee ' || g,
  (ARRAY['Sales Rep','Sales Rep','Sales Rep','Account Manager','Regional Manager'])[((g % 5) + 1)],
  (g % 6) + 1,
  CASE WHEN g <= 5 THEN NULL ELSE ((g % 5) + 1) END,
  current_date - ((g * 47) % 2000),
  round((random() * 60000 + 45000)::numeric, 2)
FROM generate_series(1, 30) g;

INSERT INTO warehouses (name, region_id, capacity)
SELECT r.name || ' DC', r.id, (5000 + (r.id * 1500))
FROM regions r;

-- 800 customers.
INSERT INTO customers (name, email, region_id, segment, signed_up_at)
SELECT 'Customer ' || g,
       'customer' || g || '@example.com',
       (g % 6) + 1,
       (ARRAY['SMB','SMB','Mid-Market','Enterprise'])[((g % 4) + 1)],
       now() - ((g % 1000) * interval '1 day')
FROM generate_series(1, 800) g;

-- 5000 orders over the last ~2 years, each with a customer and a sales rep.
INSERT INTO orders (customer_id, employee_id, order_date, status, channel, total_amount)
SELECT (g % 800) + 1,
       (g % 30) + 1,
       current_date - ((g * 3) % 730),
       (ARRAY['completed','completed','completed','completed','shipped',
              'pending','cancelled','returned'])[((g % 8) + 1)],
       (ARRAY['web','web','web','phone','partner'])[((g % 5) + 1)],
       round((random() * 900 + 60)::numeric, 2)
FROM generate_series(1, 5000) g;

-- 15000 order lines.
INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount)
SELECT (g % 5000) + 1,
       (g % 40) + 1,
       (g % 5) + 1,
       round((random() * 250 + 25)::numeric, 2),
       (ARRAY[0,0,0,5,10,15])[((g % 6) + 1)]
FROM generate_series(1, 15000) g;

-- Inventory: every product in every warehouse.
INSERT INTO inventory (product_id, warehouse_id, quantity)
SELECT p, w, (random() * 500)::int
FROM generate_series(1, 40) p CROSS JOIN generate_series(1, 6) w;

-- Payments for orders that were actually fulfilled.
INSERT INTO payments (order_id, method, amount, paid_at)
SELECT o.id,
       (ARRAY['card','card','card','paypal','wire'])[((o.id % 5) + 1)],
       o.total_amount,
       o.order_date + ((o.id % 3) * interval '1 day')
FROM orders o
WHERE o.status IN ('completed','shipped','returned');

-- Shipments for fulfilled orders, delivered a few days later.
INSERT INTO shipments (order_id, warehouse_id, carrier, tracking_number, shipped_at, delivered_at)
SELECT o.id,
       (o.id % 6) + 1,
       (ARRAY['UPS','FedEx','DHL','USPS'])[((o.id % 4) + 1)],
       'TRK' || lpad(o.id::text, 8, '0'),
       o.order_date + interval '1 day',
       o.order_date + ((2 + (o.id % 6)) * interval '1 day')
FROM orders o
WHERE o.status IN ('completed','shipped','returned');

-- Returns on roughly 3% of order lines.
INSERT INTO returns (order_item_id, reason, quantity, refund_amount, returned_at)
SELECT oi.id,
       (ARRAY['defective','wrong item','no longer needed','damaged'])[((oi.id % 4) + 1)],
       1,
       oi.unit_price,
       now() - ((oi.id % 200) * interval '1 day')
FROM order_items oi
WHERE oi.id % 33 = 0;

ANALYZE;

-- The read-only role DataMind is expected to connect with. Defined after the
-- tables exist so the blanket SELECT grant covers all of them.
CREATE ROLE analytics_ro LOGIN PASSWORD 'analytics_ro';
GRANT CONNECT ON DATABASE sales TO analytics_ro;
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_ro;
REVOKE CREATE ON SCHEMA public FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM PUBLIC;
