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
COMMERCIAL = Board(
    id="commercial-overview",
    connection="sales",
    name="Commercial overview",
    description="Revenue, customers and fulfilment.",
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
        Tile("Average order value", "METRIC", 8, 0, 4, 3, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(AVG(o.total_amount), 2) AS "average order"
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1""", question="Average order value per month"),
        Tile("New customers", "METRIC", 7, 27, 5, 3, sql="""
SELECT date_trunc('month', c.signed_up_at)::date AS month,
       COUNT(*) AS "signed up this month"
FROM public.customers AS c
WHERE NOT c.is_deleted
  AND c.signed_up_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND c.signed_up_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""", question="New customers per month"),

        # ── revenue over time, and where it comes from
        Tile("Revenue by month", "CHART", 0, 3, 8, 7, sql=f"""
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
        Tile("Channel mix", "CHART", 8, 3, 4, 7, sql=f"""
SELECT o.channel,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY o.channel
ORDER BY revenue DESC""", question="Revenue by sales channel over the last 12 months",
             chart={"chart_type": "pie", "x_axis": axis("channel", "nominal"), "y_axis": measure("revenue")}),
        Tile("Revenue by region", "CHART", 0, 10, 6, 7, sql=f"""
SELECT r.name AS region,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY r.name
ORDER BY revenue DESC""", question="Revenue by customer region over the last 12 months",
             chart={"chart_type": "bar", "orientation": "horizontal",
                    "x_axis": axis("region", "nominal", "Region"), "y_axis": measure("revenue", "Revenue")}),
        Tile("Category mix by segment", "CHART", 6, 10, 6, 7, sql=f"""
SELECT cat.name AS category,
       c.segment,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
JOIN public.customers AS c ON c.id = o.customer_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY cat.name, c.segment
ORDER BY revenue DESC""", question="Revenue by product category and customer segment",
             chart={"chart_type": "bar", "stack": "stacked",
                    "x_axis": axis("category", "nominal", "Category"), "y_axis": measure("revenue", "Revenue"),
                    "series": axis("segment", "nominal")}),

        # ── products and customers
        Tile("Products and customers", "TEXT", 0, 17, 12, 2, question=(
            "What sells, and who buys it: products for this year, customers and returns for the last "
            "twelve months. An order counts once it is completed or shipped, never while cancelled or returned.")),
        Tile("Top products this year", "TABLE", 0, 19, 7, 8, sql=f"""
SELECT p.name AS product,
       cat.name AS category,
       SUM(oi.quantity) AS units,
       ROUND(SUM(oi.line_total), 2) AS revenue,
       ROUND(AVG(oi.unit_price), 2) AS avg_price
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND o.order_date >= date_trunc('year', CURRENT_DATE)
GROUP BY p.name, cat.name
ORDER BY revenue DESC
LIMIT 10""", question="Top 10 products by revenue this year",
             table={"columns": [col("product", "Product"), col("category", "Category"),
                                col("units", "Units", "integer", "right"),
                                col("revenue", "Revenue", "decimal", "right"),
                                col("avg_price", "Avg price", "decimal", "right")],
                    "sort_column": "revenue", "sort_direction": "desc"}),
        Tile("Revenue by segment", "CHART", 7, 19, 5, 8, sql=f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       c.segment,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1, 2
ORDER BY 1, 2""", question="Monthly revenue by customer segment",
             chart={"chart_type": "line", "x_axis": axis("month", "temporal", "Month"),
                    "y_axis": measure("revenue", "Revenue"), "series": axis("segment", "nominal")}),
        Tile("Largest customers", "TABLE", 0, 27, 7, 7, sql=f"""
SELECT c.name AS customer,
       c.segment,
       r.name AS region,
       COUNT(*) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY c.name, c.segment, r.name
ORDER BY revenue DESC
LIMIT 8""", origin="GENERATED_EDITED", question="Our eight largest customers over the last 12 months",
             table={"columns": [col("customer", "Customer"), col("segment", "Segment"), col("region", "Region"),
                                col("orders", "Orders", "integer", "right"),
                                col("revenue", "Revenue", "decimal", "right")],
                    "sort_column": "revenue", "sort_direction": "desc"}),
        # Why things come back — a split that genuinely varies, where the
        # average review rating is ~4.1 in every category and draws a flat wall.
        Tile("Returns by reason", "CHART", 7, 30, 5, 4, sql="""
SELECT rt.reason,
       COUNT(*) AS returns
FROM public.returns AS rt
WHERE rt.returned_at >= CURRENT_DATE - INTERVAL '12 months'
GROUP BY rt.reason
ORDER BY returns DESC""", origin="GENERATED_EDITED", question="Why were items returned over the last 12 months?",
             chart={"chart_type": "bar", "orientation": "horizontal", "x_axis": axis("reason", "nominal", "Reason"),
                    "y_axis": measure("returns", "Returns")}),

        # ── fulfilment
        Tile("Fulfilment", "TEXT", 0, 34, 12, 2, question=(
            "How orders reach customers: how many deliveries each carrier made over the last six months and "
            "how long they took door to door, and which stock is at or below the level it is reordered at.")),
        # Every carrier averages about three and a half days, so days alone
        # would be a flat wall of bars; volume against speed is the picture.
        Tile("Carriers: volume and speed", "CHART", 0, 36, 5, 7, sql="""
SELECT ca.name AS carrier,
       COUNT(*) AS deliveries,
       ROUND(AVG(EXTRACT(EPOCH FROM (s.delivered_at - s.shipped_at)) / 86400)::numeric, 1) AS avg_days
FROM public.shipments AS s
JOIN public.carriers AS ca ON ca.id = s.carrier_id
WHERE s.delivered_at IS NOT NULL
  AND s.shipped_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ca.name
ORDER BY deliveries DESC""", question="Deliveries and average days from shipping to delivery, by carrier",
             chart={"chart_type": "combo", "x_axis": axis("carrier", "nominal", "Carrier"),
                    "y_axis": measure("deliveries", "Deliveries"), "y2_axis": measure("avg_days", "Average days")}),
        Tile("Stock at or below reorder level", "TABLE", 5, 36, 7, 7, sql="""
SELECT p.name AS product,
       w.name AS warehouse,
       i.quantity AS on_hand,
       i.reorder_level
FROM public.inventory AS i
JOIN public.products AS p ON p.id = i.product_id
JOIN public.warehouses AS w ON w.id = i.warehouse_id
WHERE i.quantity <= i.reorder_level
  AND p.active
ORDER BY i.quantity - i.reorder_level, p.name
LIMIT 12""", question="Which products are at or below their reorder level?",
             table={"columns": [col("product", "Product"), col("warehouse", "Warehouse"),
                                col("on_hand", "On hand", "integer", "right"),
                                col("reorder_level", "Reorder at", "integer", "right")]}),
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
