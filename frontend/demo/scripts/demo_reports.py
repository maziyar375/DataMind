"""The demo's reports: outlines a person approved, run for real, written from the rows.

A report is sections of blocks — each block one question, one statement, one
rendering — and a paragraph per section written from that section's results.
`build.py` runs every block through the guard and the connector and plans its
chart or big number with the backend's own `_chart` / `_kpi`, then checks each
paragraph the way the report worker does (`workers/report._numeric_check`): a
sentence citing block 2 may only state figures block 2's writer was given. The
executive summary is checked against the sections' prose, since that is all the
summary writer is shown.

The report is over the Sales warehouse. Sakila's policy is AGGREGATE, and
reports refuse NONE and AGGREGATE outright: a document written about values
the writer was never shown would be a document of guesses.

The prose is a function of the results, like every narrative in the demo, and
asserts the shape it describes, so moving `DEMO_TODAY` cannot ship a paragraph
that stopped being true.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from questions import Result, check, money, month_name

BOOKED = "o.status IN ('completed', 'shipped')"
LAST_12 = (
    "o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'\n"
    "  AND o.order_date < date_trunc('month', CURRENT_DATE)"
)
LAST_3 = (
    "o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '3 months'\n"
    "  AND o.order_date < date_trunc('month', CURRENT_DATE)"
)


@dataclass
class Block:
    question: str
    title: str
    block_type: str                      # CHART | TABLE | METRIC
    sql: str
    chart: dict[str, Any] | None = None
    time_window: str = "none"


@dataclass
class Section:
    heading: str
    intent: str
    blocks: list[Block] = field(default_factory=list)
    #: The writer's paragraph, `[n]`-cited, from this section's results.
    prose: Callable[[list[Result]], str] | None = None


@dataclass
class ReportScript:
    id: str
    name: str
    description: str
    prompt: str
    owner: str
    privileges: list[str]
    days_old: float
    run_days_ago: float
    sections: list[Section]
    #: The summary: one paragraph, then its findings one per line — written
    #: from the sections' prose alone.
    summary: Callable[[list[str], list[list[Result]]], str]
    summary_intent: str = "The findings to read first."
    connection: str = "sales"


def y(name: str, label: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"field": name, "type": "quantitative", "aggregation": "none"}
    if label:
        out["label"] = label
    return out


def x(name: str, kind: str = "nominal", label: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"field": name, "type": kind}
    if label:
        out["label"] = label
    return out


OWNER = ["describe", "select", "modify", "delete", "manage"]

# ── the statements, shared by the two reports where they ask the same thing ──
MONTHLY_REVENUE_12 = f"""SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1"""

ORDERS_AND_AOV_12 = f"""SELECT to_char(o.order_date, 'YYYY-MM') AS month,
       COUNT(*) AS orders,
       ROUND(AVG(o.total_amount), 2) AS avg_order_value
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY 1
ORDER BY 1"""

REGION_12 = f"""SELECT r.name AS region,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY r.name
ORDER BY revenue DESC"""

CHANNEL_12 = f"""SELECT o.channel,
       COUNT(*) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND {LAST_12}
GROUP BY o.channel
ORDER BY revenue DESC"""

TOP_PRODUCTS_YTD = f"""SELECT p.name AS product,
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
LIMIT 10"""

CATEGORY_3 = f"""SELECT cat.name AS category,
       ROUND(SUM(oi.line_total), 2) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS cat ON cat.id = p.category_id
WHERE {BOOKED}
  AND {LAST_3}
GROUP BY cat.name
ORDER BY revenue DESC"""

CARRIER_6 = """SELECT ca.name AS carrier,
       COUNT(*) AS deliveries,
       ROUND(AVG(EXTRACT(EPOCH FROM (s.delivered_at - s.shipped_at)) / 86400)::numeric, 1) AS avg_days
FROM public.shipments AS s
JOIN public.carriers AS ca ON ca.id = s.carrier_id
WHERE s.delivered_at IS NOT NULL
  AND s.shipped_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ca.name
ORDER BY avg_days DESC"""

REORDER_BY_WAREHOUSE = """SELECT w.name AS warehouse,
       COUNT(*) AS products_to_reorder
FROM public.inventory AS i
JOIN public.warehouses AS w ON w.id = i.warehouse_id
JOIN public.products AS p ON p.id = i.product_id
WHERE i.quantity <= i.reorder_level
  AND p.active
GROUP BY w.name
ORDER BY products_to_reorder DESC"""

WORDS = ["None", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]


def dollars(x: float) -> str:
    return f"${x:,.2f}"


def pct(part: float, whole: float) -> str:
    return f"{part / whole * 100:.1f}%"


# ── Monthly business review (English) ──────────────────────────────────────
def mbr_revenue(results: list[Result]) -> str:
    months = results[0].dicts()
    orders = results[1].dicts()
    aug, jul = months[-1], months[-2]
    peak = max(months, key=lambda m: m["revenue"])
    check(aug["month"][5:7] == "08" and jul["month"][5:7] == "07", "the review month is August")
    check(peak["month"][5:7] == "06", "June is the year's peak")
    since = [m for m in months if m["month"] >= "2026-06"]
    check(min(since, key=lambda m: m["revenue"]) is aug, "August is the quietest month since May")
    low = min(orders, key=lambda o: o["orders"])
    high = max(orders, key=lambda o: o["orders"])
    return (
        f"August brought in {money(aug['revenue'])}, a little less than July's {money(jul['revenue'])}, "
        f"and the quietest month since May [1]. It sits well below June's "
        f"{money(peak['revenue'])}, the highest month of the year [1]. Orders fell to {orders[-1]['orders']} "
        f"from {orders[-2]['orders']} in July, while the average order rose to {dollars(orders[-1]['avg_order_value'])} "
        f"from {dollars(orders[-2]['avg_order_value'])} [2]. Across the twelve months the order count ran from "
        f"{low['orders']} in {month_name(low['month'] + '-01')} to {high['orders']} in "
        f"{month_name(high['month'] + '-01')} [2]."
    )


def mbr_sources(results: list[Result]) -> str:
    regions = results[0].dicts()
    channels = results[1].dicts()
    total = sum(r["revenue"] for r in regions)
    check(abs(total - sum(c["revenue"] for c in channels)) < 0.01, "regions and channels add up to the same revenue")
    first, second, third = regions[0], regions[1], regions[2]
    check(first["region"] == "North America" and second["region"] == "Europe", "North America then Europe")
    web = channels[0]
    check(web["channel"] == "web", "the web leads")
    phone = next(c for c in channels if c["channel"] == "phone")
    partner = next(c for c in channels if c["channel"] == "partner")
    return (
        f"North America brought in {money(first['revenue'])} over the last twelve months, "
        f"{pct(first['revenue'], total)} of revenue, followed by Europe at {money(second['revenue'])} [1]. "
        f"Asia Pacific is third at {money(third['revenue'])}, and Africa the smallest named region at "
        f"{money(next(r for r in regions if r['region'] == 'Africa')['revenue'])} [1]. The web carried "
        f"{web['orders']:,} orders and {money(web['revenue'])}, {pct(web['revenue'], total)} of the total, while "
        f"phone orders brought in {money(phone['revenue'])} and partners {money(partner['revenue'])} [2]."
    )


def mbr_products(results: list[Result]) -> str:
    products = results[0].dicts()
    categories = results[1].dicts()
    first, second = products[0], products[1]
    check(first["product"] == 'Arcwave 27" 4K Monitor', "the 27-inch monitor leads")
    check(first["revenue"] > 2 * second["revenue"], "more than twice the next product")
    displays = [p for p in products if p["category"] == "Displays"]
    top, runner = categories[0], categories[1]
    check(top["category"] == "Displays" and top["revenue"] > 2 * runner["revenue"], "Displays lead by more than double")
    return (
        f"The Arcwave 27-inch monitor leads the year with {money(first['revenue'])} from {first['units']:,} units, "
        f"more than twice the next product, the Arcwave 34-inch UltraWide at {money(second['revenue'])} [1]. "
        f"{WORDS[len(displays)]} of the top ten are Displays products [1]. Over the last three months Displays brought in "
        f"{money(top['revenue'])}, more than twice {runner['category']} at {money(runner['revenue'])}, and "
        f"{categories[-1]['category']} was the smallest category at {money(categories[-1]['revenue'])} [2]."
    )


def mbr_fulfilment(results: list[Result]) -> str:
    carriers = results[0].dicts()
    reorder = results[1].dicts()
    days = [c["avg_days"] for c in carriers]
    check(max(days) - min(days) <= 0.5, "no carrier stands out on speed")
    busiest = max(carriers, key=lambda c: c["deliveries"])
    total = sum(r["products_to_reorder"] for r in reorder)
    return (
        f"Every carrier delivered in {min(days)} to {max(days)} days on average over the last six months, so none "
        f"stands out on speed; {busiest['carrier']} carried the most, {busiest['deliveries']:,} deliveries [1]. "
        f"Across {len(reorder)} warehouses, {total} stock lines are at or below their reorder level, "
        f"{reorder[0]['products_to_reorder']} of them in {reorder[0]['warehouse']} [2]."
    )


def mbr_summary(prose: list[str], results: list[list[Result]]) -> str:
    """The shape `REPORT_SUMMARY_SYSTEM` asks for: a paragraph on the finding
    that matters most, a blank line, then one finding per "- " line, none of
    them repeating the paragraph."""
    months = results[0][0].dicts()
    aug, jul = months[-1], months[-2]
    peak = max(months, key=lambda m: m["revenue"])
    regions = results[1][0].dicts()
    channels = results[1][1].dicts()
    total = sum(r["revenue"] for r in regions)
    top = results[2][0].dicts()[0]
    reorder = results[3][1].dicts()
    check(aug["revenue"] < jul["revenue"], "August is below July")
    return (
        f"August revenue was {money(aug['revenue'])}, a little below July's {money(jul['revenue'])}, on "
        f"{results[0][1].dicts()[-1]['orders']} orders, and June's {money(peak['revenue'])} remains the high "
        f"point of the year.\n\n"
        f"- North America brought in {money(regions[0]['revenue'])} over the last twelve months, "
        f"{pct(regions[0]['revenue'], total)} of revenue.\n"
        f"- The web carried {pct(channels[0]['revenue'], total)} of revenue.\n"
        f"- The Arcwave 27-inch monitor leads the year with {money(top['revenue'])}.\n"
        f"- {sum(r['products_to_reorder'] for r in reorder)} stock lines are at or below their reorder level."
    )


MBR = ReportScript(
    id="monthly-business-review",
    name="Monthly business review — August 2026",
    description="Revenue, where it came from, products and fulfilment, for the leadership meeting.",
    prompt=(
        "A monthly business review for August 2026 for the leadership team: how revenue and orders moved, "
        "where revenue came from, which products led, and how fulfilment performed."
    ),
    owner="Mazbar Azami", privileges=OWNER, days_old=12, run_days_ago=0.8,
    summary=mbr_summary,
    summary_intent="The four things the leadership team should read before the meeting.",
    sections=[
        Section("Revenue and orders", "How August's revenue and order count compare with July and with the last twelve months.", [
            Block("What was revenue by month over the last 12 months?", "Revenue, latest month", "METRIC",
                  MONTHLY_REVENUE_12, time_window="last_12_months"),
            Block("How many orders did we take each month, and what was the average order value?",
                  "Orders and average order value by month", "CHART", ORDERS_AND_AOV_12,
                  chart={"chart_type": "combo", "x_axis": x("month", label="Month"), "y_axis": y("orders", "Orders"),
                         "y2_axis": y("avg_order_value", "Average order")}, time_window="last_12_months"),
        ], mbr_revenue),
        Section("Where revenue came from", "Revenue by customer region and by sales channel over the last twelve months.", [
            Block("What was revenue by customer region over the last 12 months?", "Revenue by region", "CHART",
                  REGION_12, chart={"chart_type": "bar", "orientation": "horizontal", "x_axis": x("region", label="Region"),
                                    "y_axis": y("revenue", "Revenue")}, time_window="last_12_months"),
            Block("How did orders and revenue split across sales channels?", "Revenue by channel", "CHART",
                  CHANNEL_12, chart={"chart_type": "pie", "x_axis": x("channel"), "y_axis": y("revenue")},
                  time_window="last_12_months"),
        ], mbr_sources),
        Section("Products", "Which products and categories led revenue this year and this quarter.", [
            Block("What are the top 10 products by revenue this year?", "Top ten products this year", "TABLE",
                  TOP_PRODUCTS_YTD, time_window="ytd"),
            Block("What was revenue by product category over the last three months?", "Revenue by category, last three months",
                  "CHART", CATEGORY_3, chart={"chart_type": "bar", "x_axis": x("category", label="Category"),
                                              "y_axis": y("revenue", "Revenue")}, time_window="last_3_months"),
        ], mbr_products),
        Section("Fulfilment", "How quickly carriers delivered over the last six months, and which stock needs reordering.", [
            Block("How many deliveries did each carrier make, and how long did they take?", "Deliveries and days to deliver",
                  "CHART", CARRIER_6, chart={"chart_type": "combo", "x_axis": x("carrier", label="Carrier"),
                                             "y_axis": y("deliveries", "Deliveries"), "y2_axis": y("avg_days", "Average days")}),
            Block("How many stock lines are at or below their reorder level in each warehouse?",
                  "Stock lines to reorder, by warehouse", "TABLE", REORDER_BY_WAREHOUSE),
        ], mbr_fulfilment),
    ],
)


REPORTS: list[ReportScript] = [MBR]
