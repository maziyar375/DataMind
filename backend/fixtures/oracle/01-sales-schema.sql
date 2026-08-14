-- Oracle demo target: a small commerce schema that is *about its comments*.
--
-- This is deliberately NOT a mirror of the 42-table `sales_seed.sql`. The three
-- dialect mirrors (Postgres, MySQL, SQL Server) exist so the same golden eval
-- questions run everywhere, and they are sized so retrieval is exercised. This
-- one has a different job, the same way `fixtures/mysql/` holds Sakila rather
-- than a fourth mirror: it is the only Oracle target in the repository, and it
-- exists so somebody can point DataMind at Oracle and see the thing Oracle is
-- interesting for — `ALL_TAB_COMMENTS` / `ALL_COL_COMMENTS` reaching the model
-- (docs/catalog-metadata-plan.md).
--
-- Every comment below is load-bearing for that demo:
--   * ORDERS.STATUS carries *code meanings* no column name could convey, and a
--     generated semantic layer picks them up as `value_meanings`;
--   * CUSTOMERS.SEGMENT names a row class that must be excluded from revenue,
--     which is the kind of rule that otherwise has to be typed by hand;
--   * RECENT_ORDERS is a VIEW with a comment, and must never reach a snapshot —
--     the connector filters `TABLE_TYPE = 'TABLE'`;
--   * "Orders" is a quoted mixed-case twin of ORDERS, kept because it is the
--     one input that breaks identifier folding (CLAUDE.md, "Oracle identifier
--     case"). It is not a mistake and should not be "fixed".
--
-- Init scripts run as `sqlplus / as sysdba`, which lands in CDB$ROOT on a
-- multitenant XE — hence the container switch. Every object is qualified rather
-- than relying on CURRENT_SCHEMA, so a copy-paste of any statement does the
-- same thing outside this file.
ALTER SESSION SET CONTAINER = XEPDB1;

CREATE TABLE sales.customers (
  id           NUMBER PRIMARY KEY,
  email        VARCHAR2(200),
  signed_up_at DATE,
  segment      VARCHAR2(20)
);

CREATE TABLE sales.products (
  sku        VARCHAR2(40) PRIMARY KEY,
  name       VARCHAR2(200),
  list_price NUMBER(12,2),
  active     NUMBER(1)
);

CREATE TABLE sales.orders (
  id           NUMBER PRIMARY KEY,
  customer_id  NUMBER REFERENCES sales.customers(id),
  status       VARCHAR2(20),
  order_date   DATE,
  total_amount NUMBER(12,2)
);

CREATE TABLE sales.order_items (
  id         NUMBER PRIMARY KEY,
  order_id   NUMBER REFERENCES sales.orders(id),
  sku        VARCHAR2(40) REFERENCES sales.products(sku),
  quantity   NUMBER,
  unit_price NUMBER(12,2)
);

CREATE VIEW sales.recent_orders AS
  SELECT * FROM sales.orders WHERE order_date > SYSDATE - 30;

-- The quoted mixed-case twin. See the header: this is a documented hazard the
-- repository keeps reproducible on purpose, not an accident.
CREATE TABLE sales."Orders" (
  id     NUMBER PRIMARY KEY,
  amount NUMBER(12,2)
);

COMMENT ON TABLE  sales.customers IS 'One row per buyer account. Staff accounts are kept and have segment = INTERNAL.';
COMMENT ON COLUMN sales.customers.email IS 'Login and contact address. Unique in practice, not enforced.';
COMMENT ON COLUMN sales.customers.signed_up_at IS 'Account creation date, UTC.';
COMMENT ON COLUMN sales.customers.segment IS 'RETAIL, WHOLESALE or INTERNAL; INTERNAL rows are staff and are excluded from revenue.';

COMMENT ON TABLE  sales.products IS 'The catalogue. One row per sellable SKU, including discontinued ones.';
COMMENT ON COLUMN sales.products.list_price IS 'Catalogue price in EUR, excluding tax. An order line records the price actually charged.';
COMMENT ON COLUMN sales.products.active IS '1 = still sellable, 0 = discontinued. Discontinued SKUs keep their history.';

COMMENT ON TABLE  sales.orders IS 'One row per checkout. Cancelled orders are kept and still bill.';
COMMENT ON COLUMN sales.orders.status IS 'fulfilment state; C = cancelled by the customer, X = cancelled by us, P = paid.';
COMMENT ON COLUMN sales.orders.order_date IS 'Checkout time, UTC.';
COMMENT ON COLUMN sales.orders.total_amount IS 'Order total in EUR, including tax.';

COMMENT ON TABLE  sales.order_items IS 'One row per line item on an order.';
COMMENT ON COLUMN sales.order_items.quantity IS 'Units sold. Never negative; a return is a separate order.';
COMMENT ON COLUMN sales.order_items.unit_price IS 'Price per unit in EUR at time of sale, which may differ from the catalogue price.';

COMMENT ON TABLE  sales.recent_orders IS 'A VIEW: this comment must never reach a snapshot.';

INSERT INTO sales.customers VALUES (1, 'ada@example.com',   DATE '2026-01-05', 'RETAIL');
INSERT INTO sales.customers VALUES (2, 'grace@example.com', DATE '2026-02-11', 'WHOLESALE');
INSERT INTO sales.customers VALUES (3, 'staff@example.com', DATE '2026-03-01', 'INTERNAL');
INSERT INTO sales.customers VALUES (4, 'linus@example.com', DATE '2026-04-20', 'RETAIL');

INSERT INTO sales.products VALUES ('SKU-1', 'Desk lamp',      60.25, 1);
INSERT INTO sales.products VALUES ('SKU-2', 'Office chair',   89.00, 1);
INSERT INTO sales.products VALUES ('SKU-3', 'Standing desk', 450.00, 0);

INSERT INTO sales.orders VALUES (10, 1, 'P', DATE '2026-07-02', 120.50);
INSERT INTO sales.orders VALUES (11, 2, 'P', DATE '2026-07-14', 890.00);
INSERT INTO sales.orders VALUES (12, 1, 'C', DATE '2026-08-01',  45.00);
INSERT INTO sales.orders VALUES (13, 3, 'P', DATE '2026-08-03', 450.00);
INSERT INTO sales.orders VALUES (14, 4, 'X', DATE '2026-08-05',  89.00);

INSERT INTO sales.order_items VALUES (100, 10, 'SKU-1',  2, 60.25);
INSERT INTO sales.order_items VALUES (101, 11, 'SKU-2', 10, 89.00);
INSERT INTO sales.order_items VALUES (102, 12, 'SKU-1',  1, 45.00);
INSERT INTO sales.order_items VALUES (103, 13, 'SKU-3',  1, 450.00);
INSERT INTO sales.order_items VALUES (104, 14, 'SKU-2',  1, 89.00);

COMMIT;
EXIT;
