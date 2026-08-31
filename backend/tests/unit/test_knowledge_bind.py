"""Filling a template's slots from the question — and refusing to guess.

**The rule this file is mostly about: any parameter that cannot be bound
cancels the short-circuit.** The cost of refusing is today's behaviour, and the
run generates SQL as it always did. The cost of binding badly is an answer that
wears a Verified badge and is wrong, which is the failure class this product
exists to avoid. So roughly half the tests below assert that the binder said
*no*.

The clock is passed in, never read, so *"last month"* asked at 23:59 on the
31st resolves the same however long the queue was.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.knowledge import ParamType, TemplateParam
from app.knowledge.bind import DateRange, bind_params, bind_sql

# A Monday, in the middle of Q3, so every branch of the grammar has a
# distinguishable answer.
NOW = datetime(2026, 8, 31, 12, 0, 0)


def date_param(name: str = "from_date") -> TemplateParam:
    return TemplateParam(name=name, type=ParamType.DATE)


def region() -> TemplateParam:
    return TemplateParam(
        name="region", type=ParamType.STRING, comment="one of: EMEA, NA, APAC"
    )


def _window(question: str) -> DateRange | None:
    binding = bind_params(
        question, [date_param("from_date"), date_param("to_date")], now=NOW
    )
    if not binding.bound:
        return None
    return DateRange(binding.values["from_date"], binding.values["to_date"])


# ── the date grammar ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "question,start,end",
    [
        ("revenue last month", date(2026, 7, 1), date(2026, 8, 1)),
        ("revenue this month", date(2026, 8, 1), date(2026, 9, 1)),
        ("revenue last year", date(2025, 1, 1), date(2026, 1, 1)),
        ("revenue this year", date(2026, 1, 1), date(2027, 1, 1)),
        ("revenue in 2025", date(2025, 1, 1), date(2026, 1, 1)),
        ("revenue in July", date(2026, 7, 1), date(2026, 8, 1)),
        ("revenue in July 2024", date(2024, 7, 1), date(2024, 8, 1)),
        ("revenue in Q3", date(2026, 7, 1), date(2026, 10, 1)),
        ("revenue in Q1 2025", date(2025, 1, 1), date(2025, 4, 1)),
        ("revenue since 2026-03-15", date(2026, 3, 15), date(2026, 9, 1)),
        ("revenue from 2026-01-01 to 2026-04-01", date(2026, 1, 1), date(2026, 4, 1)),
        ("revenue yesterday", date(2026, 8, 30), date(2026, 8, 31)),
        ("revenue today", date(2026, 8, 31), date(2026, 9, 1)),
    ],
)
def test_the_phrases_people_actually_type(
    question: str, start: date, end: date
) -> None:
    assert _window(question) == DateRange(start, end)


@pytest.mark.parametrize(
    "question,start",
    [
        ("revenue last 12 months", date(2025, 8, 31)),
        ("revenue in the last 7 days", date(2026, 8, 25)),
        ("revenue over the past 2 weeks", date(2026, 8, 17)),
        ("revenue in the previous 2 quarters", date(2026, 2, 28)),
        ("revenue over the last 3 years", date(2023, 8, 31)),
    ],
)
def test_last_n_units_beats_the_bare_numeral_inside_it(
    question: str, start: date
) -> None:
    # Ordered most-specific-first for exactly this reason: "last 3 months" read
    # as the numeral 3 would bind a threshold instead of a window.
    window = _window(question)
    assert window is not None and window.start == start


def test_a_range_is_half_open() -> None:
    """`start <= x < end`, and the end of "last month" is the 1st of this one.

    Closed ranges need "the last instant of the month", which is a different
    value on a `date` column and a `timestamp` one — and getting it wrong
    silently drops or double-counts a day's rows, in an answer wearing a
    Verified badge.
    """
    assert _window("revenue last month") == DateRange(date(2026, 7, 1), date(2026, 8, 1))


def test_an_iso_date_is_not_read_as_the_year_inside_it() -> None:
    window = _window("revenue since 2026-03-15")
    assert window is not None and window.start == date(2026, 3, 15)


def test_february_in_a_leap_year_survives_the_month_arithmetic() -> None:
    """31 March minus one month is 29 February, not the 31st of it.

    "last month" is a calendar month and lands on the 1st either way; it is the
    *rolling* form that has to clamp, and clamping wrongly here would silently
    move a window by two days once a year.
    """
    rolling = bind_params(
        "revenue in the last 1 month", [date_param()], now=datetime(2024, 3, 31, 9, 0)
    )
    assert rolling.values["from_date"] == date(2024, 2, 29)

    calendar_month = bind_params(
        "revenue last month", [date_param()], now=datetime(2024, 3, 31, 9, 0)
    )
    assert calendar_month.values["from_date"] == date(2024, 2, 1)


def test_the_clock_is_the_run_s_own_not_the_machine_s() -> None:
    early = bind_params("revenue last month", [date_param()], now=datetime(2026, 1, 5))
    assert early.values["from_date"] == date(2025, 12, 1)


def test_a_question_with_no_time_in_it_binds_no_date() -> None:
    binding = bind_params("total revenue", [date_param()], now=NOW)
    assert not binding.bound and binding.missing == ["from_date"]


def test_one_phrase_fills_a_pair_of_slots() -> None:
    # Which is what the AST proposed them as: a `BETWEEN` or a pair of
    # comparisons is one window in the question, not two.
    binding = bind_params(
        "revenue last month", [date_param("from_date"), date_param("to_date")], now=NOW
    )
    assert binding.bound and binding.window == "last month"


def test_a_lone_date_slot_takes_the_start_of_the_window() -> None:
    binding = bind_params("revenue in July", [date_param("order_month")], now=NOW)
    assert binding.values["order_month"] == date(2026, 7, 1)


# ── strings ──────────────────────────────────────────────────────────────
def test_a_declared_value_is_matched_case_insensitively() -> None:
    for question in ("revenue for EMEA", "revenue for emea", "revenue for Emea"):
        assert bind_params(question, [region()], now=NOW).values == {"region": "EMEA"}


def test_a_declared_value_is_matched_as_a_whole_word() -> None:
    # `NA` inside "national" is not the region NA. Masking it would be the
    # binder inventing a value rather than reading one.
    binding = bind_params("national revenue", [region()], now=NOW)
    assert not binding.bound


def test_a_parameter_with_no_vocabulary_refuses_to_pick_a_noun() -> None:
    """The most important refusal in the file.

    A `:region` with no declared values cannot be filled by choosing a word out
    of a sentence. That guess is precisely the one that produces a confident
    wrong answer, and the `REJECTED_UNBOUND` log is how we discover this
    template needed a value list.
    """
    bare = TemplateParam(name="region", type=ParamType.STRING)
    assert not bind_params("revenue for the EMEA region", [bare], now=NOW).bound


def test_an_explicitly_quoted_value_is_taken_at_the_asker_s_word() -> None:
    bare = TemplateParam(name="region", type=ParamType.STRING)
    assert bind_params('revenue for "EMEA"', [bare], now=NOW).values == {
        "region": "EMEA"
    }


def test_two_quoted_values_and_one_slot_is_refused() -> None:
    bare = TemplateParam(name="region", type=ParamType.STRING)
    assert not bind_params("'EMEA' versus 'NA'", [bare], now=NOW).bound


# ── numbers ──────────────────────────────────────────────────────────────
def test_one_numeral_binds() -> None:
    param = TemplateParam(name="threshold", type=ParamType.NUMBER)
    assert bind_params("orders over 10000", [param], now=NOW).values == {
        "threshold": 10000
    }


def test_a_thousands_separator_and_a_decimal_are_read() -> None:
    param = TemplateParam(name="threshold", type=ParamType.NUMBER)
    assert bind_params("orders over 10,500.25", [param], now=NOW).values == {
        "threshold": 10500.25
    }


def test_two_numerals_and_one_slot_is_a_coin_toss_and_is_refused() -> None:
    param = TemplateParam(name="threshold", type=ParamType.NUMBER)
    assert not bind_params("top 5 stores over 10000", [param], now=NOW).bound


def test_a_year_belongs_to_the_date_slot_and_not_to_the_number_slot() -> None:
    # Without this the perfectly unambiguous "revenue over 10000 in 2026"
    # offers the number binder two numerals and it refuses.
    params = [TemplateParam(name="threshold", type=ParamType.NUMBER), date_param()]
    binding = bind_params("revenue over 10000 in 2026", params, now=NOW)
    assert binding.bound
    assert binding.values["threshold"] == 10000
    assert binding.values["from_date"] == date(2026, 1, 1)


# ── booleans, and the cancel rule ────────────────────────────────────────
def test_a_boolean_reads_a_clear_yes_or_no_and_nothing_else() -> None:
    param = TemplateParam(name="is_gift", type=ParamType.BOOLEAN)
    assert bind_params("gift orders only", [param], now=NOW).values == {"is_gift": True}
    assert bind_params("orders without gifts", [param], now=NOW).values == {
        "is_gift": False
    }
    assert not bind_params("all orders", [param], now=NOW).bound


def test_one_unbound_parameter_cancels_the_whole_binding() -> None:
    """Not "bind what you can" — a half-bound template is a wrong answer."""
    binding = bind_params("revenue last month", [date_param(), region()], now=NOW)
    assert not binding.bound
    assert binding.missing == ["region"]
    # The ones that *did* bind are still reported, because the log line that
    # says which slot failed is how we learn what to teach the binder next.
    assert "from_date" in binding.values


def test_a_template_with_no_parameters_binds_trivially() -> None:
    assert bind_params("total revenue", [], now=NOW).bound


# ── substitution ─────────────────────────────────────────────────────────
SQL = (
    "SELECT SUM(amount) FROM orders "
    "WHERE created_at >= :from_date AND created_at < :to_date AND region = :region"
)


def test_every_slot_is_replaced_by_a_literal() -> None:
    out = bind_sql(
        SQL,
        {"from_date": date(2026, 7, 1), "to_date": date(2026, 8, 1), "region": "EMEA"},
    )
    assert out is not None
    assert ":from_date" not in out and ":region" not in out
    assert "'2026-07-01'" in out and "'EMEA'" in out


def test_a_missing_value_produces_nothing_rather_than_a_hole() -> None:
    # A statement with a placeholder still in it would reach the guard, be
    # rewritten into a driver's binding syntax, and run against no value.
    assert bind_sql(SQL, {"from_date": date(2026, 7, 1)}) is None


def test_unparseable_sql_produces_nothing_rather_than_raising() -> None:
    assert bind_sql("SELECT FROM WHERE :x", {"x": 1}) is None


def test_a_value_cannot_become_sql() -> None:
    """There is no string formatting in the binder, so there is no injection.

    A value replaces an AST node and renders as a literal, whatever it
    contains. The result still goes through `guard()` before anything runs it —
    this test is about the layer *before* that, because a defence that only
    works because something downstream catches it is not a defence.
    """
    out = bind_sql(SQL, {
        "from_date": date(2026, 7, 1),
        "to_date": date(2026, 8, 1),
        "region": "EMEA'; DROP TABLE orders; --",
    })
    assert out is not None
    assert "DROP TABLE" in out          # it is *in* the statement…
    assert out.count("'") % 2 == 0      # …as one quoted string, not as SQL
    import sqlglot
    from sqlglot import expressions as exp

    tree = sqlglot.parse_one(out, read="postgres")
    literals = [n.this for n in tree.find_all(exp.Literal) if n.is_string]
    assert "EMEA'; DROP TABLE orders; --" in literals


def test_a_stored_template_binds_whether_it_holds_a_var_or_a_placeholder() -> None:
    """The two spellings a `:slot` can have in the tree.

    `parameterize` writes `exp.Var(':region')`; re-parsing the stored text
    yields `exp.Placeholder`. A template saved before or after any round trip
    has to bind identically, or the store would work until someone edited it.
    """
    from app.knowledge.params import parameterize

    rewritten, _ = parameterize(
        "SELECT id FROM orders WHERE region = 'EMEA'",
        {"region"},
        tables=[{"schema": "public", "name": "orders",
                 "columns": [{"name": "id", "data_type": "bigint"},
                             {"name": "region", "data_type": "text"}]}],
    )
    assert bind_sql(rewritten, {"region": "NA"}) == (
        "SELECT id FROM orders WHERE region = 'NA'"
    )
