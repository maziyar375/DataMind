-- ══════════════════════════════════════════════════════════════════════════
--  "aurora" — the DEMO fixture. A specialty coffee chain, 24 months trading.
--
--  WHY THIS EXISTS ALONGSIDE sales_seed.sql
--  `sales` is the *eval* fixture: 42 tables, deliberately messy — near-duplicate
--  table names, legacy cruft columns, soft-delete traps, a stale rollup that
--  gives wrong answers. That messiness is the point there, because an eval that
--  never fails measures nothing.
--
--  This is the opposite artifact. It is the fixture for a DEMO: every question a
--  presenter is likely to ask should produce correct SQL on the first try from a
--  fast, cheap model, and the result should land as a chart that looks
--  deliberate rather than accidental. So:
--
--    * 12 tables, all clearly named, no traps, no legacy columns, no
--      soft-delete filter to remember, one obvious join path per question.
--    * The schema estimate (`sum(60 + 40*ncols)`, app/pipeline retrieve node) is
--      ~6k against a 50k budget, so the whole snapshot always reaches the
--      generator and retrieval never silently drops the table you need.
--    * `orders` is the single source of truth. `daily_store_metrics` is derived
--      from it by aggregation, so asking the same question two ways reconciles —
--      the opposite of the `sales_daily_rollup` trap.
--
--  CARDINALITIES ARE TUNED TO THE CHART BUDGETS (app/charts/__init__.py)
--  These are not arbitrary row counts. Each one is sized so the chart the
--  question wants is the chart the platform is allowed to draw:
--    product_categories = 6   == MAX_PIE_SLICES   -> a pie of category mix is
--                                never trimmed, and the six angles stay readable
--    channels           = 4   <= MAX_SERIES (8)   -> channel is always a legal
--                                colour split, stacked or normalized
--    loyalty_tiers      = 4   <= MAX_SERIES       -> ditto, and a legal pie
--    regions            = 5   <= MAX_PIE_SLICES   -> ditto
--    stores             = 18  <= MAX_CATEGORY_MARKS (25), and > HORIZONTAL_BAR_FROM
--                                (8), so "revenue by store" is a full, untrimmed
--                                horizontal bar — the flip the platform prefers
--    products           = 24  <= MAX_CATEGORY_MARKS -> "top products" needs no
--                                LIMIT to stay legible
--    24 months of history     -> a monthly line/area steps to a legal tick count
--    7 weekdays x 15 trading hours = 105 cells <= MAX_HEATMAP_CELLS (400)
--    satisfaction_score (10 levels) and prep_seconds (continuous) both clear
--                                MIN_HISTOGRAM_ROWS (20) and MIN_HISTOGRAM_LEVELS (10)
--
--  SIGNAL IS BAKED IN, NOT LEFT TO NOISE
--  A demo chart of uniform random data looks like static. Every series here has
--  a shape a presenter can narrate:
--    * ~1.25%/month compounding growth        -> a line that clearly rises
--    * smooth annual seasonality             -> cold drinks peak mid-July,
--                                               espresso and brewed peak in winter
--    * a weekly pattern (weekday > weekend)   -> visible in a daily line
--    * a bimodal day (07-09 rush, 12-13 second) -> the weekday x hour heatmap
--    * channel mix drift: in-store 62%->44%, mobile app 15%->34% over the two
--      years -> a 100%-stacked area that tells a story on its own
--    * prep time rises at peak hours and satisfaction falls with prep time
--      -> a scatter with a real, findable negative correlation
--
--  Read-only role `analytics_ro` is created at the end so its blanket SELECT
--  grant covers every table.
-- ══════════════════════════════════════════════════════════════════════════

SET client_min_messages = warning;
SELECT setseed(0.7311);   -- reproducible "random" values -> stable aggregates

-- ══════════════════════════════════════════════════════════════════════════
--  REFERENCE / DIMENSION TABLES
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE regions (
  region_id     smallint PRIMARY KEY,
  region_name   text NOT NULL UNIQUE,
  country       text NOT NULL,
  launched_on   date NOT NULL
);
INSERT INTO regions VALUES
  (1, 'Pacific Northwest', 'USA', '2019-03-04'),
  (2, 'Northern California', 'USA', '2020-06-15'),
  (3, 'Southwest',          'USA', '2021-09-01'),
  (4, 'Midwest',            'USA', '2022-04-11'),
  (5, 'Northeast',          'USA', '2023-01-23');

CREATE TABLE store_formats (
  format_code   text PRIMARY KEY,
  format_name   text NOT NULL,
  typical_seats smallint NOT NULL
);
INSERT INTO store_formats VALUES
  ('FLAG', 'Flagship', 64),
  ('STD',  'Standard', 28),
  ('KIOSK','Kiosk',     0);

CREATE TABLE stores (
  store_id         smallint PRIMARY KEY,
  store_name       text NOT NULL UNIQUE,
  region_id        smallint NOT NULL REFERENCES regions(region_id),
  city             text NOT NULL,
  format_code      text NOT NULL REFERENCES store_formats(format_code),
  opened_on        date NOT NULL,
  floor_area_sqm   numeric(6,1) NOT NULL,
  seat_count       smallint NOT NULL,
  has_drive_thru   boolean NOT NULL
);
INSERT INTO stores VALUES
  ( 1,'Pike Place',      1,'Seattle',      'FLAG', '2019-03-04',210.5,72,false),
  ( 2,'Capitol Hill',    1,'Seattle',      'STD',  '2019-11-20',118.0,30,false),
  ( 3,'Ballard Yard',    1,'Seattle',      'STD',  '2021-05-14',102.5,24,true ),
  ( 4,'Portland Pearl',  1,'Portland',     'STD',  '2020-02-08',124.0,34,false),
  ( 5,'SeaTac Concourse',1,'SeaTac',       'KIOSK','2022-07-01', 28.0, 0,false),
  ( 6,'Mission Dolores', 2,'San Francisco','FLAG', '2020-06-15',188.0,58,false),
  ( 7,'Hayes Valley',    2,'San Francisco','STD',  '2021-01-30',110.0,26,false),
  ( 8,'Berkeley Fourth', 2,'Berkeley',     'STD',  '2022-03-19', 96.5,22,false),
  ( 9,'Palo Alto Ramona',2,'Palo Alto',    'KIOSK','2023-08-05', 31.0, 0,false),
  (10,'Roosevelt Row',   3,'Phoenix',      'FLAG', '2021-09-01',176.0,54,true ),
  (11,'Tucson Fourth',   3,'Tucson',       'STD',  '2022-05-21', 99.0,24,true ),
  (12,'Las Vegas Arts',  3,'Las Vegas',    'STD',  '2023-02-11',105.5,28,false),
  (13,'Wicker Park',     4,'Chicago',      'FLAG', '2022-04-11',194.5,66,false),
  (14,'Logan Square',    4,'Chicago',      'STD',  '2022-10-08',108.0,26,false),
  (15,'Ann Arbor Main',  4,'Ann Arbor',    'STD',  '2023-09-16', 94.0,20,false),
  (16,'Beacon Hill',     5,'Boston',       'STD',  '2023-01-23',112.5,28,false),
  (17,'Williamsburg',    5,'Brooklyn',     'STD',  '2023-06-03',119.0,32,false),
  (18,'Back Bay Kiosk',  5,'Boston',       'KIOSK','2024-04-27', 26.5, 0,false);

CREATE TABLE product_categories (
  category_id    smallint PRIMARY KEY,
  category_name  text NOT NULL UNIQUE,
  is_food        boolean NOT NULL,
  is_beverage    boolean NOT NULL
);
INSERT INTO product_categories VALUES
  (1,'Espresso Drinks',   false,true ),
  (2,'Brewed Coffee',     false,true ),
  (3,'Cold Brew & Iced',  false,true ),
  (4,'Tea & Alternatives',false,true ),
  (5,'Bakery',            true, false),
  (6,'Retail Beans',      false,false);

CREATE TABLE products (
  product_id    smallint PRIMARY KEY,
  product_name  text NOT NULL UNIQUE,
  category_id   smallint NOT NULL REFERENCES product_categories(category_id),
  unit_price    numeric(6,2) NOT NULL,
  unit_cost     numeric(6,2) NOT NULL,
  calories      smallint,
  is_seasonal   boolean NOT NULL,
  launched_on   date NOT NULL
);
INSERT INTO products VALUES
  ( 1,'Espresso Doppio',        1, 3.25,0.62, 10,false,'2019-03-04'),
  ( 2,'Cortado',                1, 4.10,0.78, 90,false,'2019-03-04'),
  ( 3,'Flat White',             1, 4.85,0.96,170,false,'2019-03-04'),
  ( 4,'Cappuccino',             1, 4.55,0.88,140,false,'2019-03-04'),
  ( 5,'Caramel Latte',          1, 5.60,1.24,290,false,'2019-06-01'),
  ( 6,'Maple Oat Latte',        1, 5.95,1.38,310,true, '2024-09-15'),
  ( 7,'House Drip',             2, 2.85,0.42,  5,false,'2019-03-04'),
  ( 8,'Single Origin Pourover', 2, 5.25,1.05,  5,false,'2020-01-20'),
  ( 9,'French Press (2 cup)',   2, 6.40,1.18, 10,false,'2021-04-02'),
  (10,'Americano',              2, 3.60,0.58,  15,false,'2019-03-04'),
  (11,'Cold Brew',              3, 4.95,0.84,  15,false,'2019-05-10'),
  (12,'Nitro Cold Brew',        3, 5.75,1.02,  20,false,'2021-06-18'),
  (13,'Iced Shaken Espresso',   3, 5.45,1.08,120,false,'2022-05-02'),
  (14,'Sparkling Yuzu Espresso',3, 6.25,1.30, 90,true, '2025-05-19'),
  (15,'Matcha Latte',           4, 5.85,1.42,220,false,'2020-09-14'),
  (16,'Earl Grey',              4, 3.40,0.36,  0,false,'2019-03-04'),
  (17,'Chai Latte',             4, 5.15,1.12,240,false,'2020-11-02'),
  (18,'Butter Croissant',       5, 4.25,1.35,310,false,'2019-03-04'),
  (19,'Almond Bear Claw',       5, 4.95,1.62,430,false,'2020-03-16'),
  (20,'Morning Bun',            5, 4.50,1.44,380,false,'2019-08-12'),
  (21,'Avocado Toast',          5, 9.75,3.10,350,false,'2021-02-08'),
  (22,'Pumpkin Loaf',           5, 4.80,1.52,400,true, '2024-09-20'),
  (23,'Aurora House Blend 12oz',6,17.50,6.80,  0,false,'2019-03-04'),
  (24,'Ethiopia Guji 12oz',     6,23.00,9.40,  0,false,'2020-02-24');

CREATE TABLE channels (
  channel_id      smallint PRIMARY KEY,
  channel_name    text NOT NULL UNIQUE,
  commission_rate numeric(4,3) NOT NULL
);
INSERT INTO channels VALUES
  (1,'In-Store',         0.000),
  (2,'Mobile App',       0.000),
  (3,'Web Pickup',       0.015),
  (4,'Delivery Partner', 0.180);

CREATE TABLE loyalty_tiers (
  tier_id          smallint PRIMARY KEY,
  tier_name        text NOT NULL UNIQUE,
  min_annual_spend numeric(8,2) NOT NULL,
  discount_rate    numeric(4,3) NOT NULL
);
INSERT INTO loyalty_tiers VALUES
  (1,'Bronze',    0.00,0.000),
  (2,'Silver',  250.00,0.030),
  (3,'Gold',    750.00,0.060),
  (4,'Platinum',2000.00,0.100);

-- ══════════════════════════════════════════════════════════════════════════
--  PEOPLE
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE customers (
  customer_id   integer PRIMARY KEY,
  signup_date   date NOT NULL,
  tier_id       smallint NOT NULL REFERENCES loyalty_tiers(tier_id),
  region_id     smallint NOT NULL REFERENCES regions(region_id),
  birth_year    smallint NOT NULL,
  email_opt_in  boolean NOT NULL
);
-- Signups accelerate over the window (sqrt spacing), so "new customers by month"
-- rises instead of sitting flat. Tier mix is deliberately pyramid-shaped:
-- Bronze 45 / Silver 30 / Gold 18 / Platinum 7 — a legal 4-slice pie that is
-- visibly unequal, which is what makes a pie worth drawing at all.
INSERT INTO customers
SELECT g,
       DATE '2024-08-01' + (sqrt(random()) * 729)::int,
       CASE WHEN random() < 0.45 THEN 1
            WHEN random() < 0.55 THEN 2
            WHEN random() < 0.72 THEN 3
            ELSE 4 END,
       1 + floor(random() * 5)::int,
       (1958 + random() * 45)::int,
       random() < 0.62
FROM generate_series(1, 1400) g;

CREATE TABLE employees (
  employee_id   smallint PRIMARY KEY,
  full_name     text NOT NULL,
  store_id      smallint NOT NULL REFERENCES stores(store_id),
  job_title     text NOT NULL,
  hired_on      date NOT NULL,
  hourly_rate   numeric(5,2) NOT NULL,
  training_hours smallint NOT NULL
);
INSERT INTO employees
SELECT g,
       (ARRAY['Ava','Noah','Mia','Liam','Zoe','Kai','Ivy','Owen','Luna','Eli',
              'Nora','Milo','Sage','Theo','Iris','Rex','Wren','Cruz','Faye','Jude']
         )[1 + (g - 1) % 20] || ' ' ||
       (ARRAY['Reyes','Okafor','Lindqvist','Moreau','Tanaka','Silva','Novak',
              'Haddad','Ferrari','Nguyen','Brennan','Osei']
         )[1 + (g * 7 - 1) % 12],
       1 + (g - 1) % 18,
       CASE WHEN g % 18 = 0 THEN 'Store Manager'
            WHEN g % 6  = 0 THEN 'Shift Lead'
            ELSE 'Barista' END,
       DATE '2024-08-01' + (random() * 600)::int,
       round((19.5 + random() * 11)::numeric, 2),
       (12 + random() * 60)::int
FROM generate_series(1, 72) g;

CREATE TABLE marketing_campaigns (
  campaign_id        smallint PRIMARY KEY,
  campaign_name      text NOT NULL UNIQUE,
  channel_id         smallint NOT NULL REFERENCES channels(channel_id),
  start_date         date NOT NULL,
  end_date           date NOT NULL,
  spend              numeric(10,2) NOT NULL,
  impressions        integer NOT NULL,
  attributed_revenue numeric(10,2) NOT NULL
);
-- Ten campaigns: spend in thousands against revenue in tens of thousands. The
-- ~10x gap is on purpose — it trips DUAL_AXIS_RATIO, so "spend vs attributed
-- revenue by campaign" compiles to a genuine dual-axis combo chart rather than
-- one series flattened against the other.
INSERT INTO marketing_campaigns VALUES
  ( 1,'Autumn Maple Launch',   2,'2024-09-15','2024-10-31', 18400.00, 1240000,  196500.00),
  ( 2,'Holiday Beans Gifting', 3,'2024-11-20','2024-12-26', 26900.00, 1880000,  341200.00),
  ( 3,'New Year Reset',        2,'2025-01-06','2025-02-02',  9800.00,  620000,   88400.00),
  ( 4,'App Reorder Push',      2,'2025-03-10','2025-04-20', 21500.00, 1410000,  268900.00),
  ( 5,'Cold Brew Season',      1,'2025-05-19','2025-07-31', 31200.00, 2050000,  452800.00),
  ( 6,'Campus Return',         3,'2025-08-25','2025-09-30', 14600.00,  940000,  151300.00),
  ( 7,'Holiday Beans 2025',    3,'2025-11-19','2025-12-26', 29800.00, 2110000,  398600.00),
  ( 8,'Winter Warmers',        1,'2026-01-12','2026-02-28', 12400.00,  780000,  118900.00),
  ( 9,'Yuzu Summer Drop',      2,'2026-05-18','2026-07-15', 34500.00, 2380000,  511400.00),
  (10,'Loyalty Double Stars',  4,'2026-06-29','2026-07-26', 16200.00, 1020000,  187600.00);

-- ══════════════════════════════════════════════════════════════════════════
--  SHAPE TABLES (temporary)
--  The distributions the fact table is drawn from. Each is stored as
--  cumulative [lo, hi) bounds so a single random() draw joins straight to an
--  outcome — no per-row sort, no correlated subquery.
-- ══════════════════════════════════════════════════════════════════════════

-- Trading hours, 06:00-20:00. Weekdays are sharply bimodal (the 07-09 commute
-- rush, then a smaller 12-13 lunch). Weekends flatten and shift ~2h later.
-- This is the single most important distribution in the file: it is what makes
-- the weekday x hour heatmap worth putting in a demo.
CREATE TEMP TABLE hour_w AS
WITH raw(profile, hour, w) AS (VALUES
  ('WD', 6,0.030),('WD', 7,0.092),('WD', 8,0.138),('WD', 9,0.101),('WD',10,0.068),
  ('WD',11,0.055),('WD',12,0.094),('WD',13,0.079),('WD',14,0.058),('WD',15,0.057),
  ('WD',16,0.052),('WD',17,0.048),('WD',18,0.038),('WD',19,0.026),('WD',20,0.016),
  ('WE', 6,0.010),('WE', 7,0.030),('WE', 8,0.062),('WE', 9,0.104),('WE',10,0.132),
  ('WE',11,0.128),('WE',12,0.106),('WE',13,0.088),('WE',14,0.074),('WE',15,0.066),
  ('WE',16,0.058),('WE',17,0.049),('WE',18,0.040),('WE',19,0.031),('WE',20,0.022)
)
SELECT profile, hour::smallint AS hour,
       (sum(w) OVER (PARTITION BY profile ORDER BY hour) - w) / sum(w) OVER (PARTITION BY profile) AS lo,
        sum(w) OVER (PARTITION BY profile ORDER BY hour)      / sum(w) OVER (PARTITION BY profile) AS hi
FROM raw;

-- Channel mix drifting month by month across the 24-month window: in-store
-- 62%->44% while the mobile app climbs 15%->34%. Linear interpolation, so a
-- 100%-stacked area of channel share is a clean widening wedge rather than
-- noise. `t` runs 0 -> 1 across the window.
CREATE TEMP TABLE channel_w AS
WITH m AS (
  SELECT mon, (row_number() OVER (ORDER BY mon) - 1) / 23.0 AS t
  FROM generate_series(DATE '2024-08-01', DATE '2026-07-01', INTERVAL '1 month') mon
),
raw AS (
  SELECT m.mon::date AS mon, c.channel_id,
         CASE c.channel_id
           WHEN 1 THEN 0.62 + (0.44 - 0.62) * m.t
           WHEN 2 THEN 0.15 + (0.34 - 0.15) * m.t
           WHEN 3 THEN 0.13 + (0.14 - 0.13) * m.t
           ELSE        0.10 + (0.08 - 0.10) * m.t
         END AS w
  FROM m CROSS JOIN channels c
)
SELECT mon, channel_id,
       (sum(w) OVER (PARTITION BY mon ORDER BY channel_id) - w) / sum(w) OVER (PARTITION BY mon) AS lo,
        sum(w) OVER (PARTITION BY mon ORDER BY channel_id)      / sum(w) OVER (PARTITION BY mon) AS hi
FROM raw;

-- Category mix by month, driven by a cosine peaking at day-of-year 196
-- (mid-July). Cold Brew & Iced swings 3% -> 25% of the mix across the year
-- while espresso and brewed coffee move the other way, and Retail Beans spikes
-- every December (gifting). This is what puts a real, explainable seasonal
-- swing into "revenue by category over time".
CREATE TEMP TABLE cat_w AS
WITH m AS (
  SELECT mon::date AS mon,
         cos(2 * pi() * (extract(doy FROM mon) - 196) / 365.0) AS s,
         extract(month FROM mon)::int AS mnum
  FROM generate_series(DATE '2024-08-01', DATE '2026-07-01', INTERVAL '1 month') mon
),
raw AS (
  SELECT m.mon, c.category_id,
         CASE c.category_id
           WHEN 1 THEN 0.300 - 0.050 * m.s
           WHEN 2 THEN 0.180 - 0.055 * m.s
           WHEN 3 THEN 0.140 + 0.110 * m.s
           WHEN 4 THEN 0.100 - 0.010 * m.s
           WHEN 5 THEN 0.200
           ELSE        0.080 - 0.010 * m.s + CASE WHEN m.mnum = 12 THEN 0.055 ELSE 0 END
         END AS w
  FROM m CROSS JOIN product_categories c
)
SELECT mon, category_id,
       (sum(w) OVER (PARTITION BY mon ORDER BY category_id) - w) / sum(w) OVER (PARTITION BY mon) AS lo,
        sum(w) OVER (PARTITION BY mon ORDER BY category_id)      / sum(w) OVER (PARTITION BY mon) AS hi
FROM raw;

-- Popularity *within* a category, so "top 10 products" is a ranked staircase
-- rather than 24 bars of the same height. Weights are hand-set per product.
CREATE TEMP TABLE prod_w AS
WITH raw AS (
  SELECT product_id, category_id,
         (ARRAY[0.09,0.14,0.26,0.19,0.22,0.10,   -- 1-6   espresso drinks
                0.42,0.16,0.09,0.33,             -- 7-10  brewed
                0.38,0.24,0.29,0.09,             -- 11-14 cold
                0.41,0.26,0.33,                  -- 15-17 tea
                0.31,0.17,0.22,0.19,0.11,        -- 18-22 bakery
                0.62,0.38])[product_id] AS w     -- 23-24 beans
  FROM products
)
SELECT category_id, product_id,
       (sum(w) OVER (PARTITION BY category_id ORDER BY product_id) - w) / sum(w) OVER (PARTITION BY category_id) AS lo,
        sum(w) OVER (PARTITION BY category_id ORDER BY product_id)      / sum(w) OVER (PARTITION BY category_id) AS hi
FROM raw;

-- Customers eligible on a given day: the ones already signed up, indexed per
-- region so an order can draw one with a single random index. Keeping the
-- signup_date <= ordered_at invariant is what makes cohort questions honest.
CREATE TEMP TABLE cust_seq AS
SELECT region_id,
       row_number() OVER (PARTITION BY region_id ORDER BY signup_date, customer_id) AS seq,
       customer_id, signup_date
FROM customers;
CREATE INDEX ON cust_seq (region_id, seq);

CREATE TEMP TABLE cust_avail AS
SELECT r.region_id, d.d::date AS d,
       (SELECT count(*) FROM cust_seq c WHERE c.region_id = r.region_id AND c.signup_date <= d.d) AS cnt
FROM regions r
CROSS JOIN generate_series(DATE '2024-08-01', DATE '2026-07-31', INTERVAL '1 day') d;
CREATE INDEX ON cust_avail (region_id, d);

-- One row per employee per store, indexed, so an order picks a server cheaply.
CREATE TEMP TABLE emp_seq AS
SELECT store_id, row_number() OVER (PARTITION BY store_id ORDER BY employee_id) AS seq,
       employee_id, count(*) OVER (PARTITION BY store_id) AS n
FROM employees;
CREATE INDEX ON emp_seq (store_id, seq);

-- ══════════════════════════════════════════════════════════════════════════
--  ORDERS — the one source of truth
--
--  Volume per store-day is a product of six explainable factors rather than a
--  flat random draw, which is why every time series drawn from this table has
--  a readable shape:
--    format    Flagship 16/day, Standard 8, Kiosk 4
--    region    +-15% around 1.0
--    trend     +1.25% compounding per month  -> ~+30% by the final month
--    weekday   Mon-Fri ~1.10, Sat 0.85, Sun 0.62
--    season    +-10% on a cosine peaking mid-July
--    campaign  +18% while a marketing_campaigns window is open
--  ~156k orders across 730 days (2024-08-01 .. 2026-07-31).
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE orders (
  order_id           bigint PRIMARY KEY,
  ordered_at         timestamp NOT NULL,
  store_id           smallint NOT NULL REFERENCES stores(store_id),
  customer_id        integer REFERENCES customers(customer_id),
  channel_id         smallint NOT NULL REFERENCES channels(channel_id),
  employee_id        smallint NOT NULL REFERENCES employees(employee_id),
  payment_method     text NOT NULL,
  item_count         integer NOT NULL,
  gross_amount       numeric(10,2) NOT NULL,
  discount_amount    numeric(10,2) NOT NULL,
  tip_amount         numeric(10,2) NOT NULL,
  order_total        numeric(10,2) NOT NULL,
  prep_seconds       integer NOT NULL,
  satisfaction_score integer NOT NULL,
  is_refunded        boolean NOT NULL
);

INSERT INTO orders (
  order_id, ordered_at, store_id, customer_id, channel_id, employee_id,
  payment_method, item_count, gross_amount, discount_amount, tip_amount,
  order_total, prep_seconds, satisfaction_score, is_refunded)
WITH day_store AS (
  SELECT d::date AS d,
         s.store_id,
         s.region_id,
         extract(dow FROM d)::int AS dow,
         greatest(1, round(
             CASE s.format_code WHEN 'FLAG' THEN 16 WHEN 'STD' THEN 8 ELSE 4 END
           * CASE s.region_id WHEN 1 THEN 1.10 WHEN 2 THEN 1.15 WHEN 3 THEN 0.92
                              WHEN 4 THEN 0.98 ELSE 1.05 END
           * (1 + 0.0125 * ((d::date - DATE '2024-08-01') / 30.44))
           * CASE extract(dow FROM d)::int WHEN 0 THEN 0.62 WHEN 6 THEN 0.85 ELSE 1.10 END
           * (1 + 0.10 * cos(2 * pi() * (extract(doy FROM d) - 196) / 365.0))
           * CASE WHEN EXISTS (SELECT 1 FROM marketing_campaigns mc
                                WHERE d::date BETWEEN mc.start_date AND mc.end_date)
                  THEN 1.18 ELSE 1.0 END
         )::int) AS n
  FROM generate_series(DATE '2024-08-01', DATE '2026-07-31', INTERVAL '1 day') d
  CROSS JOIN stores s
),
draws AS MATERIALIZED (
  SELECT ds.d, ds.store_id, ds.region_id, ds.dow,
         CASE WHEN ds.dow IN (0, 6) THEN 'WE' ELSE 'WD' END AS profile,
         date_trunc('month', ds.d)::date AS mon,
         random() AS r_hour, random() AS r_chan, random() AS r_cust,
         random() AS r_emp,  random() AS r_pay,  random() AS r_prep,
         random() AS r_sat,  random() AS r_ref,  random() AS r_has_cust,
         (random() * 3540)::int AS r_secs
  FROM day_store ds, LATERAL generate_series(1, ds.n) k
)
SELECT row_number() OVER (ORDER BY x.d, x.store_id, x.hour, x.r_secs) AS order_id,
       x.d + make_interval(hours => x.hour::int, secs => x.r_secs) AS ordered_at,
       x.store_id,
       x.customer_id,
       x.channel_id,
       x.employee_id,
       -- Payment mix follows the channel: cash only really happens in store,
       -- and the app is overwhelmingly wallet.
       CASE
         WHEN x.channel_id = 1 THEN
           CASE WHEN x.r_pay < 0.55 THEN 'Card'
                WHEN x.r_pay < 0.73 THEN 'Cash'
                WHEN x.r_pay < 0.93 THEN 'Mobile Wallet'
                ELSE 'Gift Card' END
         WHEN x.channel_id = 2 THEN
           CASE WHEN x.r_pay < 0.60 THEN 'Mobile Wallet'
                WHEN x.r_pay < 0.93 THEN 'Card'
                ELSE 'Gift Card' END
         ELSE
           CASE WHEN x.r_pay < 0.82 THEN 'Card'
                WHEN x.r_pay < 0.95 THEN 'Mobile Wallet'
                ELSE 'Gift Card' END
       END AS payment_method,
       0::integer, 0::numeric, 0::numeric, 0::numeric, 0::numeric,  -- filled below
       x.prep_seconds,
       -- Satisfaction is a function of prep time plus noise, so the scatter of
       -- one against the other holds a real negative correlation to find.
       greatest(1, least(10, round(9.2 - x.prep_seconds / 95.0
                                       + (x.r_sat * 6.0 - 3.0))))::integer,
       x.r_ref < 0.012
FROM (
  SELECT dr.*,
         h.hour,
         ch.channel_id,
         e.employee_id,
         CASE WHEN dr.r_has_cust < 0.66 AND ca.cnt > 0 THEN cs.customer_id END AS customer_id,
         greatest(45, least(900, round(
             60
           + CASE h.hour WHEN 7 THEN 90 WHEN 8 THEN 95 WHEN 12 THEN 55 ELSE 0 END
           - ln(1 - dr.r_prep * 0.999) * 65
         )::int))::integer AS prep_seconds
  FROM draws dr
  JOIN hour_w h      ON h.profile = dr.profile AND dr.r_hour >= h.lo AND dr.r_hour < h.hi
  JOIN channel_w ch  ON ch.mon = dr.mon AND dr.r_chan >= ch.lo AND dr.r_chan < ch.hi
  JOIN cust_avail ca ON ca.region_id = dr.region_id AND ca.d = dr.d
  LEFT JOIN cust_seq cs ON cs.region_id = dr.region_id
                       AND cs.seq = 1 + floor(dr.r_cust * ca.cnt)::int
  JOIN emp_seq e     ON e.store_id = dr.store_id
                    AND e.seq = 1 + floor(dr.r_emp * e.n)::int
) x;

CREATE INDEX ON orders (ordered_at);
CREATE INDEX ON orders (store_id);
CREATE INDEX ON orders (customer_id);

-- ══════════════════════════════════════════════════════════════════════════
--  ORDER ITEMS
--  1-4 lines per order (mean ~2.1). The category is drawn from that month's
--  seasonal mix, the product from popularity within the category, so both
--  "revenue by category over time" and "top products" have real structure.
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE order_items (
  order_item_id bigint PRIMARY KEY,
  order_id      bigint NOT NULL REFERENCES orders(order_id),
  product_id    smallint NOT NULL REFERENCES products(product_id),
  quantity      integer NOT NULL,
  unit_price    numeric(6,2) NOT NULL,
  line_total    numeric(10,2) NOT NULL
);

INSERT INTO order_items (order_item_id, order_id, product_id, quantity, unit_price, line_total)
-- NOTE: the per-order line count is computed in its own MATERIALIZED CTE and
-- only then expanded. Putting `random()` directly in the LATERAL
-- generate_series bound does NOT work — Postgres evaluates that argument once
-- for the whole statement, so every order silently ends up with the same
-- number of lines.
WITH ord AS MATERIALIZED (
  SELECT o.order_id,
         date_trunc('month', o.ordered_at)::date AS mon,
         CASE WHEN random() < 0.42 THEN 1
              WHEN random() < 0.73 THEN 2
              WHEN random() < 0.91 THEN 3
              ELSE 4 END AS n_lines
  FROM orders o
),
lines AS MATERIALIZED (
  SELECT ord.order_id,
         ord.mon,
         random() AS r_cat,
         random() AS r_prod,
         CASE WHEN random() < 0.74 THEN 1
              WHEN random() < 0.85 THEN 2
              ELSE 3 END AS quantity
  FROM ord, LATERAL generate_series(1, ord.n_lines) ln
)
SELECT row_number() OVER (ORDER BY l.order_id, p.product_id) AS order_item_id,
       l.order_id,
       p.product_id,
       l.quantity,
       pr.unit_price,
       round(pr.unit_price * l.quantity, 2)
FROM lines l
JOIN cat_w  c ON c.mon = l.mon AND l.r_cat  >= c.lo AND l.r_cat  < c.hi
JOIN prod_w p ON p.category_id = c.category_id AND l.r_prod >= p.lo AND l.r_prod < p.hi
JOIN products pr ON pr.product_id = p.product_id;

CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);

-- Roll the lines back up into the order. Doing it this way — rather than
-- inventing an order_total and hoping it matches — is what makes
-- "revenue from order_items" and "revenue from orders" agree to the cent.
-- The loyalty discount is the tier's own rate, so "average discount by tier"
-- returns exactly the rate card in loyalty_tiers.
CREATE TEMP TABLE order_agg AS
SELECT ag.order_id,
       ag.item_count,
       ag.gross,
       COALESCE(lt.discount_rate, 0) AS discount_rate,
       -- Tip is a deterministic function of the order id, not a random draw:
       -- it keeps re-seeding reproducible and still spreads 5%-18%.
       CASE WHEN o.channel_id IN (1, 2, 3)
            THEN (0.05 + (o.order_id % 13) * 0.011)::numeric
            ELSE 0 END AS tip_rate
FROM (SELECT order_id, sum(quantity)::integer AS item_count, sum(line_total) AS gross
      FROM order_items GROUP BY order_id) ag
JOIN orders o           ON o.order_id = ag.order_id
LEFT JOIN customers cu  ON cu.customer_id = o.customer_id
LEFT JOIN loyalty_tiers lt ON lt.tier_id = cu.tier_id;
CREATE UNIQUE INDEX ON order_agg (order_id);

UPDATE orders o
SET item_count      = a.item_count,
    gross_amount    = a.gross,
    discount_amount = round(a.gross * a.discount_rate, 2),
    tip_amount      = round(a.gross * a.tip_rate, 2),
    order_total     = round(a.gross * (1 - a.discount_rate), 2)
                    + round(a.gross * a.tip_rate, 2)
FROM order_agg a
WHERE o.order_id = a.order_id;

-- ══════════════════════════════════════════════════════════════════════════
--  DAILY STORE METRICS — derived, never invented
--  A pre-aggregated table for the "how did each store trade" questions, built
--  by aggregating `orders` so it can never disagree with it. `net_revenue` in
--  the thousands against `transaction_count` in the hundreds is roughly a 10x
--  gap, which is what makes a spend-vs-count combo chart pick a second axis.
-- ══════════════════════════════════════════════════════════════════════════

CREATE TABLE daily_store_metrics (
  metric_date       date NOT NULL,
  store_id          smallint NOT NULL REFERENCES stores(store_id),
  transaction_count integer NOT NULL,
  net_revenue       numeric(12,2) NOT NULL,
  avg_ticket        numeric(8,2) NOT NULL,
  unique_customers  integer NOT NULL,
  avg_prep_seconds  numeric(6,1) NOT NULL,
  avg_satisfaction  numeric(4,2) NOT NULL,
  labor_hours       numeric(6,1) NOT NULL,
  waste_kg          numeric(6,2) NOT NULL,
  PRIMARY KEY (metric_date, store_id)
);

INSERT INTO daily_store_metrics
SELECT o.ordered_at::date,
       o.store_id,
       count(*),
       round(sum(o.order_total), 2),
       round(avg(o.order_total), 2),
       count(DISTINCT o.customer_id),
       round(avg(o.prep_seconds)::numeric, 1),
       round(avg(o.satisfaction_score)::numeric, 2),
       round((count(*) * 0.11 + 8)::numeric, 1),
       round((count(*) * 0.014 + random() * 1.5)::numeric, 2)
FROM orders o
WHERE NOT o.is_refunded
GROUP BY o.ordered_at::date, o.store_id;

ANALYZE;

-- ══════════════════════════════════════════════════════════════════════════
--  READ-ONLY ROLE
--  Defined after the tables so the blanket SELECT grant covers all of them.
--  Same shape as the `sales` fixture: DataMind reaches a customer database
--  over a connector holding a role that cannot write.
-- ══════════════════════════════════════════════════════════════════════════
CREATE ROLE analytics_ro LOGIN PASSWORD 'analytics_ro';
GRANT CONNECT ON DATABASE aurora TO analytics_ro;
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analytics_ro;
REVOKE CREATE ON SCHEMA public FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE aurora FROM analytics_ro;
REVOKE TEMPORARY ON DATABASE aurora FROM PUBLIC;
