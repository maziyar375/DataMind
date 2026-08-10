#!/usr/bin/env python3
"""Build the demo dashboard — the one you put on a screen.

Twenty-nine tiles over the `sales` fixture: eight KPIs, twelve charts covering
every family the platform can draw (line, bar both ways, stacked bar, area,
pie, heatmap, combo, scatter, histogram), six formatted tables, and three TEXT
tiles that divide the page into sections. Every tile is ordinary — the same
rows the tile editor writes, the same guard on the way in — so what the
presentation shows is the product, not a mock of it.

A note on the fixture, because it shapes half the choices here: `sales_seed.sql`
assigns rows round-robin, so several dimensions come out *exactly* uniform —
every product has the same revenue to the cent, every payment method the same
total. `plan_chart` refuses to draw those ("a chart would show one flat level"),
correctly, so the tiles below group by dimensions that actually vary: brands
rather than products, regions rather than payment methods.

It talks to the running API rather than the database on purpose: the SQL goes
through `sqlguard` exactly as a hand-written tile does, so a query this script
gets away with is a query a user could have typed.

    python scripts/seed_demo_dashboard.py                  # create/replace it
    python scripts/seed_demo_dashboard.py --check          # run every tile too
    python scripts/seed_demo_dashboard.py --keep-existing  # add another copy

Credentials come from ADMIN_EMAIL / ADMIN_PASSWORD (the .env values are the
defaults), the connection from --connection (the first one otherwise).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API = os.environ.get("DATAMIND_API", "http://localhost:8000") + "/api/v1"
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@raymand.com")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "raymand")

DASHBOARD_NAME = "Commercial overview"
DASHBOARD_DESCRIPTION = (
    "Revenue, mix and fulfilment across the sales database — refreshed every "
    "five minutes."
)

# ── the shape of the page ────────────────────────────────────────────────
# 12 columns at 60px rows: a quarter is w=3, a half w=6, two thirds w=8. The
# y values are written out rather than accumulated so the page can be read
# here, top to bottom, in the order it appears on screen.
COLUMNS = 12
ROW_HEIGHT = 60
GAP = 12

# Every window ends at the start of the current month. The fixture's last day
# is today, so a "last 12 months" that included the current one would compare a
# ten-day stub against a full month and report a collapse.
MONTH_WINDOW = (
    "o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'\n"
    "      AND o.order_date < date_trunc('month', CURRENT_DATE)"
)
# Cancelled and returned orders are not revenue. Stated once so every tile that
# says "revenue" means the same thing.
BOOKED = "o.status IN ('completed', 'shipped')"


def sql(text: str) -> str:
    return text.strip()


def metric(
    title: str,
    query: str,
    *,
    x: int,
    y: int,
    w: int = 3,
    h: int = 3,
) -> dict[str, Any]:
    """A big number. Two columns — a month and a measure — so the backend's
    `plan_kpi` has a series to read the latest value, its move and a sparkline
    off, instead of one lonely figure with no context."""
    return {
        "title": title,
        "tile_type": "METRIC",
        "sql": sql(query),
        "grid_x": x,
        "grid_y": y,
        "grid_w": w,
        "grid_h": h,
    }


def chart(
    title: str,
    query: str,
    config: dict[str, Any],
    *,
    x: int,
    y: int,
    w: int,
    h: int,
) -> dict[str, Any]:
    """A chart with its type stated. `chart_config` is a `ChartIntent`: the
    backend still fits it to the result's real shape and may demote it, which
    is why nothing here needs to be defensive about cardinality."""
    return {
        "title": title,
        "tile_type": "CHART",
        "sql": sql(query),
        "chart_config": config,
        "grid_x": x,
        "grid_y": y,
        "grid_w": w,
        "grid_h": h,
    }


def table(
    title: str,
    query: str,
    columns: list[dict[str, Any]],
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    sort: tuple[str, str] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {"columns": columns}
    if sort:
        config["sort_column"], config["sort_direction"] = sort
    return {
        "title": title,
        "tile_type": "TABLE",
        "sql": sql(query),
        "table_config": config,
        "grid_x": x,
        "grid_y": y,
        "grid_w": w,
        "grid_h": h,
    }


def text(title: str, body: str, *, y: int, w: int = COLUMNS, h: int = 2) -> dict[str, Any]:
    """A section divider. A TEXT tile draws its `question` as prose and runs no
    query, so it costs the database nothing."""
    return {
        "title": title,
        "tile_type": "TEXT",
        "question": body,
        "grid_x": 0,
        "grid_y": y,
        "grid_w": w,
        "grid_h": h,
    }


def col(
    name: str,
    *,
    label: str | None = None,
    fmt: str = "auto",
    align: str = "auto",
) -> dict[str, Any]:
    return {"name": name, "label": label, "format": fmt, "align": align}


def tiles() -> list[dict[str, Any]]:
    return [
        # ── the headline strip ───────────────────────────────────────────
        metric(
            "Revenue",
            f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS "revenue"
FROM orders o
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1
ORDER BY 1
""",
            x=0,
            y=0,
        ),
        metric(
            "Orders",
            f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       COUNT(*) AS "orders placed"
FROM orders o
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1
ORDER BY 1
""",
            x=3,
            y=0,
        ),
        metric(
            "Average order value",
            f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(AVG(o.total_amount), 2) AS "average order"
FROM orders o
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1
ORDER BY 1
""",
            x=6,
            y=0,
        ),
        metric(
            "New customers",
            """
SELECT date_trunc('month', c.signed_up_at)::date AS month,
       COUNT(*) AS "new customers"
FROM customers c
WHERE c.is_deleted = FALSE
  AND c.signed_up_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND c.signed_up_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            x=9,
            y=0,
        ),

        # ── revenue over time ────────────────────────────────────────────
        # 23 months, not "everything": the fixture's first day is mid-month, so
        # an unbounded window opens on a half month drawn as a collapse.
        chart(
            "Revenue by month",
            f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM orders o
WHERE {BOOKED}
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '23 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            {
                "chart_type": "line",
                "x_axis": {"field": "month", "type": "temporal", "label": "Month"},
                "y_axis": {
                    "field": "revenue",
                    "type": "quantitative",
                    "label": "Revenue",
                },
            },
            x=0,
            y=3,
            w=8,
            h=8,
        ),
        chart(
            "Revenue by channel",
            f"""
SELECT o.channel AS channel,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM orders o
WHERE {BOOKED}
GROUP BY 1
ORDER BY 2 DESC
""",
            {
                "chart_type": "pie",
                "x_axis": {"field": "channel", "type": "nominal"},
                "y_axis": {"field": "revenue", "type": "quantitative"},
            },
            x=8,
            y=3,
            w=4,
            h=8,
        ),
        # Brands rather than products: the fixture assigns order lines to
        # products round-robin, so every product's total is the same number to
        # the cent and `plan_chart` rightly refuses to draw a flat wall of
        # bars. Brands aggregate several products each, so they actually rank.
        chart(
            "Top brands by revenue",
            f"""
SELECT b.name AS brand,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN products p ON p.id = oi.product_id
JOIN brands b ON b.id = p.brand_id
WHERE {BOOKED}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 12
""",
            {
                "chart_type": "bar",
                "orientation": "horizontal",
                "x_axis": {"field": "brand", "type": "nominal"},
                "y_axis": {
                    "field": "revenue",
                    "type": "quantitative",
                    "label": "Revenue",
                },
            },
            x=0,
            y=11,
            w=6,
            h=6,
        ),
        # Categories on the x axis, not quarters. A stacked bar is a *ranking*
        # here: `_layout` sorts a nominal category axis by the measure, which
        # is right for "which category is biggest" and would scramble a row of
        # quarters into revenue order. Time belongs on the line and area tiles,
        # where the platform keeps the query's own ordering.
        chart(
            "Category mix by channel",
            f"""
SELECT c.name AS category,
       o.channel AS channel,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN products p ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
WHERE {BOOKED}
GROUP BY 1, 2
ORDER BY 3 DESC
""",
            {
                "chart_type": "bar",
                "stack": "stacked",
                "x_axis": {"field": "category", "type": "nominal", "label": "Category"},
                "y_axis": {
                    "field": "revenue",
                    "type": "quantitative",
                    "label": "Revenue",
                },
                "series": {"field": "channel", "type": "nominal"},
            },
            x=6,
            y=11,
            w=6,
            h=6,
        ),

        # ── mix, geography, price ────────────────────────────────────────
        text(
            "Mix, geography and price",
            "Where the revenue comes from, how it is spread across the "
            "regions, and what a unit sells for. Every tile below reads the "
            "same booked-orders definition as the strip at the top: completed "
            "and shipped, never cancelled or returned.",
            y=17,
        ),
        chart(
            "Units sold by category",
            f"""
SELECT date_trunc('month', o.order_date)::date AS month,
       c.name AS category,
       SUM(oi.quantity) AS units
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN products p ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1, 2
ORDER BY 1, 2
""",
            {
                "chart_type": "area",
                "stack": "stacked",
                "x_axis": {"field": "month", "type": "temporal", "label": "Month"},
                "y_axis": {
                    "field": "units",
                    "type": "quantitative",
                    "label": "Units sold",
                },
                "series": {"field": "category", "type": "nominal"},
            },
            x=0,
            y=19,
            w=6,
            h=7,
        ),
        chart(
            "Revenue by region and month",
            f"""
SELECT to_char(o.order_date, 'YYYY-MM') AS month,
       r.name AS region,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM orders o
JOIN customers c ON c.id = o.customer_id
JOIN regions r ON r.id = c.region_id
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1, 2
ORDER BY 1, 2
""",
            {
                "chart_type": "heatmap",
                "x_axis": {"field": "month", "type": "nominal", "label": "Month"},
                "y_axis": {"field": "region", "type": "nominal", "label": "Region"},
                "color": {"field": "revenue", "type": "quantitative"},
            },
            x=6,
            y=19,
            w=6,
            h=7,
        ),
        # A combo draws bars, so the month is text here for the same reason the
        # quarter above is.
        chart(
            "Revenue against average order",
            f"""
SELECT to_char(o.order_date, 'YYYY-MM') AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue,
       ROUND(AVG(o.total_amount), 2) AS avg_order
FROM orders o
WHERE {BOOKED}
  AND {MONTH_WINDOW}
GROUP BY 1
ORDER BY 1
""",
            {
                "chart_type": "combo",
                "x_axis": {"field": "month", "type": "nominal", "label": "Month"},
                "y_axis": {
                    "field": "revenue",
                    "type": "quantitative",
                    "label": "Revenue",
                },
                "y2_axis": {
                    "field": "avg_order",
                    "type": "quantitative",
                    "label": "Avg order",
                },
            },
            x=0,
            y=26,
            w=6,
            h=7,
        ),
        chart(
            "Price against volume",
            f"""
SELECT p.name AS product,
       c.name AS category,
       p.price AS price,
       SUM(oi.quantity) AS units,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN products p ON p.id = oi.product_id
JOIN categories c ON c.id = p.category_id
WHERE {BOOKED}
GROUP BY 1, 2, 3
ORDER BY 5 DESC
""",
            {
                "chart_type": "scatter",
                "x_axis": {"field": "price", "type": "quantitative", "label": "Unit price"},
                "y_axis": {"field": "units", "type": "quantitative", "label": "Units sold"},
                "series": {"field": "category", "type": "nominal"},
                "size": {"field": "revenue", "type": "quantitative"},
            },
            x=6,
            y=26,
            w=6,
            h=7,
        ),
        # Shipping cost, not order value: a histogram needs a column with a
        # real spread (`MIN_HISTOGRAM_LEVELS` is ten distinct values) and the
        # fixture prices its orders from a handful of totals, so binning those
        # gets the intent demoted to a bar chart. The carrier column is not
        # drawn — a histogram bins one measure and counts it — but a one-column
        # result is never charted at all (`query_service._chart` needs two).
        chart(
            "Spread of shipping cost",
            """
SELECT ca.name AS carrier,
       s.cost AS "shipping cost"
FROM shipments s
JOIN carriers ca ON ca.id = s.carrier_id
WHERE s.shipped_at >= CURRENT_DATE - INTERVAL '3 months'
""",
            {
                "chart_type": "histogram",
                "x_axis": {
                    "field": "shipping cost",
                    "type": "quantitative",
                    "label": "Shipping cost",
                },
            },
            x=0,
            y=33,
            w=6,
            h=7,
        ),
        chart(
            "Revenue by region",
            f"""
SELECT r.name AS region,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM orders o
JOIN customers c ON c.id = o.customer_id
JOIN regions r ON r.id = c.region_id
WHERE {BOOKED}
GROUP BY 1
ORDER BY 2 DESC
""",
            {
                "chart_type": "bar",
                "orientation": "vertical",
                "x_axis": {"field": "region", "type": "nominal", "label": "Region"},
                "y_axis": {
                    "field": "revenue",
                    "type": "quantitative",
                    "label": "Revenue",
                },
            },
            x=6,
            y=33,
            w=6,
            h=7,
        ),

        # ── operations ───────────────────────────────────────────────────
        text(
            "Operations and service",
            "What happens after the order is placed: how much of it comes "
            "back, how long it takes to arrive, and what the support queue "
            "looks like while it does.",
            y=40,
        ),
        metric(
            "Return rate",
            """
SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(
         100.0 * COUNT(*) FILTER (WHERE o.status = 'returned') / COUNT(*), 2
       ) AS "returned pct"
FROM orders o
WHERE o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            x=0,
            y=42,
        ),
        metric(
            "Days to deliver",
            """
SELECT date_trunc('month', s.shipped_at)::date AS month,
       ROUND(AVG(s.delivered_at::date - s.shipped_at::date), 2) AS "days in transit"
FROM shipments s
WHERE s.delivered_at IS NOT NULL
  AND s.shipped_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND s.shipped_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            x=3,
            y=42,
        ),
        metric(
            "Tickets opened",
            """
SELECT date_trunc('month', t.opened_at)::date AS month,
       COUNT(*) AS "tickets opened"
FROM support_tickets t
WHERE t.opened_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND t.opened_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            x=6,
            y=42,
        ),
        metric(
            "Refunded",
            """
SELECT date_trunc('month', r.processed_at)::date AS month,
       ROUND(SUM(r.amount), 2) AS "refunded"
FROM refunds r
WHERE r.processed_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1
""",
            x=9,
            y=42,
        ),
        chart(
            "Days in transit by carrier",
            """
SELECT ca.name AS carrier,
       ROUND(AVG(s.delivered_at::date - s.shipped_at::date), 2) AS days_in_transit
FROM shipments s
JOIN carriers ca ON ca.id = s.carrier_id
WHERE s.delivered_at IS NOT NULL
GROUP BY 1
ORDER BY 2
""",
            {
                "chart_type": "bar",
                "orientation": "vertical",
                "x_axis": {"field": "carrier", "type": "nominal", "label": "Carrier"},
                "y_axis": {
                    "field": "days_in_transit",
                    "type": "quantitative",
                    "label": "Average days",
                },
            },
            x=0,
            y=45,
            w=6,
            h=6,
        ),
        # A line, not a stack of bars: this is a time series, and a bar chart
        # on a nominal month would be re-ranked by ticket count (see the
        # category note above) while a bar chart on a real date scale draws
        # hairlines. A line on a temporal axis is neither.
        chart(
            "Tickets opened by month",
            """
SELECT date_trunc('month', t.opened_at)::date AS month,
       t.status AS status,
       COUNT(*) AS tickets
FROM support_tickets t
WHERE t.opened_at < date_trunc('month', CURRENT_DATE)
GROUP BY 1, 2
ORDER BY 1, 2
""",
            {
                "chart_type": "line",
                "x_axis": {"field": "month", "type": "temporal", "label": "Month"},
                "y_axis": {
                    "field": "tickets",
                    "type": "quantitative",
                    "label": "Tickets",
                },
                "series": {"field": "status", "type": "nominal"},
            },
            x=6,
            y=45,
            w=6,
            h=6,
        ),

        # ── the lists ────────────────────────────────────────────────────
        text(
            "The detail behind the numbers",
            "The rows a chart summarises. Each table is sorted on the column "
            "that decides it, and formatted where the raw value is not what a "
            "reader wants to see.",
            y=51,
        ),
        table(
            "Top customers",
            f"""
SELECT c.name AS customer,
       c.segment AS segment,
       t.name AS tier,
       COUNT(DISTINCT o.id) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue,
       MAX(o.order_date) AS last_order
FROM orders o
JOIN customers c ON c.id = o.customer_id
LEFT JOIN loyalty_tiers t ON t.id = c.loyalty_tier_id
WHERE {BOOKED}
  AND c.is_deleted = FALSE
GROUP BY 1, 2, 3
ORDER BY 5 DESC
LIMIT 40
""",
            [
                col("customer", label="Customer", align="left"),
                col("segment", label="Segment", align="left"),
                col("tier", label="Loyalty tier", align="left"),
                col("orders", label="Orders", fmt="integer", align="right"),
                col("revenue", label="Revenue", fmt="decimal", align="right"),
                col("last_order", label="Last order", align="right"),
            ],
            x=0,
            y=53,
            w=6,
            h=8,
            sort=("revenue", "desc"),
        ),
        table(
            "Below reorder level",
            """
SELECT p.name AS product,
       w.name AS warehouse,
       i.quantity AS on_hand,
       i.reorder_level AS reorder_at,
       i.reorder_level - i.quantity AS shortfall
FROM inventory i
JOIN products p ON p.id = i.product_id
JOIN warehouses w ON w.id = i.warehouse_id
WHERE i.quantity < i.reorder_level
ORDER BY 5 DESC
LIMIT 40
""",
            [
                col("product", label="Product", align="left"),
                col("warehouse", label="Warehouse", align="left"),
                col("on_hand", label="On hand", fmt="integer", align="right"),
                col("reorder_at", label="Reorder at", fmt="integer", align="right"),
                col("shortfall", label="Short by", fmt="integer", align="right"),
            ],
            x=6,
            y=53,
            w=6,
            h=8,
            sort=("shortfall", "desc"),
        ),
        table(
            "Latest orders",
            """
SELECT o.order_date AS placed,
       c.name AS customer,
       o.channel AS channel,
       o.status AS status,
       ROUND(o.total_amount, 2) AS total
FROM orders o
JOIN customers c ON c.id = o.customer_id
ORDER BY o.order_date DESC, o.id DESC
LIMIT 40
""",
            [
                col("placed", label="Placed", align="left"),
                col("customer", label="Customer", align="left"),
                col("channel", label="Channel", align="left"),
                col("status", label="Status", align="left"),
                col("total", label="Total", fmt="decimal", align="right"),
            ],
            x=0,
            y=61,
            w=6,
            h=8,
        ),
        table(
            "Carrier performance",
            """
SELECT ca.name AS carrier,
       COUNT(*) AS shipments,
       ROUND(AVG(s.delivered_at::date - s.shipped_at::date), 1) AS avg_days,
       ROUND(
         1.0 * COUNT(*) FILTER (WHERE s.delivered_at IS NOT NULL) / COUNT(*), 4
       ) AS delivered,
       ROUND(SUM(s.cost), 2) AS cost
FROM shipments s
JOIN carriers ca ON ca.id = s.carrier_id
GROUP BY 1
ORDER BY 2 DESC
""",
            [
                col("carrier", label="Carrier", align="left"),
                col("shipments", label="Shipments", fmt="integer", align="right"),
                col("avg_days", label="Avg days", fmt="decimal", align="right"),
                col("delivered", label="Delivered", fmt="percent", align="right"),
                col("cost", label="Shipping cost", fmt="decimal", align="right"),
            ],
            x=6,
            y=69,
            w=6,
            h=5,
            sort=("shipments", "desc"),
        ),
        table(
            "Supplier scorecard",
            """
SELECT s.name AS supplier,
       r.name AS region,
       s.lead_time_days AS lead_days,
       s.rating AS rating,
       COUNT(ps.product_id) AS products
FROM suppliers s
LEFT JOIN regions r ON r.id = s.region_id
LEFT JOIN product_suppliers ps ON ps.supplier_id = s.id
WHERE s.active = TRUE
GROUP BY 1, 2, 3, 4
ORDER BY 4 DESC
LIMIT 40
""",
            [
                col("supplier", label="Supplier", align="left"),
                col("region", label="Region", align="left"),
                col("lead_days", label="Lead days", fmt="integer", align="right"),
                col("rating", label="Rating", fmt="decimal", align="right"),
                col("products", label="Products", fmt="integer", align="right"),
            ],
            x=0,
            y=69,
            w=6,
            h=8,
            sort=("rating", "desc"),
        ),
        table(
            "Campaigns running",
            """
SELECT pr.name AS promotion,
       pr.promo_type AS kind,
       ROUND(pr.discount_pct / 100.0, 4) AS discount,
       pr.budget AS budget,
       pr.starts_on AS starts,
       pr.ends_on AS ends
FROM promotions pr
WHERE pr.is_active = TRUE
ORDER BY pr.starts_on DESC
LIMIT 40
""",
            [
                col("promotion", label="Campaign", align="left"),
                col("kind", label="Type", align="left"),
                col("discount", label="Discount", fmt="percent", align="right"),
                col("budget", label="Budget", fmt="decimal", align="right"),
                col("starts", label="Starts", align="right"),
                col("ends", label="Ends", align="right"),
            ],
            x=6,
            y=61,
            w=6,
            h=8,
        ),
    ]


# ── the wire ─────────────────────────────────────────────────────────────
def call(
    method: str, path: str, token: str | None = None, body: Any = None
) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(API + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SystemExit(f"{method} {path} → {error.code}\n{detail}") from error
    except urllib.error.URLError as error:
        raise SystemExit(
            f"Could not reach {API}. Is the stack up? (`make up`)\n  {error.reason}"
        ) from error


def settle(method: str, path: str, token: str, body: Any = None) -> Any:
    """`call`, retried while the write it depends on is still in flight.

    `get_db` commits in dependency teardown, which is not ordered before the
    response reaches the client, so a 201 can arrive before its row is
    visible to the next connection. Every call here follows a write — add a
    tile to a dashboard created a millisecond ago — and the losing side of
    that race is a 404 for a dashboard that certainly exists.
    """
    for attempt in range(6):
        try:
            return call(method, path, token, body)
        except SystemExit as error:
            transient = "→ 404" in str(error) or "→ 409" in str(error)
            if not transient or attempt == 5:
                raise
            time.sleep(0.3 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", help="connection name (default: the first)")
    parser.add_argument("--name", default=DASHBOARD_NAME)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="do not delete a dashboard of the same name first",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="run every tile afterwards and report any that failed",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=300,
        help="default refresh interval in seconds; 0 for manual (default: 300)",
    )
    args = parser.parse_args()

    token = call(
        "POST", "/auth/login", body={"email": EMAIL, "password": PASSWORD}
    )["access_token"]

    connections = call("GET", "/connections", token)
    if not connections:
        raise SystemExit("No connections. Add one in Data sources first.")
    if args.connection:
        chosen = next(
            (c for c in connections if c["name"] == args.connection), None
        )
        if chosen is None:
            names = ", ".join(c["name"] for c in connections)
            raise SystemExit(f"No connection called {args.connection!r}. Have: {names}")
    else:
        chosen = connections[0]
    if not chosen.get("last_synced_at"):
        raise SystemExit(
            f"Connection {chosen['name']!r} has never been synced — the guard "
            "resolves every name against the schema snapshot, so no tile can "
            "run. Sync it in Data sources first."
        )
    print(f"connection: {chosen['name']} ({chosen['database_type']})")

    if not args.keep_existing:
        deleted = False
        for existing in call("GET", "/dashboards", token):
            if existing["name"] == args.name:
                call("DELETE", f"/dashboards/{existing['id']}", token)
                deleted = True
                print(f"replaced the existing {args.name!r}")
        # The 204 comes back before the transaction commits — `get_db` commits
        # in dependency teardown, which is not ordered before the response
        # reaches the client — so creating straight away races the delete and
        # loses on the unique name. Wait for the row to actually be gone.
        for _ in range(20):
            if not deleted:
                break
            if all(d["name"] != args.name for d in call("GET", "/dashboards", token)):
                break
            time.sleep(0.25)

    dashboard = settle(
        "POST",
        "/dashboards",
        token,
        {
            "name": args.name,
            "description": DASHBOARD_DESCRIPTION,
            "grid_columns": COLUMNS,
            "row_height_px": ROW_HEIGHT,
            "gap_px": GAP,
            "default_refresh_interval_seconds": args.refresh,
        },
    )
    dashboard_id = dashboard["id"]

    failed: list[tuple[str, str]] = []
    created: list[dict[str, Any]] = []
    for position, spec in enumerate(tiles()):
        payload = {
            "connection_id": None if spec["tile_type"] == "TEXT" else chosen["id"],
            "sql_origin": "HANDWRITTEN",
            "position": position,
            **spec,
        }
        try:
            created.append(
                settle("POST", f"/dashboards/{dashboard_id}/tiles", token, payload)
            )
        except SystemExit as error:
            failed.append((spec["title"], str(error)))

    print(f"created {len(created)} tiles on {args.name!r}")
    for title, error in failed:
        print(f"  REJECTED  {title}\n            {error.splitlines()[-1]}")

    if args.check:
        ids = [t["id"] for t in created if t["tile_type"] != "TEXT"]
        results = call(
            "POST",
            f"/dashboards/{dashboard_id}/data",
            token,
            {"tile_ids": ids},
        )["results"]
        broken = 0
        for tile in created:
            result = results.get(tile["id"])
            if result is None:
                continue
            if result["status"] != "OK":
                broken += 1
                message = (result.get("error") or {}).get("message", "failed")
                print(f"  ERROR   {tile['title']}: {message}")
            elif tile["tile_type"] == "CHART" and not result.get("vega_spec"):
                broken += 1
                print(f"  NOCHART {tile['title']}: {result.get('chart_note')}")
            elif tile["tile_type"] == "METRIC" and not result.get("kpi"):
                broken += 1
                print(f"  NOKPI   {tile['title']}: no big number planned")
        print("every tile ran" if broken == 0 else f"{broken} tile(s) need attention")

    print(f"open: http://localhost:5173 → Dashboards → {args.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
