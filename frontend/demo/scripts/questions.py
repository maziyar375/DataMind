"""Every question the demo can answer, and how each answer is written.

A question is the statement a model would have written, run for real by
`build.py` — through the backend's own guard, a real driver, the backend's own
result checks and chart compiler — and a narrative written *from those rows*.
The narrative is a function of the result rather than a string, so the sentence
and the table cannot disagree, and each one asserts the shape it describes: if a
new `DEMO_TODAY` moves the data so that "June stands out" is no longer true, the
build fails instead of shipping a false sentence.

Under the Sakila connection's AGGREGATE policy the model never sees a value, so
those narratives describe the result's shape and never quote a figure — which is
exactly what the product does.

Adding a question: append a `Question` below, run `build-fixtures.sh`, and read
the generated run in `mock/fixtures/runs.generated.ts`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable


# ── result helpers ─────────────────────────────────────────────────────────
@dataclass
class Result:
    columns: list[str]
    rows: list[list[Any]]
    #: Codes of the advisory findings `inspect` raised — the caveats `present`
    #: is handed and asked to work into the answer.
    findings: list[str] = field(default_factory=list)
    #: A deep step's computation (`pipeline/evidence.compute`), or None.
    computed: dict[str, Any] | None = None

    def col(self, name: str) -> list[Any]:
        i = self.columns.index(name)
        return [r[i] for r in self.rows]

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]


@dataclass
class Schema:
    """What a METADATA answer is written from: the synced snapshot."""
    tables: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    schema_name: str


def money(x: float) -> str:
    return f"${x:,.0f}"


def millions(x: float) -> str:
    return f"${x / 1e6:.1f} million"


def month_name(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return d.strftime("%B %Y")


FA_DIGITS = str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")
FA_MONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]


def fa_money(x: float) -> str:
    return f"{x:,.0f}".translate(FA_DIGITS) + " دلار"


def fa_num(x: float | int) -> str:
    return f"{x:,.0f}".translate(FA_DIGITS)


def fa_pct(x: float) -> str:
    return f"{x:.0f}".translate(FA_DIGITS) + "٪"


def check(condition: bool, claim: str) -> None:
    if not condition:
        raise AssertionError(f"narrative claim no longer holds: {claim}")


# ── the question ───────────────────────────────────────────────────────────
@dataclass
class Question:
    id: str
    connection: str                      # 'sales' | 'sakila'
    text: str
    #: Other phrasings that count as this question (compared normalised).
    aliases: list[str] = field(default_factory=list)
    intent: str = "ANALYTICAL"           # 'ANALYTICAL' | 'METADATA'
    #: The statement that ran. None for a METADATA question.
    sql: str | None = None
    #: Earlier drafts the guard refused, in order — the repair loop.
    rejected: list[str] = field(default_factory=list)
    #: What the model proposed to the chart node. None: it declined.
    chart: dict[str, Any] | None = None
    narrative: Callable[..., str] | None = None
    #: What this is in the demo's guide: the capability it shows.
    shows: str = ""
    #: Suggested follow-ups after this answer (question ids).
    followups: list[str] = field(default_factory=list)
    #: Asked in a conversation that is already in the sidebar.
    history: bool = False


# ── Sales warehouse (PostgreSQL, SAMPLE) ──────────────────────────────────
def revenue_trend(r: Result) -> str:
    months = r.col("month")
    revenue = r.col("revenue")
    total = sum(revenue)
    peak_i = max(range(len(revenue)), key=lambda i: revenue[i])
    check(len(revenue) == 12, "twelve complete months")
    check(months[peak_i][5:7] == "06", "June is the highest month")
    neighbours = (revenue[peak_i - 1] + revenue[peak_i + 1]) / 2
    lift = revenue[peak_i] / neighbours - 1
    check(lift > 0.4, "June is far above its neighbours")
    dec = next(i for i, m in enumerate(months) if m[5:7] == "12")
    jan = dec + 1
    check(revenue[jan] < revenue[dec] * 0.7, "January falls well below December")
    check(revenue[-1] > revenue[0], "the year ends higher than it started")
    growth = revenue[-1] / revenue[0] - 1
    return (
        f"Revenue over the last 12 complete months came to about **{millions(total)}**, "
        f"rising from {money(revenue[0])} in {month_name(months[0])} to {money(revenue[-1])} in "
        f"{month_name(months[-1])} ({growth:.0%} higher). The holiday peak landed in "
        f"{month_name(months[dec])} at {money(revenue[dec])}, followed by the usual dip in "
        f"{month_name(months[jan])} ({money(revenue[jan])}). **{month_name(months[peak_i])} is the outlier** "
        f"at {money(revenue[peak_i])} — about {lift:.0%} above the months either side of it — "
        f"so something beyond the normal trend happened that month."
    )


def top_products(r: Result) -> str:
    rows = r.dicts()
    check(len(rows) == 10, "ten products")
    first, second = rows[0], rows[1]
    check("Monitor" in first["product"], "a monitor leads")
    share = first["revenue"] / sum(x["revenue"] for x in rows)
    categories = sorted({x["category"] for x in rows})
    displays = [x for x in rows if x["category"] == "Displays"]
    return (
        f"The **{first['product']}** leads the year by a wide margin with {money(first['revenue'])} "
        f"from {first['units']:,} units — {share:.0%} of this top-ten list on its own — ahead of the "
        f"{second['product']} at {money(second['revenue'])}. {len(displays)} of the ten are "
        f"Displays products, and the list spans {len(categories)} categories. Each product is "
        f"shown with its preferred supplier from the product–supplier bridge table."
    )


def region_revenue(r: Result) -> str:
    rows = r.dicts()
    total = sum(x["revenue"] for x in rows)
    first, second = rows[0], rows[1]
    check(first["region"] == "North America", "North America leads")
    small = [x for x in rows if x["revenue"] / total < 0.05]
    return (
        f"**{first['region']}** brought in {money(first['revenue'])} over the last 12 months — "
        f"{first['revenue'] / total:.0%} of the {millions(total)} total — followed by "
        f"{second['region']} at {money(second['revenue'])} ({second['revenue'] / total:.0%}). "
        f"{len(small)} regions each contributed less than 5%, the smallest being "
        f"{rows[-1]['region']} at {money(rows[-1]['revenue'])}."
    )


def category_fa(r: Result) -> str:
    rows = r.dicts()
    total = sum(x["revenue"] for x in rows)
    first, second, last = rows[0], rows[1], rows[-1]
    check(first["category"] == "Displays", "Displays lead")
    return (
        f"در سه ماه کامل گذشته، مجموع فروش شش دسته‌بندی حدود **{fa_money(total)}** بوده است. "
        f"دسته‌بندی **{first['category']}** (نمایشگرها) با {fa_money(first['revenue'])} در صدر قرار دارد — "
        f"یعنی {fa_pct(100 * first['revenue'] / total)} کل فروش — و پس از آن {second['category']} "
        f"با {fa_money(second['revenue'])} است. کمترین فروش مربوط به {last['category']} با "
        f"{fa_money(last['revenue'])} بوده است."
    )


def row_counts_sales(r: Result) -> str:
    rows = r.dicts()
    check(rows[0]["row_count"] >= rows[1]["row_count"], "sorted largest first")
    small = [x for x in rows if x["row_count"] < 10]
    orders = next(x for x in rows if x["table_name"] == "orders")
    customers = next(x for x in rows if x["table_name"] == "customers")
    check("C_SOFT_DELETE_UNFILTERED" in r.findings, "the soft-delete caveat was raised")
    return (
        f"The {len(rows)} tables range from **{rows[0]['table_name']}** at {rows[0]['row_count']:,} rows "
        f"down to single digits. After it come {rows[1]['table_name']} "
        f"({rows[1]['row_count']:,}) and {rows[2]['table_name']} ({rows[2]['row_count']:,}); "
        f"orders itself holds {orders['row_count']:,}. These are raw row counts, so customers "
        f"({customers['row_count']:,}) includes closed accounts that are only flagged as deleted. "
        f"{len(small)} small reference tables — regions and loyalty tiers among them — have fewer "
        f"than ten rows each."
    )


def sample_orders(r: Result) -> str:
    rows = r.dicts()
    statuses: dict[str, int] = {}
    for x in rows:
        statuses[x["status"]] = statuses.get(x["status"], 0) + 1
    biggest = max(rows, key=lambda x: x["total_amount"])
    parts = ", ".join(f"{n} {s}" for s, n in sorted(statuses.items(), key=lambda kv: -kv[1]))
    return (
        f"Here are the {len(rows)} most recent orders, newest first. They were all placed in the last "
        f"few days, so most are still moving through fulfilment ({parts}). The largest is "
        f"{money(biggest['total_amount'])} from {biggest['customer']}, placed by {biggest['channel']}."
    )


def active_customers(r: Result) -> str:
    rows = r.dicts()
    total = sum(x["customers"] for x in rows)
    check(rows[0]["segment"] == "SMB", "SMB is the largest segment")
    ent = next(x for x in rows if x["segment"] == "Enterprise")
    return (
        f"You have **{total:,} active customers**: {rows[0]['customers']:,} {rows[0]['segment']}, "
        f"{rows[1]['customers']:,} {rows[1]['segment']} and {ent['customers']:,} Enterprise accounts. "
        f"Closed accounts are excluded — they stay in the customers table with is_deleted set."
    )


def order_value_by_channel(r: Result) -> str:
    rows = r.dicts()
    top = max(rows, key=lambda x: x["avg_order_value"])
    low = min(rows, key=lambda x: x["avg_order_value"])
    volume = max(rows, key=lambda x: x["orders"])
    return (
        f"Over the last 90 days, **{top['channel']}** orders were the largest on average at "
        f"{money(top['avg_order_value'])}, against {money(low['avg_order_value'])} for {low['channel']}. "
        f"{volume['channel'].capitalize()} still carries the most volume with {volume['orders']:,} orders, "
        f"which is why it leads on total revenue despite the smaller baskets."
    )


def carrier_speed(r: Result) -> str:
    rows = r.dicts()
    slow, fast = rows[0], rows[-1]
    check(slow["avg_days"] > fast["avg_days"], "sorted slowest first")
    return (
        f"Over the last six months **{slow['carrier']}** has been the slowest, averaging "
        f"{slow['avg_days']:.1f} days from dispatch to delivery across {slow['deliveries']:,} shipments. "
        f"{fast['carrier']} is the quickest at {fast['avg_days']:.1f} days. Only delivered shipments "
        f"are counted — anything still in transit has no delivery date yet."
    )


# ── Sakila (MySQL, AGGREGATE — the model sees no values) ──────────────────
def sakila_categories(r: Result) -> str:
    check(len(r.rows) == 16, "sixteen categories")
    return (
        f"The query returned **{len(r.rows)} film categories**, each with its number of paid rentals, "
        f"how many days a rental was kept on average, and the total payments behind them, ranked "
        f"from the highest-earning category down. "
        f"This connection shares only the shape of a result with the model, not its values, so I "
        f"can't name the leader here — it is the first row of the table and the longest bar below."
    )


def sakila_row_counts(r: Result) -> str:
    return (
        f"I counted the rows in all **{len(r.rows)} tables** and returned one row per table, largest "
        f"first. The counts themselves weren't shared with me under this connection's result-sharing "
        f"policy, so they're in the table below rather than in this summary."
    )


def sakila_sample(r: Result) -> str:
    return (
        f"Here is a sample of **{len(r.rows)} films** in title order, each with its category, rating, "
        f"rental rate and running time. The individual values weren't shared with me, so the rows "
        f"are in the table below."
    )


def sakila_actors(r: Result) -> str:
    return (
        f"The query ranked actors by how many films they appear in and returned the top "
        f"**{len(r.rows)}**, with each actor's film count. Values aren't shared with the model on "
        f"this connection, so the names and counts are in the table and chart below."
    )


# ── METADATA — answered from the snapshot, no SQL ─────────────────────────
GROUPS_SALES = [
    ("Orders and money", ["orders", "order_items", "order_promotions", "payments", "refunds", "returns", "order_status_history", "sales_daily_rollup"]),
    ("Fulfilment", ["shipments", "shipment_items", "warehouses", "carriers", "inventory"]),
    ("Catalogue", ["products", "product_variants", "categories", "subcategories", "brands", "suppliers", "product_suppliers", "price_lists", "price_list_items", "product_price_history", "tags", "product_tags"]),
    ("Customers", ["customers", "customer_addresses", "loyalty_tiers", "reviews", "support_tickets", "wishlists"]),
    ("Sales team", ["employees", "teams", "employee_teams"]),
    ("Reference", ["countries", "regions", "currencies", "tax_rates", "payment_methods", "promotions", "coupons"]),
]

GROUPS_SAKILA = [
    ("Films", ["film", "film_text", "film_actor", "film_category", "actor", "category", "language"]),
    ("Stores and stock", ["store", "inventory", "staff"]),
    ("Customers and places", ["customer", "address", "city", "country"]),
    ("Transactions", ["rental", "payment"]),
]


def _grouped(s: Schema, groups: list[tuple[str, list[str]]]) -> list[str]:
    names = {t["name"] for t in s.tables}
    listed = [n for _, members in groups for n in members]
    check(sorted(listed) == sorted(names), "every table is in exactly one group")
    return [f"• **{label}** — {', '.join(m for m in members)}" for label, members in groups]


def tables_sales(s: Schema) -> str:
    lines = _grouped(s, GROUPS_SALES)
    orders = next(t for t in s.tables if t["name"] == "orders")
    return (
        f"You have **{len(s.tables)} tables** in the {s.schema_name} schema — an order-to-cash model for "
        f"the online store:\n\n" + "\n".join(lines) + "\n\n"
        f"The heart of it is orders (~{orders['approx_row_count']:,} rows) and order_items, which "
        f"connects every order to the products it contained. Most tables carry descriptions from "
        f"your database's own comments."
    )


def tables_sakila(s: Schema) -> str:
    lines = _grouped(s, GROUPS_SAKILA)
    return (
        f"You have **{len(s.tables)} tables** in the {s.schema_name} database — a DVD rental business "
        f"with two stores:\n\n" + "\n".join(lines) + "\n\n"
        f"rental and payment are the transactions; inventory links each rental to a physical copy of a "
        f"film at a store."
    )


def joins(s: Schema, hubs: list[str]) -> str:
    by_table: dict[str, list[str]] = {}
    for rel in s.relationships:
        src = rel["from_table"].split(".")[-1]
        dst = rel["to_table"].split(".")[-1]
        by_table.setdefault(src, []).append(f"{dst} ({rel['from_column']})")
    lines = [f"• **{hub}** → {', '.join(by_table[hub])}" for hub in hubs if hub in by_table]
    check(len(lines) == len(hubs), "every hub has foreign keys")
    total = len(s.relationships)
    return (
        f"There are **{total} foreign keys** in this database. The main join paths:\n\n"
        + "\n".join(lines)
        + "\n\nEvery other table joins to these through the keys named in brackets. The graph view "
        "on the data source's Schema tab draws all of them."
    )


def joins_sales(s: Schema) -> str:
    return joins(s, ["orders", "order_items", "products", "customers", "shipments", "payments"])


def joins_sakila(s: Schema) -> str:
    return joins(s, ["rental", "payment", "inventory", "film_category", "film_actor", "customer"])


# ── the list ──────────────────────────────────────────────────────────────
SALES_FOLLOWUPS = ["revenue-trend", "top-products", "region-revenue", "category-fa"]
SAKILA_FOLLOWUPS = ["sakila-categories", "sakila-actors", "sakila-tables"]

QUESTIONS: list[Question] = [
    # The four starters the product shows on an empty chat, answered on both.
    Question(
        id="sales-tables", connection="sales", intent="METADATA",
        text="What tables do I have?",
        aliases=["what tables are there", "list the tables", "which tables do i have"],
        narrative=tables_sales, followups=SALES_FOLLOWUPS,
        shows="A schema question: answered from the synced snapshot, no SQL runs.",
    ),
    Question(
        id="sales-joins", connection="sales", intent="METADATA",
        text="Which tables can I join together?",
        narrative=joins_sales, followups=SALES_FOLLOWUPS,
        shows="Foreign keys from the snapshot, summarised without querying data.",
    ),
    Question(
        id="sales-row-counts", connection="sales",
        text="How many records are in each table?",
        sql="__ROW_COUNTS__",
        chart={"chart_type": "bar", "x_axis": {"field": "table_name", "type": "nominal"}, "y_axis": {"field": "row_count", "type": "quantitative", "aggregation": "none"}},
        narrative=row_counts_sales, followups=SALES_FOLLOWUPS,
        shows="A generated UNION ALL across every table.",
    ),
    Question(
        id="sales-sample", connection="sales",
        text="Show me a sample of rows",
        sql="""SELECT o.id, o.order_date, c.name AS customer, o.channel, o.status, o.total_amount
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
ORDER BY o.placed_at DESC
LIMIT 20""",
        chart=None, narrative=sample_orders, followups=SALES_FOLLOWUPS,
        shows="A plain sample of the newest orders.",
    ),
    # 1. Time series.
    Question(
        id="revenue-trend", connection="sales",
        text="How has monthly revenue trended over the last 12 months?",
        aliases=["monthly revenue over the last 12 months", "revenue by month for the last year",
                 "show monthly revenue for the last 12 months", "revenue trend"],
        sql="""SELECT date_trunc('month', o.order_date)::date AS month,
       SUM(o.total_amount) AS revenue,
       COUNT(*) AS orders
FROM public.orders AS o
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""",
        chart={"chart_type": "line", "x_axis": {"field": "month", "type": "temporal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        narrative=revenue_trend, followups=["top-products", "region-revenue", "category-fa", "sales-tables"],
        shows="A time-series aggregate: narrative, table and a line chart that agree.",
    ),
    # 3. Multi-join.
    Question(
        id="top-products", connection="sales",
        text="What are our top 10 products by revenue this year, with their category and preferred supplier?",
        aliases=["top 10 products by revenue this year", "top products this year", "best selling products this year"],
        sql="""SELECT p.name AS product,
       c.name AS category,
       s.name AS preferred_supplier,
       SUM(oi.quantity) AS units,
       SUM(oi.line_total) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS c ON c.id = p.category_id
LEFT JOIN public.product_suppliers AS ps ON ps.product_id = p.id AND ps.is_preferred
LEFT JOIN public.suppliers AS s ON s.id = ps.supplier_id
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('year', CURRENT_DATE)
GROUP BY p.name, c.name, s.name
ORDER BY revenue DESC
LIMIT 10""",
        chart={"chart_type": "bar", "x_axis": {"field": "product", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        narrative=top_products, followups=["revenue-trend", "region-revenue", "category-fa"],
        shows="Six tables joined through a bridge table, with a filtered LEFT JOIN.",
    ),
    # 5. The repair loop.
    Question(
        id="region-revenue", connection="sales",
        text="Break down revenue by customer region for the last 12 months",
        aliases=["revenue by region", "revenue by customer region", "revenue by region for the last 12 months"],
        rejected=["""SELECT c.region AS region,
       SUM(o.total_amount) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY revenue DESC"""],
        sql="""SELECT r.name AS region,
       SUM(o.total_amount) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY r.name
ORDER BY revenue DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "region", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        narrative=region_revenue, followups=["revenue-trend", "top-products", "category-fa"],
        shows="The first draft fails validation; the bounded repair loop fixes it.",
    ),
    # 6. Persian.
    Question(
        id="category-fa", connection="sales",
        text="فروش هر دسته‌بندی محصول در سه ماه گذشته چقدر بوده است؟",
        aliases=["فروش هر دسته بندی محصول در سه ماه گذشته چقدر بوده است", "فروش هر دسته‌بندی در سه ماه گذشته"],
        sql="""SELECT c.name AS category,
       SUM(oi.line_total) AS revenue
FROM public.orders AS o
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
JOIN public.categories AS c ON c.id = p.category_id
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '3 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY c.name
ORDER BY revenue DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "category", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        narrative=category_fa, followups=["revenue-trend", "top-products", "region-revenue"],
        shows="A question in Persian: the answer is written right-to-left.",
    ),
    # In the sidebar already.
    Question(
        id="active-customers", connection="sales", history=True,
        text="How many active customers do we have in each segment?",
        sql="""SELECT c.segment,
       COUNT(*) AS customers
FROM public.customers AS c
WHERE NOT c.is_deleted
GROUP BY c.segment
ORDER BY customers DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "segment", "type": "nominal"}, "y_axis": {"field": "customers", "type": "quantitative", "aggregation": "none"}},
        narrative=active_customers, followups=SALES_FOLLOWUPS,
        shows="The soft-delete filter the column comment asks for.",
    ),
    Question(
        id="order-value-channel", connection="sales", history=True,
        text="What's our average order value by sales channel over the last 90 days?",
        sql="""SELECT o.channel,
       COUNT(*) AS orders,
       ROUND(AVG(o.total_amount), 2) AS avg_order_value,
       SUM(o.total_amount) AS revenue
FROM public.orders AS o
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= CURRENT_DATE - 90
GROUP BY o.channel
ORDER BY revenue DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "channel", "type": "nominal"}, "y_axis": {"field": "avg_order_value", "type": "quantitative", "aggregation": "none"}},
        narrative=order_value_by_channel, followups=SALES_FOLLOWUPS,
    ),
    Question(
        id="carrier-speed", connection="sales", history=True,
        text="Which carriers take the longest to deliver?",
        sql="""SELECT ca.name AS carrier,
       COUNT(*) AS deliveries,
       ROUND(AVG(EXTRACT(EPOCH FROM (s.delivered_at - s.shipped_at)) / 86400)::numeric, 1) AS avg_days
FROM public.shipments AS s
JOIN public.carriers AS ca ON ca.id = s.carrier_id
WHERE s.delivered_at IS NOT NULL
  AND s.shipped_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ca.name
ORDER BY avg_days DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "carrier", "type": "nominal"}, "y_axis": {"field": "avg_days", "type": "quantitative", "aggregation": "none"}},
        narrative=carrier_speed, followups=SALES_FOLLOWUPS,
    ),
    # ── Sakila ──
    Question(
        id="sakila-tables", connection="sakila", intent="METADATA",
        text="What tables do I have?",
        aliases=["what tables are there", "list the tables", "which tables do i have"],
        narrative=tables_sakila, followups=SAKILA_FOLLOWUPS,
        shows="The same schema question on MySQL.",
    ),
    Question(
        id="sakila-joins", connection="sakila", intent="METADATA",
        text="Which tables can I join together?",
        narrative=joins_sakila, followups=SAKILA_FOLLOWUPS,
    ),
    Question(
        id="sakila-row-counts", connection="sakila",
        text="How many records are in each table?",
        sql="__ROW_COUNTS__",
        chart={"chart_type": "bar", "x_axis": {"field": "table_name", "type": "nominal"}, "y_axis": {"field": "row_count", "type": "quantitative", "aggregation": "none"}},
        narrative=sakila_row_counts, followups=SAKILA_FOLLOWUPS,
    ),
    Question(
        id="sakila-sample", connection="sakila",
        text="Show me a sample of rows",
        sql="""SELECT f.title, c.name AS category, f.rating, f.rental_rate, f.length
FROM sakila.film AS f
JOIN sakila.film_category AS fc ON fc.film_id = f.film_id
JOIN sakila.category AS c ON c.category_id = fc.category_id
ORDER BY f.title
LIMIT 20""",
        chart=None, narrative=sakila_sample, followups=SAKILA_FOLLOWUPS,
    ),
    # 4. Same UX, different engine.
    Question(
        id="sakila-categories", connection="sakila",
        text="Which film categories bring in the most rental revenue, and how long are they kept?",
        aliases=["Which film categories bring in the most rental revenue?", "rental revenue by film category",
                 "revenue by category", "top film categories by revenue"],
        sql="""SELECT c.name AS category,
       COUNT(r.rental_id) AS rentals,
       ROUND(AVG(DATEDIFF(r.return_date, r.rental_date)), 1) AS avg_days_kept,
       SUM(p.amount) AS revenue
FROM sakila.payment AS p
JOIN sakila.rental AS r ON r.rental_id = p.rental_id
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
JOIN sakila.film_category AS fc ON fc.film_id = i.film_id
JOIN sakila.category AS c ON c.category_id = fc.category_id
GROUP BY c.name
ORDER BY revenue DESC""",
        chart={"chart_type": "bar", "x_axis": {"field": "category", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        narrative=sakila_categories, followups=["sakila-actors", "sakila-tables", "sakila-joins"],
        shows="MySQL: same pipeline, MySQL dialect, and an AGGREGATE policy that keeps values from the model.",
    ),
    Question(
        id="sakila-actors", connection="sakila", history=True,
        text="Which actors appear in the most films?",
        sql="""SELECT CONCAT(a.first_name, ' ', a.last_name) AS actor,
       COUNT(fa.film_id) AS films
FROM sakila.actor AS a
JOIN sakila.film_actor AS fa ON fa.actor_id = a.actor_id
GROUP BY a.actor_id, a.first_name, a.last_name
ORDER BY films DESC, actor
LIMIT 10""",
        chart={"chart_type": "bar", "x_axis": {"field": "actor", "type": "nominal"}, "y_axis": {"field": "films", "type": "quantitative", "aggregation": "none"}},
        narrative=sakila_actors, followups=["sakila-categories", "sakila-tables", "sakila-joins"],
    ),
]

#: The questions the guide in the demo banner offers, in the brief's order.
GUIDE = ["revenue-trend", "sales-tables", "top-products", "sakila-categories", "region-revenue", "category-fa"]

#: The conversations already in the sidebar, newest first: (question id, days before today, hour).
HISTORY = [
    ("active-customers", 1, 16.4),
    ("order-value-channel", 3, 10.2),
    ("sakila-actors", 6, 14.8),
    ("carrier-speed", 9, 11.5),
]


# ── deep analyses ──────────────────────────────────────────────────────────
@dataclass
class DeepStep:
    """One step of a scripted plan: the sub-question, and the statement that ran.

    The fields are `pipeline/state.PlanStep`'s. `planned` is the wording the
    planner first wrote when the step was sharpened once its dependencies had
    answered (`PLAN_REVISED`); None, it ran as planned. A step that depends on
    nothing is never revised, because the reviser has nothing to read.
    """
    question: str
    intent: str                          # CONFIRM | DECOMPOSE | COMPARE | DRILL | CHECK
    why: str
    tool: str                            # SQL | CONTRIBUTION | COMPARE_PERIODS | OUTLIERS
    sql: str
    depends_on: list[int] = field(default_factory=list)
    planned: str | None = None
    planned_why: str | None = None
    #: What the model proposes to the chart node if this step's result is the
    #: one charted — the last step, or the last one to run before *Answer now*.
    chart: dict[str, Any] | None = None


@dataclass
class DeepQuestion:
    """A why-question, answered by a plan of several steps.

    `answer` is the writer's prose, `[n]`-cited, written from the steps'
    results. It is called with **every prefix** of them, because *Answer now*
    stops a plan after any step and the answer is then written from what was
    found: it must say what those steps establish and nothing more. `build.py`
    checks every version with the backend's own `check_claims`.
    """
    id: str
    connection: str
    text: str
    aliases: list[str]
    restatement: str
    stop_when: str
    steps: list[DeepStep]
    answer: Callable[[list[Result]], str]
    shows: str = ""
    followups: list[str] = field(default_factory=list)


#: The products of Meridian's June order, as a sentence names them. Not the
#: catalogue names: `check_claims` reads the "4K" in `Arcwave 27" 4K Monitor`
#: as the figure 4,000, and a writer quoting it would be flagged for a number
#: no row holds. What a careful writer does instead is name the kind of thing.
_MERIDIAN_LINES = {
    'Arcwave 27" 4K Monitor': "Arcwave 27-inch monitors",
    "Northpeak Thunderbolt 4 Dock": "Northpeak Thunderbolt docks",
    "Keystone Slim Low-Profile Keyboard": "Keystone keyboards",
    "Lumen Dual Monitor Arm": "dual monitor arms",
}


def june_spike(steps: list[Result]) -> str:
    sentences: list[str] = []
    if len(steps) >= 5:
        sentences.append("Most of June's jump came from one unusually large order [5].")

    months = steps[0].dicts()
    by_month = {m["month"][5:7]: m for m in months}
    may, jun, jul = by_month["05"], by_month["06"], by_month["07"]
    check(max(months, key=lambda m: m["revenue"]) is jun, "June is the highest of the six months")
    check(abs(jun["orders"] - may["orders"]) / may["orders"] < 0.05, "June had about as many orders as May")
    check(jun["avg_order_value"] > may["avg_order_value"] * 1.4, "June's average order is far larger")
    sentences.append(
        f"June 2026 brought in {money(jun['revenue'])}, against {money(may['revenue'])} in May and "
        f"{money(jul['revenue'])} in July, from almost the same number of orders ({jun['orders']} "
        f"against {may['orders']} and {jul['orders']}), so the average order rose to "
        f"${jun['avg_order_value']:,.2f} from ${may['avg_order_value']:,.2f} [1]."
    )

    if len(steps) >= 2:
        rows = steps[1].dicts()
        before = {r["segment"]: r["revenue"] for r in rows if r["month"][5:7] == "05"}
        after = {r["segment"]: r["revenue"] for r in rows if r["month"][5:7] == "06"}
        change = sum(after.values()) - sum(before.values())
        share = (after["Enterprise"] - before["Enterprise"]) / change * 100
        check(steps[1].computed is not None and steps[1].computed["ok"], "the contribution computed")
        check(share > 90, "Enterprise carries the increase")
        check(after["SMB"] < before["SMB"], "SMB fell")
        sentences.append(
            f"Revenue rose by {money(change)} ({change / sum(before.values()) * 100:.1f}%) from May to June, "
            f"and the Enterprise segment accounts for {share:.1f}% of that rise on its own, while SMB "
            f"revenue fell to {money(after['SMB'])} from {money(before['SMB'])} [2]."
        )

    if len(steps) >= 3:
        rows = steps[2].dicts()
        first, second = rows[0], rows[1]
        check(first["customer"].startswith("Meridian Health Systems"), "Meridian leads June")
        check(first["revenue"] > second["revenue"] * 5, "Meridian is far above the next customer")
        check(steps[2].computed is not None and "Meridian" in steps[2].computed["summary"],
              "the outlier computation flags Meridian")
        sentences.append(
            f"Of the {len(rows)} Enterprise customers who bought in June, one stands far above the rest: "
            f"Meridian Health Systems, with {money(first['revenue'])} against {money(second['revenue'])} "
            f"for the next, {second['customer']} [3]."
        )

    if len(steps) >= 4:
        lines = steps[3].dicts()
        orders = {line["order_id"] for line in lines}
        check(len(orders) == 1, "Meridian's June revenue is one order")
        check(all(line["product"] in _MERIDIAN_LINES for line in lines), "the order is the four products named")
        placed = date.fromisoformat(lines[0]["order_date"])
        items = [f"{line['quantity']} {_MERIDIAN_LINES[line['product']]}" for line in lines]
        listed = ", ".join(items[:-1]) + f" and {items[-1]}" if len(items) > 1 else items[0]
        sentences.append(f"That was a single order, placed on {placed.day} June, for {listed} [4].")

    if len(steps) >= 5:
        rows = steps[4].dicts()
        june = next(r for r in rows if r["month"][5:7] == "06")
        others = [r["revenue_without_largest_order"] for r in rows if r is not june]
        check(abs(june["largest_order"] - steps[2].dicts()[0]["revenue"]) < 0.01,
              "June's largest order is Meridian's")
        check(june["revenue_without_largest_order"] > max(others), "June still leads without it")
        gap_before = jun["revenue"] - may["revenue"]
        gap_after = june["revenue_without_largest_order"] - by_month_of(rows, "05")["revenue_without_largest_order"]
        check(gap_after < gap_before / 3, "most of the lead came from that order")
        sentences.append(
            f"Take each month's largest order out and June still leads at "
            f"{money(june['revenue_without_largest_order'])}, but by far less: the other five months then sit "
            f"between {money(min(others))} and {money(max(others))} [5]."
        )
    return " ".join(sentences)


def by_month_of(rows: list[dict[str, Any]], month: str) -> dict[str, Any]:
    return next(r for r in rows if r["month"][5:7] == month)


DEEP_QUESTIONS: list[DeepQuestion] = [
    DeepQuestion(
        id="deep-june", connection="sales",
        text="Why was June revenue so much higher than the months around it?",
        aliases=["why was june revenue so high", "why was june so high", "why did revenue spike in june",
                 "why was revenue so high in june", "what happened to revenue in june",
                 "why was june revenue higher than may and july"],
        restatement=(
            "Explain why June 2026 revenue stands so far above May and July: whether more orders or "
            "larger ones drove it, which customer segment and which customers account for the "
            "increase, and how June compares once unusual orders are set aside."
        ),
        stop_when=(
            "The increase over May is traced to specific customers or orders, or shown to be spread "
            "across the business."
        ),
        steps=[
            DeepStep(
                question="What were revenue, order count and average order value in each of the last six complete months?",
                intent="CONFIRM", tool="SQL",
                why="Confirms how far June stands above its neighbours, and whether more orders or bigger ones produced it.",
                sql="""SELECT date_trunc('month', o.order_date)::date AS month,
       SUM(o.total_amount) AS revenue,
       COUNT(*) AS orders,
       ROUND(AVG(o.total_amount), 2) AS avg_order_value
FROM public.orders AS o
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '6 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""",
                chart={"chart_type": "line", "x_axis": {"field": "month", "type": "temporal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
            ),
            DeepStep(
                question="How did revenue by customer segment change from May to June 2026?",
                intent="DECOMPOSE", tool="CONTRIBUTION", depends_on=[0],
                why="Attributes the increase to the segments that moved.",
                sql="""SELECT date_trunc('month', o.order_date)::date AS month,
       c.segment,
       SUM(o.total_amount) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE o.status IN ('completed', 'shipped')
  AND o.order_date >= DATE '2026-05-01'
  AND o.order_date < DATE '2026-07-01'
GROUP BY 1, 2
ORDER BY 1, 2""",
                chart={"chart_type": "bar", "x_axis": {"field": "segment", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}, "series": {"field": "month", "type": "temporal"}},
            ),
            DeepStep(
                question="Which Enterprise customers' revenue in June 2026 stands out?",
                intent="DRILL", tool="OUTLIERS", depends_on=[1],
                why="Enterprise carried the whole increase, so the search narrows to its customers.",
                planned="Which customers' revenue in June 2026 stands out?",
                planned_why="Finds whether a few customers account for the increase.",
                sql="""SELECT c.name AS customer,
       SUM(o.total_amount) AS revenue
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE o.status IN ('completed', 'shipped')
  AND c.segment = 'Enterprise'
  AND o.order_date >= DATE '2026-06-01'
  AND o.order_date < DATE '2026-07-01'
GROUP BY c.name
ORDER BY revenue DESC""",
                chart={"chart_type": "bar", "x_axis": {"field": "customer", "type": "nominal"}, "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
            ),
            DeepStep(
                question="What did Meridian Health Systems Inc. order in June 2026?",
                intent="DRILL", tool="SQL", depends_on=[2],
                why="One customer stands out, so its June orders are listed line by line.",
                planned="What did the customers that stand out order in June 2026?",
                planned_why="Shows whether the increase is one purchase or many.",
                sql="""SELECT o.id AS order_id,
       o.order_date,
       p.name AS product,
       oi.quantity,
       oi.line_total
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.order_items AS oi ON oi.order_id = o.id
JOIN public.products AS p ON p.id = oi.product_id
WHERE o.status IN ('completed', 'shipped')
  AND c.name = 'Meridian Health Systems Inc.'
  AND o.order_date >= DATE '2026-06-01'
  AND o.order_date < DATE '2026-07-01'
ORDER BY oi.line_total DESC""",
                chart={"chart_type": "bar", "x_axis": {"field": "product", "type": "nominal"}, "y_axis": {"field": "line_total", "type": "quantitative", "aggregation": "none"}},
            ),
            DeepStep(
                question="What would each of the last six complete months look like without its single largest order?",
                intent="CHECK", tool="SQL", depends_on=[0],
                why="Removes the biggest order from every month alike, so June is compared on the same terms as the rest.",
                sql="""WITH ranked AS (
  SELECT date_trunc('month', o.order_date)::date AS month,
         o.total_amount,
         ROW_NUMBER() OVER (PARTITION BY date_trunc('month', o.order_date)
                            ORDER BY o.total_amount DESC) AS rank_in_month
  FROM public.orders AS o
  WHERE o.status IN ('completed', 'shipped')
    AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '6 months'
    AND o.order_date < date_trunc('month', CURRENT_DATE)
)
SELECT to_char(month, 'YYYY-MM') AS month,
       SUM(total_amount) AS revenue,
       SUM(total_amount) FILTER (WHERE rank_in_month > 1) AS revenue_without_largest_order,
       MAX(total_amount) AS largest_order
FROM ranked
GROUP BY month
ORDER BY month""",
                # A combo draws bars, so the month is text: on a date axis each
                # bar is a hairline at the first of its month.
                chart={"chart_type": "combo",
                       "x_axis": {"field": "month", "type": "nominal", "label": "Month"},
                       "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none", "label": "Revenue"},
                       "y2_axis": {"field": "revenue_without_largest_order", "type": "quantitative", "aggregation": "none",
                                   "label": "Without its largest order"}},
            ),
        ],
        answer=june_spike,
        shows="A deep analysis: a five-step plan, two steps sharpened as the evidence arrives, and an answer whose every sentence cites the step it came from.",
        followups=["revenue-trend", "top-products", "region-revenue"],
    ),
]


# ── what the knowledge backlog is built from ───────────────────────────────
#: How often each recorded question was asked this month, by the demo's
#: invented colleagues. The backlog turns these into TRAFFIC rows — or FAILED
#: ones, for a question whose recorded run needed repairing.
TRAFFIC: dict[str, int] = {
    "revenue-trend": 14,
    "top-products": 9,
    "order-value-channel": 6,
    "region-revenue": 4,
    "carrier-speed": 3,
    "sakila-categories": 5,
    "sakila-actors": 3,
}

#: Questions that used a word nothing in the connection knows. The words are
#: found by the backlog's own `unknown_words`, never listed here.
UNKNOWN: dict[str, list[tuple[str, int]]] = {
    "sales": [("What is our churn rate by segment?", 3)],
    "sakila": [("Late fees by store", 2)],
}
