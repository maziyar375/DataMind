"""What the demo's colleagues have taught DataMind: saved questions per connection.

A template is a question pattern and the SQL that answers it, with `{slots}` in
the question and `:slots` in the statement. `build.py` puts every one through
the knowledge store's own gate — `validate_template`, the guard's fifth entry
point — against the synced snapshot, so a template shown in the console is one
the product would have accepted, and its referenced tables are the guard's.

Two of the chat's questions are *answered from* a template (`VERIFIED`), so the
Knowledge screen is not a list that does nothing: asking "Monthly revenue for
Europe" on the Sales warehouse short-circuits five nodes and lands on the guard,
with the Verified badge and the bound value shown. Every other scripted
question's `match` step reports the real best score against this store —
computed by the matcher's own `score_against` — and the build fails if one of
them would in fact have matched, because its recorded run would then be a lie.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from questions import Result, check, money, month_name


@dataclass
class Template:
    id: str
    connection: str
    question: str
    sql: str
    params: list[dict[str, str]] = field(default_factory=list)
    note: str = ""
    source: str = "MANUAL"               # MANUAL | CHAT_CONFIRMED | CHAT_CORRECTED | TILE | REPORT_BLOCK
    role: str = "RETRIEVABLE"
    author: str = "Leila Karimi"
    hit_count: int = 0
    last_hit_days: float | None = None
    created_days: float = 60
    verified_days: float | None = None


@dataclass
class VerifiedQuestion:
    """A chat question the store answers: the template's SQL, bound and run."""
    id: str
    template: str
    connection: str
    text: str
    narrative: Callable[[Result], str]
    chart: dict[str, Any] | None = None
    followups: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


BOOKED = "o.status IN ('completed', 'shipped')"

REGIONS = "Africa, Asia Pacific, Europe, Latin America, Middle East, Nordics, North America"
CARRIERS = "Aramex, DHL, FedEx, Local Courier, UPS, USPS"
WAREHOUSES = "Columbus DC, Dubai DC, Gothenburg DC, Reno DC, Singapore DC, Sydney DC, São Paulo DC, Venlo DC"
SAKILA_CATEGORIES = (
    "Action, Animation, Children, Classics, Comedy, Documentary, Drama, Family, Foreign, Games, "
    "Horror, Music, New, Sci-Fi, Sports, Travel"
)

TEMPLATES: list[Template] = [
    # ── Sales warehouse ──
    Template(
        id="t-sales-region-month", connection="sales",
        question="Monthly revenue for {region}",
        sql=f"""SELECT date_trunc('month', o.order_date)::date AS month,
       ROUND(SUM(o.total_amount), 2) AS revenue,
       COUNT(*) AS orders
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
JOIN public.regions AS r ON r.id = c.region_id
WHERE {BOOKED}
  AND r.name = :region
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY 1
ORDER BY 1""",
        params=[{"name": "region", "type": "string", "comment": f"The customer's region, one of: {REGIONS}"}],
        note="Revenue is booked orders only (completed and shipped), by the customer's region — not the warehouse's. Twelve complete months.",
        source="CHAT_CORRECTED", author="Priya Nair", hit_count=41, last_hit_days=0.4, created_days=96, verified_days=96,
    ),
    Template(
        id="t-sales-top-customers", connection="sales",
        question="Top 10 customers by revenue this year",
        sql=f"""SELECT c.name AS customer,
       c.segment,
       ROUND(SUM(o.total_amount), 2) AS revenue,
       COUNT(*) AS orders
FROM public.orders AS o
JOIN public.customers AS c ON c.id = o.customer_id
WHERE {BOOKED}
  AND NOT c.is_deleted
  AND o.order_date >= date_trunc('year', CURRENT_DATE)
GROUP BY c.name, c.segment
ORDER BY revenue DESC
LIMIT 10""",
        note="Soft-deleted customers are excluded, as the column comment asks.",
        source="CHAT_CONFIRMED", author="Tomás Álvarez", hit_count=27, last_hit_days=1.2, created_days=74, verified_days=74,
    ),
    Template(
        id="t-sales-carrier-days", connection="sales",
        question="Average delivery time for {carrier}",
        sql="""SELECT ca.name AS carrier,
       COUNT(*) AS deliveries,
       ROUND(AVG(EXTRACT(EPOCH FROM (s.delivered_at - s.shipped_at)) / 86400)::numeric, 1) AS avg_days
FROM public.shipments AS s
JOIN public.carriers AS ca ON ca.id = s.carrier_id
WHERE s.delivered_at IS NOT NULL
  AND ca.name = :carrier
  AND s.shipped_at >= CURRENT_DATE - INTERVAL '6 months'
GROUP BY ca.name""",
        params=[{"name": "carrier", "type": "string", "comment": f"one of: {CARRIERS}"}],
        note="Door to door: shipped_at to delivered_at, over the last six months. Undelivered shipments are left out, not counted as late.",
        author="Leila Karimi", hit_count=12, last_hit_days=3.5, created_days=58, verified_days=41,
    ),
    Template(
        id="t-sales-reorder", connection="sales",
        question="Which products are below their reorder level in {warehouse}?",
        sql="""SELECT p.name AS product,
       i.quantity AS on_hand,
       i.reorder_level
FROM public.inventory AS i
JOIN public.products AS p ON p.id = i.product_id
JOIN public.warehouses AS w ON w.id = i.warehouse_id
WHERE w.name = :warehouse
  AND i.quantity <= i.reorder_level
  AND p.active
ORDER BY i.quantity - i.reorder_level, p.name""",
        params=[{"name": "warehouse", "type": "string", "comment": f"one of: {WAREHOUSES}"}],
        note="At or below the reorder level counts as below: the level is the point to reorder at.",
        source="TILE", author="Tomás Álvarez", hit_count=9, last_hit_days=6, created_days=33, verified_days=33,
    ),
    Template(
        id="t-sales-segment-count", connection="sales",
        question="How many active customers are in the {segment} segment?",
        sql="""SELECT COUNT(*) AS customers
FROM public.customers AS c
WHERE NOT c.is_deleted
  AND c.segment = :segment""",
        params=[{"name": "segment", "type": "string", "comment": "one of: SMB, Mid-Market, Enterprise"}],
        note="Active means not soft-deleted — this schema has no separate status column for customers.",
        author="Leila Karimi", hit_count=6, last_hit_days=12, created_days=45, verified_days=45,
    ),
    Template(
        id="t-sales-channel-last-month", connection="sales",
        question="Orders and revenue by channel last month",
        sql=f"""SELECT o.channel,
       COUNT(*) AS orders,
       ROUND(SUM(o.total_amount), 2) AS revenue
FROM public.orders AS o
WHERE {BOOKED}
  AND o.order_date >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month'
  AND o.order_date < date_trunc('month', CURRENT_DATE)
GROUP BY o.channel
ORDER BY revenue DESC""",
        note="Kept as a benchmark question: it is how we check a new model still reads 'last month' as the previous calendar month.",
        role="BENCHMARK_ONLY", author="Priya Nair", hit_count=0, created_days=20, verified_days=20,
    ),
    # ── Sakila DVD rental ──
    Template(
        id="t-sakila-category-films", connection="sakila",
        question="Films in the {category} category",
        sql="""SELECT f.title,
       f.rating,
       f.rental_rate,
       f.length
FROM sakila.film AS f
JOIN sakila.film_category AS fc ON fc.film_id = f.film_id
JOIN sakila.category AS c ON c.category_id = fc.category_id
WHERE c.name = :category
ORDER BY f.title""",
        params=[{"name": "category", "type": "string", "comment": f"one of: {SAKILA_CATEGORIES}"}],
        note="Through film_category: a film's category is not a column on film.",
        author="Priya Nair", hit_count=18, last_hit_days=2.2, created_days=88, verified_days=88,
    ),
    Template(
        id="t-sakila-top-renters", connection="sakila",
        question="Top 10 customers by number of rentals",
        sql="""SELECT CONCAT(cu.first_name, ' ', cu.last_name) AS customer,
       COUNT(*) AS rentals
FROM sakila.rental AS r
JOIN sakila.customer AS cu ON cu.customer_id = r.customer_id
GROUP BY cu.customer_id, cu.first_name, cu.last_name
ORDER BY rentals DESC, customer
LIMIT 10""",
        source="CHAT_CONFIRMED", author="Priya Nair", hit_count=7, last_hit_days=9, created_days=64, verified_days=64,
    ),
    Template(
        id="t-sakila-store-month", connection="sakila",
        question="Rentals per month for store {store_id}",
        sql="""SELECT SUBSTRING(r.rental_date, 1, 7) AS month,
       COUNT(*) AS rentals
FROM sakila.rental AS r
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
WHERE i.store_id = :store_id
GROUP BY month
ORDER BY month""",
        params=[{"name": "store_id", "type": "number", "comment": "The store number: 1 or 2."}],
        note="The store is the inventory copy's store, not the staff member's.",
        author="Leila Karimi", hit_count=4, last_hit_days=16, created_days=51, verified_days=51,
    ),
    Template(
        id="t-sakila-unreturned", connection="sakila",
        question="Which rentals were never returned?",
        sql="""SELECT r.rental_id,
       r.rental_date,
       f.title,
       CONCAT(cu.first_name, ' ', cu.last_name) AS customer
FROM sakila.rental AS r
JOIN sakila.inventory AS i ON i.inventory_id = r.inventory_id
JOIN sakila.film AS f ON f.film_id = i.film_id
JOIN sakila.customer AS cu ON cu.customer_id = r.customer_id
WHERE r.return_date IS NULL
ORDER BY r.rental_date""",
        note="return_date is NULL for a rental still out; there is no status column.",
        author="Leila Karimi", hit_count=0, created_days=80, verified_days=80,
    ),
]


# ── the chat questions the store answers ───────────────────────────────────
def europe_monthly(r: Result) -> str:
    rows = r.dicts()
    check(len(rows) == 12, "twelve complete months")
    total = sum(x["revenue"] for x in rows)
    peak = max(rows, key=lambda x: x["revenue"])
    low = min(rows, key=lambda x: x["revenue"])
    first, last = rows[0], rows[-1]
    # Europe has no June spike: the order behind it was a North American customer's.
    june = next(x for x in rows if x["month"][5:7] == "06")
    check(june is not peak, "June is not Europe's best month")
    return (
        f"Europe brought in {money(total)} over the last 12 complete months, from "
        f"{sum(x['orders'] for x in rows):,} orders. Its best month was {month_name(peak['month'])} at "
        f"{money(peak['revenue'])} and its quietest {month_name(low['month'])} at {money(low['revenue'])}; "
        f"it went from {money(first['revenue'])} in {month_name(first['month'])} to "
        f"{money(last['revenue'])} in {month_name(last['month'])}. This is the saved question "
        f"“Monthly revenue for {{region}}” with Europe filled in, so it counts booked orders by the "
        f"customer's region, exactly as the analytics team defined it."
    )


def comedy_films(r: Result) -> str:
    # AGGREGATE: the model sees the shape of the result, never a value in it.
    check(r.columns == ["title", "rating", "rental_rate", "length"], "the saved question's columns")
    return (
        f"The saved question returned **{len(r.rows)} films** in the Comedy category, one row per film "
        f"with its rating, rental rate and length, in title order. This connection shares only the "
        f"shape of a result with the model, so the titles and figures are in the table below rather "
        f"than in this sentence."
    )


VERIFIED: list[VerifiedQuestion] = [
    VerifiedQuestion(
        id="verified-europe", template="t-sales-region-month", connection="sales",
        text="Monthly revenue for Europe",
        aliases=["monthly revenue for europe", "monthly revenue in europe"],
        narrative=europe_monthly,
        chart={"chart_type": "line", "x_axis": {"field": "month", "type": "temporal"},
               "y_axis": {"field": "revenue", "type": "quantitative", "aggregation": "none"}},
        followups=["region-revenue", "revenue-trend", "top-products"],
    ),
    VerifiedQuestion(
        id="verified-comedy", template="t-sakila-category-films", connection="sakila",
        text="Films in the Comedy category",
        aliases=["comedy films", "films in the comedy category"],
        narrative=comedy_films,
        chart=None,
        followups=["sakila-categories", "sakila-actors", "sakila-tables"],
    ),
]
