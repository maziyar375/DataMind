-- Catalog comments for the `sales` fixture — the COMMENTED ARM of the eval A/B.
--
-- Loaded ON TOP OF sales_seed.sql, never instead of it. `sales_seed.sql` stays
-- comment-free on purpose: the two arms of the measurement are "the same 42
-- tables, with and without their DDL documentation", and that is only true if
-- the uncommented arm is byte-for-byte the fixture every earlier eval ran
-- against. Adding a COMMENT ON to the seed would silently move the baseline.
--
--   python -m app.eval.runner --suite sales_v1                 # uncommented arm
--   python -m app.eval.runner --suite sales_v1 --comments      # commented arm
--
-- The compose `sales` demo mounts this as a second init script, so the running
-- demo shows descriptions in the schema browser (docs/catalog-metadata-plan.md
-- §7 Phase 6). `make fixtures` loads and counts them.
--
-- WHAT IS DOCUMENTED
-- 21 tables and 42 columns, plus the database and the schema, written the way a
-- DBA who knew the business would write them: the grain of a fact table, what a
-- status code means, which rows must be excluded, and which table is a trap.
-- Nothing here restates a column name (`comments.py::is_noise` would drop it
-- anyway) and nothing encodes a gold answer — these are descriptions of the
-- schema, not of the questions.
--
-- Lengths are a DBA's, not the renderer's. Everything fits the STORED caps (400
-- table / 240 column, `connectors/comments.py`), and about a third of the column
-- comments run past the tighter RENDER cap (120 chars, §4.4) and are clipped on
-- a word boundary in the run prompt — which is the feature as built, so the
-- measurement should feel it. The two that carry a value list rather than a
-- sentence (orders.status, customers.is_deleted) are written short enough to
-- survive intact, because a DBA writing a code list puts the codes first.
--
-- TWO PLANTS, DELIBERATE — DO NOT "FIX" THEM
-- Real catalogs carry comments that have rotted, and a feature that only works
-- against perfect documentation is not verified. So exactly two are untrue, and
-- they are the whole reason the prompt rule in §5.1 exists ("It can be stale or
-- wrong — if it contradicts the column names and types you can see, say what
-- you can support"):
--
--   STALE  customers.segment  — lists a `Reseller` tier that no longer exists in
--          the data (the seed writes only SMB / Mid-Market / Enterprise). True
--          when written, false now: the shape staleness actually takes.
--   WRONG  orders.subtotal    — claims to be the amount the customer paid, which
--          is `total_amount`. Flatly contradicted by the sibling columns. It is
--          planted on a column NO gold query uses, so a model that believes it
--          loses revenue questions it would otherwise win — the trap is real and
--          costs nothing if the model reads the schema over the prose.
--
-- Both are listed here and in §10 of the plan. If a run's report blames either,
-- that is the measurement working.

-- ══════════════════════════════════════════════════════════════════════════
--  DATABASE AND SCHEMA
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON DATABASE sales IS
  'Order-to-cash for the online storefront: catalogue, customers, orders, fulfilment and returns. Loaded nightly from the ERP; the current day is always partial until the 03:00 UTC run.';

COMMENT ON SCHEMA public IS
  'The whole operational model. There is no staging schema here — extracts land straight in these tables.';

-- ══════════════════════════════════════════════════════════════════════════
--  FACT TABLES
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE orders IS
  'One row per checkout, at any stage of its life. Not every order is revenue: cancelled and returned orders stay here with their money on them, so anything that means "what we actually sold" has to filter on status.';
COMMENT ON COLUMN orders.status IS
  'completed = fulfilled and paid; shipped = in transit; pending = unfulfilled; cancelled = called off; returned = refunded';
COMMENT ON COLUMN orders.channel IS
  'How the order reached us: web (the storefront), phone (taken by a rep), partner (a reseller''s system). Web orders have no employee_id.';
COMMENT ON COLUMN orders.total_amount IS
  'What the customer was billed: subtotal less discount, plus tax and shipping. This is the revenue figure for an order.';
COMMENT ON COLUMN orders.subtotal IS
  'The amount the customer actually paid us, tax and shipping included.';   -- PLANTED, WRONG (see header)
COMMENT ON COLUMN orders.order_date IS
  'The calendar day the order was placed, in the storefront''s local time. placed_at is the same event with a timestamp.';
COMMENT ON COLUMN orders.employee_id IS
  'The rep who booked the order. NULL for web orders, which is most of them — an inner join here silently drops the storefront.';
COMMENT ON COLUMN orders.discount_total IS
  'Money taken off the subtotal by promotions and coupons together. The per-promotion split is in order_promotions.';
COMMENT ON COLUMN orders.cust_ref IS
  'Legacy reference from the OMS we retired in 2022. Sparse and not a key — never join on it.';

COMMENT ON TABLE order_items IS
  'One row per product line on an order. This is the bridge between the catalogue and revenue: anything about products, brands, suppliers or tags reaches orders only through here.';
COMMENT ON COLUMN order_items.line_total IS
  'The line''s value after its discount: quantity x unit_price, rounded. Summing this over an order gives its subtotal, not its total_amount.';
COMMENT ON COLUMN order_items.unit_price IS
  'Price of one unit as sold on this line. It is the price at the time of sale, so it can differ from products.price today.';
COMMENT ON COLUMN order_items.discount IS
  'Percent off this line, 0 when there was none. Already reflected in line_total — do not subtract it a second time.';
COMMENT ON COLUMN order_items.variant_id IS
  'The colour/size variant sold, when the line was placed against one. NULL on lines sold at product level.';

COMMENT ON TABLE payments IS
  'Money received against an order. Only fulfilled orders (completed, shipped, returned) have payments, so counting orders through this table quietly drops everything pending or cancelled.';
COMMENT ON COLUMN payments.amount IS
  'Amount captured, in the order''s currency. One payment per order here — instalments were never migrated.';
COMMENT ON COLUMN payments.status IS
  'Settlement state of the capture. Everything in this database is captured; authorised-only and failed attempts live in the gateway, not here.';
COMMENT ON COLUMN payments.paid_at IS
  'When the money was captured, up to three days after the order was placed. Use orders.order_date for "when did we sell it".';

COMMENT ON TABLE returns IS
  'One row per returned order line. A return is approved or rejected; only approved ones produce a refund, so joining returns to refunds without checking status overstates what we paid back.';
COMMENT ON COLUMN returns.refund_amount IS
  'What was credited for the returned units, before any restocking fee. Compare with refunds.amount, which is what actually left the account.';
COMMENT ON COLUMN returns.reason IS
  'Free text the agent picked from a fixed list — defective, wrong item, damaged, no longer needed. Not validated, so treat it as a label rather than a code.';

COMMENT ON TABLE refunds IS
  'Money sent back to the customer for an approved return. payment_id is nullable: store-credit refunds are not tied to an original capture.';

COMMENT ON TABLE shipments IS
  'One dispatch from a warehouse. An order can ship in more than one consignment, so counting shipments is not counting orders.';
COMMENT ON COLUMN shipments.delivered_at IS
  'When the carrier confirmed delivery. NULL while the consignment is still in transit — which is every shipment on a non-completed order.';
COMMENT ON COLUMN shipments.status IS
  'delivered once the carrier confirmed, in_transit until then. Derived from the order, not scanned in real time.';

COMMENT ON TABLE reviews IS
  'Customer product reviews, 1-5 stars. is_hidden marks the ones moderation pulled; they stay in the table and should be excluded from public-facing averages.';
COMMENT ON COLUMN reviews.rating IS
  'Stars out of 5, whole numbers only. 1 is the worst.';
COMMENT ON COLUMN reviews.customer_id IS
  'Who wrote it, when we know. NULL means the review was left anonymously.';

COMMENT ON TABLE support_tickets IS
  'One row per customer contact. Not all of them are about an order — order_id is NULL for pre-sales and account questions.';
COMMENT ON COLUMN support_tickets.status IS
  'open and pending are still with us; resolved and closed are finished. closed_at is only set for the last two.';

-- ══════════════════════════════════════════════════════════════════════════
--  DIMENSIONS
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE customers IS
  'One row per customer account, including the ones we no longer trade with. Rows are never deleted — is_deleted marks a closed account, so any question about our customer base has to filter it.';
COMMENT ON COLUMN customers.is_deleted IS
  'Soft delete: true means the account is closed. Rows stay for the order history hanging off them — about 8% of the table.';
COMMENT ON COLUMN customers.segment IS
  'Commercial segment the account is managed under: SMB, Mid-Market, Enterprise, or Reseller for indirect accounts.';   -- PLANTED, STALE (see header)
COMMENT ON COLUMN customers.last_order_at IS
  'Timestamp of the account''s most recent order, refreshed by the nightly load. NULL for accounts that have never ordered.';
COMMENT ON COLUMN customers.referred_by_id IS
  'The existing customer who referred this one, when the sign-up came through the referral programme. Self-referencing and mostly NULL.';
COMMENT ON COLUMN customers.credit_limit IS
  'Ceiling on unpaid invoices for this account, in USD. A commercial term, not a spending total — it says nothing about what the customer has bought.';

COMMENT ON TABLE products IS
  'The sellable catalogue, current and retired. discontinued means we have stopped selling it; active is the softer "not on the storefront right now". Both stay in the table because old orders point at them.';
COMMENT ON COLUMN products.price IS
  'Current list price in USD, before any promotion. What an order line was actually sold at is order_items.unit_price.';
COMMENT ON COLUMN products.cost IS
  'What we pay our own supplier per unit, in USD. Margin is price - cost. NULL on products we have never bought directly.';
COMMENT ON COLUMN products.category IS
  'Denormalised category label kept for the old reporting stack. category_id is the real relationship; this column is not maintained against it.';
COMMENT ON COLUMN products.discontinued IS
  'true once the product has been withdrawn from sale for good. Withdrawn products keep their order history.';
COMMENT ON COLUMN products.flg_2 IS
  'Legacy flag. Nobody left knows what it meant; do not use it in a filter.';

COMMENT ON TABLE product IS
  'DEPRECATED singular leftover from the 2019 migration, six stale rows, kept alive by one quarterly PDF. The real catalogue is `products`. Nothing here should ever appear in an answer.';

COMMENT ON TABLE employees IS
  'Sales staff, past and present. terminated_at set means they have left; active is the flag the HR feed maintains.';
COMMENT ON COLUMN employees.salary IS
  'Annual base salary in USD, excluding commission. Confidential — aggregate it, do not list it per person.';
COMMENT ON COLUMN employees.manager_id IS
  'The employee this one reports to. Self-referencing; NULL for the six people at the top.';
COMMENT ON COLUMN employees.commission_pct IS
  'Commission rate on booked revenue, as a percentage. 0 for staff who are not on a commission plan.';

COMMENT ON TABLE regions IS
  'Sales territories, which are how the business slices the world. A region groups a country but is not the same thing as one: `Unassigned` has no country at all.';

COMMENT ON TABLE inventory IS
  'Stock on hand: one row per product per warehouse, overwritten by the warehouse feed. It is a snapshot of now, with no history — it cannot answer a question about a past date.';
COMMENT ON COLUMN inventory.quantity IS
  'Units physically on the shelf at that warehouse, including anything already allocated to an unshipped order.';
COMMENT ON COLUMN inventory.reorder_level IS
  'The level at which purchasing is prompted to reorder. A per-product threshold, not a target stock figure.';

COMMENT ON TABLE suppliers IS
  'Companies we buy from. A product can have several; product_suppliers says which, and which one is preferred.';
COMMENT ON COLUMN suppliers.lead_time_days IS
  'Working days from purchase order to delivery, as agreed in the contract. The same field on product_suppliers overrides it per product.';
COMMENT ON COLUMN suppliers.rating IS
  'Internal supplier score from 1 to 5, reviewed each quarter. Not a customer-facing rating.';

COMMENT ON TABLE promotions IS
  'Marketing campaigns. A promotion reaches an order through order_promotions, or through a coupon the customer typed in — the two paths overlap, so counting both double-counts.';
COMMENT ON COLUMN promotions.discount_pct IS
  'Headline percentage off for a percent-type promotion. Ignored for bogo and fixed types, where the discount is worked out per order.';

-- ══════════════════════════════════════════════════════════════════════════
--  BRIDGES AND THE ROLLUP
-- ══════════════════════════════════════════════════════════════════════════

COMMENT ON TABLE order_promotions IS
  'Which campaigns applied to which order, with the money each one took off. An order can carry more than one, so summing across it fans out unless you aggregate first.';
COMMENT ON COLUMN order_promotions.discount_amount IS
  'The part of the order''s discount_total attributable to this promotion, in the order''s currency.';

COMMENT ON TABLE product_suppliers IS
  'Which suppliers can source which product, and at what cost. Most products have two, so joining it to order lines multiplies revenue unless the sum is over distinct orders.';

COMMENT ON TABLE product_tags IS
  'Merchandising labels on a product — bestseller, clearance, seasonal. A product carries about four, so a per-tag total counts the same sale once per tag it wears.';

COMMENT ON TABLE employee_teams IS
  'Which teams a rep belongs to, with the date they joined. Some reps sit in two, so revenue attributed by team will not add up to company revenue.';

COMMENT ON TABLE sales_daily_rollup IS
  'Pre-aggregated revenue per region per day, rebuilt nightly for the TRAILING 90 DAYS ONLY. Convenient and dangerous: any question reaching further back must be answered from orders and order_items instead, or the total is silently short.';
COMMENT ON COLUMN sales_daily_rollup.gross_revenue IS
  'Sum of order_items.line_total for that region and day. Gross: refunds are not netted off, and refunds_total is not populated by the current job.';
