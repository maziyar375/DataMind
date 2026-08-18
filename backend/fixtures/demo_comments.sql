-- Catalog comments for the `aurora` DEMO fixture.
--
-- Loaded on top of demo_seed.sql as a second init script, exactly like
-- sales_comments.sql is for the `sales` fixture. Unlike that pair, there is no
-- A/B here: the demo has no uncommented arm, because the whole point of the
-- demo fixture is that a fast, cheap model gets the SQL right first time, and
-- these sentences are the cheapest accuracy available. `connectors/comments.py`
-- captures them and the run prompt carries them into generation.
--
-- WRITTEN TO THE RENDER CAP, NOT THE STORED CAP
-- Stored caps are 400 (table) / 240 (column), but the run prompt clips column
-- comments at ~120 chars on a word boundary (docs/catalog-metadata-plan.md
-- §4.4). So every column comment below front-loads the fact that changes the
-- SQL — the filter to apply, the unit, what NULL means — and leaves colour for
-- the tail, where losing it costs nothing.
--
-- Nothing here restates a column name: `comments.py::is_noise` would drop it,
-- and it would cost tokens on every single question.

COMMENT ON DATABASE aurora IS
  'Aurora Coffee: 18 cafes across 5 US regions. Transaction-level sales, Aug 2024 - Jul 2026.';

-- ── facts ────────────────────────────────────────────────────────────────
COMMENT ON TABLE orders IS
  'One row per completed transaction; the source of truth for revenue. Grain is a single customer checkout at one store. Exclude is_refunded = true from revenue questions.';
COMMENT ON COLUMN orders.ordered_at IS
  'Local store time of the sale. Trading hours are 06:00-20:00; use this for hour-of-day and weekday questions.';
COMMENT ON COLUMN orders.customer_id IS
  'NULL for a walk-in who did not identify; about a third of orders. INNER JOIN to customers silently drops them.';
COMMENT ON COLUMN orders.gross_amount IS
  'Line-item subtotal before loyalty discount and before tip. Equals the sum of the order''s order_items.line_total.';
COMMENT ON COLUMN orders.discount_amount IS
  'Loyalty discount given, = gross_amount x the customer''s tier rate. Zero for walk-ins and Bronze.';
COMMENT ON COLUMN orders.tip_amount IS
  'Tip; always zero on Delivery Partner orders, which cannot be tipped in-app.';
COMMENT ON COLUMN orders.order_total IS
  'What the customer actually paid: gross_amount - discount_amount + tip_amount. The revenue column for most questions.';
COMMENT ON COLUMN orders.prep_seconds IS
  'Seconds from order placed to handed over. Rises sharply during the 07-09 and 12-13 rushes.';
COMMENT ON COLUMN orders.satisfaction_score IS
  'Post-order rating, 1-10, recorded on every order. Falls as prep_seconds rises.';
COMMENT ON COLUMN orders.is_refunded IS
  'true = the sale was reversed (~1.2%). Filter these out of revenue; daily_store_metrics already excludes them.';

COMMENT ON TABLE order_items IS
  'The lines of an order: one row per product on a receipt, 1-4 lines per order. Join to products for category and margin questions.';
COMMENT ON COLUMN order_items.unit_price IS
  'Price charged per unit at the time of sale, copied from products.unit_price.';
COMMENT ON COLUMN order_items.line_total IS
  'unit_price x quantity, before any order-level loyalty discount.';

COMMENT ON TABLE daily_store_metrics IS
  'Pre-aggregated store-day summary, derived from orders with refunds already excluded. Use it for store trading questions; it always agrees with orders, so either path is correct.';
COMMENT ON COLUMN daily_store_metrics.net_revenue IS
  'Sum of order_total for the store-day, refunds excluded.';
COMMENT ON COLUMN daily_store_metrics.unique_customers IS
  'Distinct identified customers; walk-ins are not counted, so this is below transaction_count.';
COMMENT ON COLUMN daily_store_metrics.labor_hours IS
  'Staffed hours rostered that day. Divide net_revenue by this for sales per labour hour.';
COMMENT ON COLUMN daily_store_metrics.waste_kg IS
  'Food and milk discarded at close, in kilograms.';

-- ── dimensions ───────────────────────────────────────────────────────────
COMMENT ON TABLE stores IS
  'The 18 cafes. format_code splits them into 4 Flagship, 11 Standard and 3 Kiosk sites, which is the main driver of store volume.';
COMMENT ON COLUMN stores.floor_area_sqm IS
  'Trading floor in square metres. Divide revenue by this for sales density.';
COMMENT ON COLUMN stores.opened_on IS
  'Trading start date. Every store was already open before the data window begins, so there are no partial histories.';

COMMENT ON TABLE products IS
  'The 24-item menu, priced and costed. unit_price - unit_cost is the per-unit margin.';
COMMENT ON COLUMN products.unit_cost IS
  'Cost of goods per unit. Retail Beans carry the highest price but also the highest cost.';
COMMENT ON COLUMN products.is_seasonal IS
  'true = limited-run item sold only part of the year, so its totals are not comparable to a core line.';

COMMENT ON TABLE product_categories IS
  'The six menu groups. Cold Brew & Iced is strongly summer-weighted; Retail Beans spike every December.';
COMMENT ON TABLE channels IS
  'How the order was placed. Delivery Partner charges an 18% commission, so its gross revenue overstates what Aurora keeps.';
COMMENT ON COLUMN channels.commission_rate IS
  'Fraction of order value taken by the channel. Multiply revenue by (1 - commission_rate) for net-of-commission questions.';

COMMENT ON TABLE customers IS
  'Loyalty members only; walk-in trade has no row here. signup_date is always on or before the customer''s first order.';
COMMENT ON COLUMN customers.signup_date IS
  'Loyalty enrolment date. Use it for cohort and new-customer-by-month questions.';
COMMENT ON COLUMN customers.region_id IS
  'Home region, always the region of the stores the customer actually shops at.';

COMMENT ON TABLE loyalty_tiers IS
  'The four-step loyalty rate card. discount_rate is exactly what orders.discount_amount applies.';
COMMENT ON TABLE regions IS 'The five US trading regions the 18 stores are grouped into.';
COMMENT ON TABLE employees IS
  'Baristas, shift leads and store managers, each assigned to one home store. Every order records who served it.';
COMMENT ON COLUMN employees.training_hours IS
  'Cumulative barista training completed, in hours. Compare against prep_seconds or satisfaction_score.';

COMMENT ON TABLE marketing_campaigns IS
  'Ten paid campaigns with spend and attributed revenue. attributed_revenue is a marketing model, not a sum of orders, so it will not reconcile against the orders table.';
COMMENT ON COLUMN marketing_campaigns.attributed_revenue IS
  'Revenue the attribution model credits to the campaign. Divide by spend for ROAS.';
COMMENT ON COLUMN marketing_campaigns.spend IS
  'Media spend in USD, roughly a tenth of attributed_revenue, so plot the two on separate axes.';

COMMENT ON TABLE store_formats IS 'Lookup for the three cafe sizes: Flagship, Standard and Kiosk.';
