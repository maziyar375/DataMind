"""The demo's dashboards: every tile a statement `build.py` runs for real.

A tile here is what the tile editor writes — a type, a statement, a chart or
table configuration and a place on a 12-column grid — and `build.py` records
what the dashboard service would answer for it: the guard's rewrite, the rows,
and the chart or big number the backend's own `_chart` / `_kpi` plan from them.
So a board in the demo is the product drawing real results, not a picture of
one.

Two boards, one per connection, because a board is where the two databases
look least alike: the Sales warehouse is a live business with a calendar, and
Sakila is a rental history that ends in February 2006, so its tiles name their
months instead of counting back from today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Cancelled and returned orders are not revenue. Stated once so every tile
#: that says "revenue" means the same thing.
BOOKED = "o.status IN ('completed', 'shipped')"

#: Every window ends at the start of the current month: a "last 12 months" that
#: included this one would compare a stub against full months.
LAST_12 = (
    "o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'\n"
    "  AND o.order_date < date_trunc('month', CURRENT_DATE)"
)


@dataclass
class Tile:
    title: str
    tile_type: str                       # CHART | TABLE | METRIC | TEXT
    x: int
    y: int
    w: int
    h: int
    sql: str = ""
    #: TEXT tiles draw this as prose; the others keep the question they were asked with.
    question: str = ""
    chart: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    #: How the SQL was written, as the tile row records it.
    origin: str = "GENERATED"


@dataclass
class Board:
    id: str
    connection: str                      # 'sales' | 'sakila'
    name: str
    description: str
    #: Whose board it is. The demo person's own, or a colleague's shared with them.
    owner: str
    #: The privileges the demo person holds on it.
    privileges: list[str]
    refresh_seconds: int
    days_old: int
    tiles: list[Tile] = field(default_factory=list)


def axis(name: str, kind: str, label: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"field": name, "type": kind}
    if label:
        out["label"] = label
    out.update(extra)
    return out


def measure(name: str, label: str | None = None) -> dict[str, Any]:
    return axis(name, "quantitative", label, aggregation="none")


def col(name: str, label: str | None = None, fmt: str = "auto", align: str = "auto") -> dict[str, Any]:
    return {"name": name, "label": label, "format": fmt, "align": align}


OWNER = ["describe", "select", "modify", "delete", "manage"]
VIEWER = ["describe", "select"]


# ── Sales warehouse ────────────────────────────────────────────────────────
# The same page as the product's own `scripts/seed_demo_dashboard.py` — a
# headline strip, then sections a line of prose introduces, and every chart
# family the platform draws — over the demo warehouse, whose data has a shape.
COMMERCIAL = Board(
    id="commercial-overview",
    connection="sales",
    name="Commercial overview",
    description="Revenue, mix, margin and service.",
    owner="Mazbar Azami",
    privileges=OWNER,
    refresh_seconds=900,
    days_old=34,
    tiles=[
        # ── the headline strip: a month and a measure, so the KPI has a
        # latest value, its move against the month before and a sparkline.
        # Three wide, not four narrow: a tile header carries its database and
        # refresh chips, and at a quarter of a laptop screen they leave the
        # title a few letters.
        Tile("Revenue", "METRIC", 0, 0, 4, 3, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS "revenue this month"
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1""", question="Monthly revenue over the last 12 months"),
        Tile("Orders", "METRIC", 4, 0, 4, 3, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       COUNT(*) AS "orders this month"
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1""", question="Orders per month over the last 12 months"),
        Tile("Gross margin", "METRIC", 8, 0, 4, 3, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(100 * SUM(oi.line_total - oi.quantity * p.cost) / SUM(oi.line_total), 1) AS "gross margin %"
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1""", question="Gross margin per month: revenue less the cost of what was sold"),

        # ── revenue over time, and where it comes from
        Tile("Revenue by month", "CHART", 0, 3, 8, 8, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '23 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""", question="Revenue by month since the warehouse's first full month",
             chart={"chart_type": "area", "x_axis": axis("month", "temporal", "Month"),
                    "y_axis": measure("revenue", "Revenue")}),
        Tile("Channel mix", "CHART", 8, 3, 4, 8, sql=f"""
SELECT o.channel,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY o.channel
ORDER BY revenue DESC""", question="Revenue by sales channel over the last 12 months",
             chart={"chart_type": "pie", "x_axis": axis("channel", "nominal"), "y_axis": measure("revenue")}),
        Tile("Top brands by revenue", "CHART", 0, 11, 6, 7, sql=f"""
SELECT b.name AS brand,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.brands AS b ON b.id = p.brand_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY b.name
ORDER BY revenue DESC""", question="Which brands brought in the most revenue over the last 12 months?",
             chart={"chart_type": "bar", "orientation": "horizontal", "x_axis": axis("brand", "nominal", "Brand"),
                    "y_axis": measure("revenue", "Revenue")}),
        Tile("Revenue by region and segment", "CHART", 6, 11, 6, 7, sql=f"""
SELECT r.name AS region,
       c.segment,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY r.name, c.segment
ORDER BY revenue DESC""", question="Revenue by customer region and segment over the last 12 months",
             chart={"chart_type": "bar", "stack": "stacked",
                    "x_axis": axis("region", "nominal", "Region"), "y_axis": measure("revenue", "Revenue"),
                    "series": axis("segment", "nominal")}),

        # ── mix, margin and price
        Tile("Mix, margin and price", "TEXT", 0, 18, 12, 2, question=(
            "Where the revenue comes from and what it earns: each segment month by month, each category "
            "month by month, what a product sells for against how many sell, and how much of a sale is "
            "margin. Revenue means completed and shipped orders, never cancelled or returned.")),
        Tile("Revenue by segment", "CHART", 0, 20, 6, 7, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       c.segment,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1, 2
ORDER BY 1, 2""", question="Monthly revenue by customer segment",
             chart={"chart_type": "area", "stack": "stacked", "x_axis": axis("month", "temporal", "Month"),
                    "y_axis": measure("revenue", "Revenue"), "series": axis("segment", "nominal")}),
        # Month as text: a heatmap's axes are categories, and 'YYYY-MM' sorts
        # the way a calendar does. The June cell is Meridian's fleet refresh.
        Tile("Revenue by category and month", "CHART", 6, 20, 6, 7, sql=f"""
SELECT to_char(o.order_date, 'YYYY-MM') AS month,
       cat.name AS category,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1, 2
ORDER BY 1, 2""", question="Revenue by product category, month by month",
             chart={"chart_type": "heatmap", "x_axis": axis("month", "nominal", "Month"),
                    "y_axis": axis("category", "nominal", "Category"), "color": measure("revenue", "Revenue")}),
        # A combo draws bars, and bars on a real date scale are hairlines, so
        # the month is text here too.
        Tile("Revenue against average order", "CHART", 0, 27, 6, 7, sql=f"""
SELECT to_char(o.order_date, 'YYYY-MM') AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue,
       ROUND(AVG(o.total_amount), 2) AS avg_order
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1""", question="Monthly revenue against the average order value",
             chart={"chart_type": "combo", "x_axis": axis("month", "nominal", "Month"),
                    "y_axis": measure("revenue", "Revenue"), "y2_axis": measure("avg_order", "Avg order")}),
        Tile("Price against volume", "CHART", 6, 27, 6, 7, sql=f"""
SELECT p.name AS product,
       cat.name AS category,
       p.price,
       SUM(oi.quantity) AS units,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY p.name, cat.name, p.price
ORDER BY revenue DESC""", question="Each product's price against the units it sold over the last 12 months",
             chart={"chart_type": "scatter", "x_axis": axis("price", "quantitative", "Unit price"),
                    "y_axis": axis("units", "quantitative", "Units sold"), "series": axis("category", "nominal"),
                    "size": axis("revenue", "quantitative")}),
        Tile("Gross margin by category", "CHART", 0, 34, 6, 7, sql=f"""
SELECT cat.name AS category,
       ROUND(100 * SUM(oi.line_total - oi.quantity * p.cost) / SUM(oi.line_total), 1) AS margin_pct
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY cat.name
ORDER BY margin_pct DESC""", question="Gross margin by product category over the last 12 months",
             chart={"chart_type": "bar", "x_axis": axis("category", "nominal", "Category"),
                    "y_axis": measure("margin_pct", "Gross margin %")}),
        # One month, under 3,000: a histogram bins the rows it is given, so a
        # year of orders would be cut at the row limit, and the handful of
        # fleet-sized orders would squeeze everyone else into the first bin.
        # The channel is not drawn, but a one-column result is never charted.
        Tile("Order size last month", "CHART", 6, 34, 6, 7, sql=f"""
SELECT o.channel,
       o.total_amount AS "order value"
FROM public.orders AS o
WHERE {BOOKED}
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
  AND o.total_amount < 3000""", question="How were last month's orders spread by value, up to 3,000?",
             chart={"chart_type": "histogram", "x_axis": axis("order value", "quantitative", "Order value")}),

        # ── customers and service
        Tile("Customers and service", "TEXT", 0, 41, 12, 2, question=(
            "Who is arriving and what happens after the order: new customers each month, the share of "
            "orders sent back and why, how long a delivery takes door to door, and what the support queue "
            "looked like while it did.")),
        Tile("New customers", "METRIC", 0, 43, 4, 3, sql="""
SELECT date_trunc('month', c.signed_up_at)::date AS month,
       COUNT(*) AS "signed up this month"
FROM public.customers AS c
WHERE NOT c.is_deleted
  AND c.signed_up_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND c.signed_up_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""", question="New customers per month"),
        Tile("Return rate", "METRIC", 4, 43, 4, 3, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(100.0 * COUNT(*) FILTER (WHERE o.status = 'returned') / COUNT(*), 2) AS "returned %"
FROM public.orders AS o
WHERE {LAST_12}
GROUP BY 1
ORDER BY 1""", question="What share of each month's orders was returned?"),
        Tile("Days to deliver", "METRIC", 8, 43, 4, 3, sql="""
SELECT date_trunc('month', s.shipped_at)::date AS month,
       ROUND(AVG(s.delivered_at::date - s.shipped_at::date), 2) AS "days in transit"
FROM public.shipments AS s
WHERE s.delivered_at IS NOT NULL
  AND s.shipped_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND s.shipped_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""", question="Average days from shipping to delivery, per month"),
        # Why things come back — a split that genuinely varies, where the
        # average review rating is ~4.1 in every category and draws a flat wall.
        Tile("Returns by reason", "CHART", 0, 46, 6, 7, sql="""
SELECT rt.reason,
       COUNT(*) AS returns
FROM public.returns AS rt
WHERE rt.returned_at >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY rt.reason
ORDER BY returns DESC""", origin="GENERATED_EDITED", question="Why were items returned over the last 12 months?",
             chart={"chart_type": "bar", "orientation": "horizontal", "x_axis": axis("reason", "nominal", "Reason"),
                    "y_axis": measure("returns", "Returns")}),
        # A line on a real date: bars on a text month would be ranked by
        # their count, and bars on a date scale are hairlines.
        Tile("Tickets opened by month", "CHART", 6, 46, 6, 7, sql="""
SELECT date_trunc('month', t.opened_at)::date AS month,
       t.priority,
       COUNT(*) AS tickets
FROM public.support_tickets AS t
WHERE t.opened_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND t.opened_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1, 2
ORDER BY 1, 2""", question="Support tickets opened each month, by priority",
             chart={"chart_type": "line", "x_axis": axis("month", "temporal", "Month"),
                    "y_axis": measure("tickets", "Tickets"), "series": axis("priority", "nominal")}),

        # ── the detail
        Tile("The detail behind the numbers", "TEXT", 0, 53, 12, 2, question=(
            "The rows the charts summarise, each sorted on the column that decides it: the largest "
            "accounts, this year's best sellers, the latest orders, stock below its reorder level, the "
            "past year's campaigns and the carriers that deliver it all.")),
        Tile("Largest customers", "TABLE", 0, 55, 6, 8, sql=f"""
SELECT c.name AS customer,
       t.name AS tier,
       COUNT(*) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
LEFT JOIN public.loyalty_tiers AS t ON t.id = c.loyalty_tier_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY c.name, t.name
ORDER BY revenue DESC
LIMIT 12""", origin="GENERATED_EDITED", question="Our largest customers over the last 12 months",
             table={"columns": [col("customer", "Customer"), col("tier", "Loyalty tier"),
                                col("orders", "Orders", "integer", "right"),
                                col("revenue", "Revenue", "decimal", "right")],
                    "sort_column": "revenue", "sort_direction": "desc"}),
        Tile("Top products this year", "TABLE", 6, 55, 6, 8, sql=f"""
SELECT p.name AS product,
       cat.name AS category,
       SUM(oi.quantity) AS units,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND o.order_date >= date_trunc('year', CURRENT_DATE)
GROUP BY p.name, cat.name
ORDER BY revenue DESC
LIMIT 12""", question="Top products by revenue this year",
             table={"columns": [col("product", "Product"), col("category", "Category"),
                                col("units", "Units", "integer", "right"),
                                col("revenue", "Revenue", "decimal", "right")],
                    "sort_column": "revenue", "sort_direction": "desc"}),
        Tile("Latest orders", "TABLE", 0, 63, 6, 8, sql="""
SELECT o.order_date AS placed,
       c.name AS customer,
       o.status,
       o.total_amount AS total
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
ORDER BY o.placed_at DESC, o.id DESC
LIMIT 12""", question="The latest orders",
             table={"columns": [col("placed", "Placed"), col("customer", "Customer"),
                                col("status", "Status"), col("total", "Total", "decimal", "right")]}),
        Tile("Below reorder level", "TABLE", 6, 63, 6, 8, sql="""
SELECT p.name AS product,
       w.name AS warehouse,
       i.quantity AS on_hand,
       i.reorder_level - i.quantity AS shortfall
FROM public.inventory AS i
JOIN public.products AS p ON p.id = i.product_id
JOIN public.warehouses AS w ON w.id = i.warehouse_id
WHERE i.quantity < i.reorder_level
  AND p.active
ORDER BY shortfall DESC, p.name
LIMIT 12""", question="Which products are below their reorder level, and by how much?",
             table={"columns": [col("product", "Product"), col("warehouse", "Warehouse"),
                                col("on_hand", "Stock", "integer", "right"),
                                col("shortfall", "Short", "integer", "right")],
                    "sort_column": "shortfall", "sort_direction": "desc"}),
        Tile("Campaign results", "TABLE", 0, 71, 6, 5, sql=f"""
SELECT pr.name AS campaign,
       ROUND(pr.discount_pct / 100.0, 4) AS discount,
       COUNT(*) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.promotions AS pr
JOIN public.order_promotions AS op ON op.promotion_id = pr.id
JOIN public.orders AS o ON o.id = op.order_id
WHERE {BOOKED}
  AND pr.ends_on >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY pr.name, pr.discount_pct, pr.starts_on
ORDER BY pr.starts_on DESC""", question="How did the past year's campaigns do?",
             table={"columns": [col("campaign", "Campaign"), col("discount", "Discount", "percent", "right"),
                                col("orders", "Orders", "integer", "right"),
                                col("revenue", "Revenue", "decimal", "right")]}),
        Tile("Carrier performance", "TABLE", 6, 71, 6, 5, sql="""
SELECT ca.name AS carrier,
       COUNT(*) AS shipments,
       ROUND(AVG(s.delivered_at::date - s.shipped_at::date), 2) AS avg_days,
       ROUND(1.0 * COUNT(*) FILTER (WHERE s.delivered_at IS NOT NULL) / COUNT(*), 4) AS delivered,
       ROUND(SUM(s.cost), 2) AS cost
FROM public.shipments AS s
JOIN public.carriers AS ca ON ca.id = s.carrier_id
WHERE s.shipped_at >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY ca.name
ORDER BY shipments DESC""", question="Shipments, speed and cost by carrier over the last 12 months",
             table={"columns": [col("carrier", "Carrier"), col("shipments", "Shipments", "integer", "right"),
                                col("avg_days", "Avg days", "decimal", "right"),
                                col("delivered", "Delivered", "percent", "right"),
                                col("cost", "Shipping cost", "decimal", "right")],
                    "sort_column": "shipments", "sort_direction": "desc"}),
    ],
)


# ── Sakila DVD rental ──────────────────────────────────────────────────────
# Sakila's rentals run from May 2005 to February 2006, so these tiles name
# their period rather than counting back from today — which would find nothing.
RENTALS = Board(
    id="rental-operations",
    connection="sakila",
    name="Rental operations",
    description="Both stores, May 2005 – February 2006.",
    owner="Priya Nair",
    privileges=VIEWER,
    refresh_seconds=3600,
    days_old=21,
    tiles=[
        # Totals rather than a month against the one before: the history ends
        # on a stub February of 182 rentals, and "−98%" would be the headline.
        Tile("Rentals", "METRIC", 0, 0, 4, 3, sql="""
SELECT COUNT(*) AS "rentals, all time"
FROM sakila.rental AS r""", question="How many rentals are there in total?"),
        Tile("Rental revenue", "METRIC", 4, 0, 4, 3, sql="""
SELECT SUM(p.amount) AS "revenue, all time"
FROM sakila.payment AS p""", question="Total rental revenue"),
        Tile("Active customers", "METRIC", 8, 0, 4, 3, sql="""
SELECT COUNT(*) AS "active customers"
FROM sakila.customer AS c
WHERE c.active = 1""", question="How many active customers are there?"),
        Tile("Revenue by film category", "CHART", 0, 3, 8, 7, sql="""
SELECT c.name AS category,
       SUM(p.amount) AS revenue
FROM sakila.payment AS p
JOIN sakila.rental AS r ON r.rental_id = p.rental_id
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
JOIN sakila.film_category AS fc ON fc.film_id = i.film_id
JOIN sakila.category AS c ON c.category_id = fc.category_id
GROUP BY c.name
ORDER BY revenue DESC""", question="Rental revenue by film category",
             chart={"chart_type": "bar", "x_axis": axis("category", "nominal", "Category"),
                    "y_axis": measure("revenue", "Revenue")}),
        Tile("Rentals by store", "CHART", 8, 3, 4, 7, sql="""
SELECT ci.city AS store,
       COUNT(*) AS rentals
FROM sakila.rental AS r
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
JOIN sakila.store AS s ON s.store_id = i.store_id
JOIN sakila.address AS a ON a.address_id = s.address_id
JOIN sakila.city AS ci ON ci.city_id = a.city_id
GROUP BY store
ORDER BY rentals DESC""", question="Rentals by store",
             chart={"chart_type": "pie", "x_axis": axis("store", "nominal"), "y_axis": measure("rentals")}),
        # A line over a real date, not bars over text: a bar axis of text is
        # ranked by its measure, which would put July before May, and a line
        # needs a date to run along. The summer only — the history's last month
        # is a February stub of 182 rentals after a four-month gap.
        Tile("Summer 2005 rentals, by store", "CHART", 0, 10, 7, 7, sql="""
SELECT DATE(CONCAT(SUBSTRING(r.rental_date, 1, 7), '-01')) AS month,
       CONCAT('Store ', i.store_id) AS store,
       COUNT(*) AS rentals
FROM sakila.rental AS r
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
WHERE r.rental_date < '2005-09-01'
GROUP BY month, store
ORDER BY month, store""", question="Rentals per month in summer 2005, for each store",
             chart={"chart_type": "line", "x_axis": axis("month", "temporal", "Month"),
                    "y_axis": measure("rentals", "Rentals"), "series": axis("store", "nominal")}),
        Tile("Customers by country", "CHART", 7, 10, 5, 7, sql="""
SELECT co.country,
       COUNT(*) AS customers
FROM sakila.customer AS cu
JOIN sakila.address AS a ON a.address_id = cu.address_id
JOIN sakila.city AS ci ON ci.city_id = a.city_id
JOIN sakila.country AS co ON co.country_id = ci.country_id
GROUP BY co.country
ORDER BY customers DESC, co.country
LIMIT 10""", question="Which ten countries have the most customers?",
             chart={"chart_type": "bar", "orientation": "horizontal", "x_axis": axis("country", "nominal", "Country"),
                    "y_axis": measure("customers", "Customers")}),
        Tile("Most rented films", "TABLE", 0, 17, 7, 7, sql="""
SELECT f.title,
       c.name AS category,
       f.rating,
       COUNT(*) AS rentals
FROM sakila.rental AS r
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
JOIN sakila.film AS f ON f.film_id = i.film_id
JOIN sakila.film_category AS fc ON fc.film_id = f.film_id
JOIN sakila.category AS c ON c.category_id = fc.category_id
GROUP BY f.film_id, f.title, c.name, f.rating
ORDER BY rentals DESC, f.title
LIMIT 10""", question="The ten most rented films",
             table={"columns": [col("title", "Film"), col("category", "Category"), col("rating", "Rating"),
                                col("rentals", "Rentals", "integer", "right")]}),
        Tile("Best customers", "TABLE", 7, 17, 5, 7, sql="""
SELECT CONCAT(cu.first_name, ' ', cu.last_name) AS customer,
       COUNT(*) AS rentals,
       SUM(p.amount) AS paid
FROM sakila.payment AS p
JOIN sakila.customer AS cu ON cu.customer_id = p.customer_id
GROUP BY cu.customer_id, cu.first_name, cu.last_name
ORDER BY paid DESC
LIMIT 8""", question="Our best customers by amount paid",
             table={"columns": [col("customer", "Customer"), col("rentals", "Rentals", "integer", "right"),
                                col("paid", "Paid", "decimal", "right")]}),
    ],
)

BOARDS = [COMMERCIAL, RENTALS]
