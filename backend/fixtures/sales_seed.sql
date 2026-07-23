-- Seeded sales fixture: five related tables plus a genuinely read-only role.
-- The read-only role is the point. Milestone item 3 asserts both directions:
-- readonly_confirmed must be true here and false for a superuser.

CREATE TABLE regions (
  id   bigserial PRIMARY KEY,
  name text NOT NULL
);

CREATE TABLE customers (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,
  region_id    bigint REFERENCES regions(id),
  signed_up_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
  id       bigserial PRIMARY KEY,
  name     text NOT NULL,
  category text NOT NULL,
  price    numeric(10,2) NOT NULL
);

CREATE TABLE orders (
  id           bigserial PRIMARY KEY,
  customer_id  bigint REFERENCES customers(id),
  order_date   date NOT NULL,
  status       text NOT NULL,
  total_amount numeric(12,2) NOT NULL
);

CREATE TABLE order_items (
  id         bigserial PRIMARY KEY,
  order_id   bigint REFERENCES orders(id),
  product_id bigint REFERENCES products(id),
  quantity   int NOT NULL,
  unit_price numeric(10,2) NOT NULL
);

INSERT INTO regions (name) VALUES
  ('North America'), ('Europe'), ('Asia Pacific'),
  ('Latin America'), ('Middle East'), ('Africa');

INSERT INTO products (name, category, price) VALUES
  ('USB-C Hub',        'Accessories', 49.00),
  ('Mechanical Keyboard','Peripherals', 129.00),
  ('27" Monitor',      'Displays',    329.00),
  ('Laptop Stand',     'Accessories',  59.00),
  ('Wireless Mouse',   'Peripherals',  39.00),
  ('Webcam 4K',        'Peripherals', 149.00),
  ('Docking Station',  'Accessories', 219.00),
  ('Noise-cancelling Headset', 'Audio', 199.00);

INSERT INTO customers (name, region_id, signed_up_at)
SELECT 'Customer ' || g,
       (g % 6) + 1,
       now() - (g % 900) * interval '1 day'
FROM generate_series(1, 400) g;

INSERT INTO orders (customer_id, order_date, status, total_amount)
SELECT (g % 400) + 1,
       (current_date - ((g % 400))::int),
       (ARRAY['completed','completed','completed','pending','cancelled'])[(g % 5) + 1],
       round((random() * 900 + 60)::numeric, 2)
FROM generate_series(1, 3000) g;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT (g % 3000) + 1,
       (g % 8) + 1,
       (g % 4) + 1,
       round((random() * 250 + 25)::numeric, 2)
FROM generate_series(1, 9000) g;

ANALYZE;

-- The read-only role Raymand is expected to connect with.
CREATE ROLE analytics_ro LOGIN PASSWORD 'analytics_ro';
GRANT CONNECT ON DATABASE sales TO analytics_ro;
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_ro;
REVOKE CREATE ON SCHEMA public FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE sales FROM PUBLIC;
