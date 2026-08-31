"""What to teach next — the ranking, and the vocabulary gap.

Research L6: *the hardest part of curation is not writing the template, it is
knowing which template to write.* The system already knows, and this is the
reasoning over that evidence — pure, so it can be argued with in a test rather
than inspected in a database.

The last rank source is the one worth reading carefully. **Words the retrieval
did not recognise** is Power BI's *Review questions*, it is the one idea in the
research nobody else has copied, and it is nearly free here because the
semantic layer already holds the vocabulary to compare against. Most of the
tests below are about what it must *not* flag: a false "nothing here is called
that" on every question would bury the real ones on the first day.
"""
from __future__ import annotations

import pytest

from app.knowledge import (
    RANK,
    Suggestion,
    SuggestionKind,
    build_vocabulary,
    rank_suggestions,
    unknown_words,
)
from app.knowledge.backlog import (
    backfill_reason,
    failed_reason,
    flagged_reason,
    traffic_reason,
    unknown_reason,
)

TABLES = [
    {
        "schema": "sales",
        "name": "order_items",
        "comment": "One row per line item. Refunds are negative.",
        "columns": [
            {"name": "id", "data_type": "bigint"},
            {"name": "unit_price", "data_type": "numeric"},
            {"name": "store_id", "data_type": "bigint",
             "comment": "the shop that fulfilled it"},
        ],
    },
    {
        "schema": "sales",
        "name": "customers",
        "columns": [{"name": "region", "data_type": "text"}],
    },
]

LAYER = {
    "entities": [
        {
            "table": "sales.order_items",
            "business_name": "Basket lines",
            "synonyms": ["cart lines"],
            "columns": [{"name": "unit_price", "business_name": "list price"}],
            "metrics": [
                {"name": "revenue", "business_name": "Net revenue",
                 "synonyms": ["takings"]}
            ],
        }
    ],
    "glossary": [{"term": "churn", "synonyms": ["attrition"]}],
}


def suggestion(kind: SuggestionKind, count: int = 1) -> Suggestion:
    return Suggestion(kind=kind, question=f"q-{kind}-{count}", count=count)


# ── the ranking ──────────────────────────────────────────────────────────
def test_the_order_is_flagged_backfill_traffic_failed_words() -> None:
    """Each kind outranks the next for a stated reason.

    A flag is a person's time already spent. A backfill is a verified pair that
    already exists and nothing reads. Traffic is demand. A failure is demand
    plus a known defect. An unrecognised word is the least *actionable* of the
    five — it names a word, not a question, and the fix is often a synonym.
    """
    shuffled = [suggestion(k) for k in reversed(RANK)]
    assert [s.kind for s in rank_suggestions(shuffled)] == list(RANK)


def test_within_a_kind_the_busiest_question_comes_first() -> None:
    items = [
        suggestion(SuggestionKind.TRAFFIC, 2),
        suggestion(SuggestionKind.TRAFFIC, 9),
        suggestion(SuggestionKind.TRAFFIC, 5),
    ]
    assert [s.count for s in rank_suggestions(items)] == [9, 5, 2]


def test_a_flag_outranks_heavy_traffic() -> None:
    # Somebody's time is already spent on the flag, and ignoring it is exactly
    # how a feedback control becomes a suggestion box.
    items = [
        suggestion(SuggestionKind.TRAFFIC, 99),
        suggestion(SuggestionKind.FLAGGED, 1),
    ]
    assert rank_suggestions(items)[0].kind is SuggestionKind.FLAGGED


def test_the_backlog_is_finite() -> None:
    # A backlog that scrolls is one nobody finishes, and the point of the
    # screen is that "what should I do next" has an answer.
    many = [suggestion(SuggestionKind.TRAFFIC, n) for n in range(100)]
    assert len(rank_suggestions(many, limit=12)) == 12


def test_an_empty_backlog_is_an_empty_list_not_an_error() -> None:
    assert rank_suggestions([]) == []


# ── the vocabulary ───────────────────────────────────────────────────────
def test_physical_names_are_split_into_words() -> None:
    # `order_items` has to recognise a question that says "order items", or
    # every question about the busiest table in the schema reads as a gap.
    vocabulary = build_vocabulary(TABLES)
    assert {"order", "items", "customers", "unit", "price"} <= vocabulary


def test_catalog_comments_are_vocabulary_too() -> None:
    # A DBA's `COMMENT ON` is documentation somebody wrote about this schema.
    # Ignoring it would flag the very words they chose to explain it with.
    assert "refunds" in build_vocabulary(TABLES)
    assert "shop" in build_vocabulary(TABLES)


def test_the_semantic_layer_contributes_names_synonyms_and_the_glossary() -> None:
    vocabulary = build_vocabulary(TABLES, LAYER)
    for word in ("basket", "cart", "revenue", "takings", "churn", "attrition"):
        assert word in vocabulary, word


def test_a_connection_with_nothing_synced_has_no_vocabulary() -> None:
    assert build_vocabulary([], None) == set()


# ── the gap, and what it must not flag ───────────────────────────────────
def test_a_word_nothing_recognises_is_the_signal() -> None:
    assert unknown_words("how many customers churned", build_vocabulary(TABLES)) == [
        "churned"
    ]


def test_the_glossary_closes_the_gap_it_was_written_to_close() -> None:
    # The whole point of the feature: a curator adds a glossary term and the
    # backlog row disappears. If it did not, the row would be an accusation
    # rather than a task.
    assert unknown_words("how many customers churned", build_vocabulary(TABLES, LAYER)) == []


@pytest.mark.parametrize(
    "question",
    [
        "revenue last month",
        "total revenue by region last quarter",
        "show me the top 10 stores this year",
        "what was the average unit price yesterday",
        "compare revenue growth versus last year",
    ],
)
def test_time_and_aggregation_words_are_never_a_gap(question: str) -> None:
    """Every question says *when* and *how much*.

    Flagging `last`, `month`, `total` and `average` would put the same four
    words at the top of every backlog forever, which is how a signal becomes
    furniture.
    """
    vocabulary = build_vocabulary(TABLES, LAYER)
    assert unknown_words(question, vocabulary) == []


def test_a_verb_form_matches_the_noun_somebody_named() -> None:
    # A curator writes a glossary term `churn`; the question says "churned".
    # Folding a couple of endings at *lookup* time can only ever mark a word
    # known, which is the fail-safe direction — a false "known" costs one
    # backlog row, a false "unknown" costs noise on every question.
    vocabulary = build_vocabulary(TABLES, LAYER)
    assert unknown_words("customers who churned", vocabulary) == []
    assert unknown_words("customers who are churning", vocabulary) == []
    # …and the guards mean a short word is never mangled into a match.
    assert unknown_words("network speed", build_vocabulary(TABLES)) == [
        "network", "speed"
    ]


def test_a_plural_matches_its_singular() -> None:
    # `customers` and `customer_id` are the same word to everybody except a set
    # difference.
    vocabulary = build_vocabulary(
        [{"schema": "s", "name": "t", "columns": [{"name": "customer_id"}]}]
    )
    assert unknown_words("how many customers", vocabulary) == []
    # And an unknown word comes back **as it was typed** — the reason line
    # quotes it to the curator, and quoting a stem would read like a typo.
    assert unknown_words("company policies", vocabulary) == ["company", "policies"]


def test_a_masked_literal_is_a_value_not_a_word() -> None:
    # `top 10 stores` masks the 10; a value is not vocabulary, and flagging the
    # mask would flag every question with a number in it.
    vocabulary = build_vocabulary(TABLES)
    assert "*" not in unknown_words("top 10 order items", vocabulary)


def test_very_short_tokens_are_noise() -> None:
    assert unknown_words("q3 vs q4 by x", build_vocabulary(TABLES)) == []


def test_the_words_come_back_in_the_order_they_were_used_without_duplicates() -> None:
    vocabulary = build_vocabulary(TABLES)
    assert unknown_words("churn and churn and attrition", vocabulary) == [
        "churn", "attrition"
    ]


# ── the copy the rows carry ──────────────────────────────────────────────
def test_the_reason_lines_read_like_sentences_a_person_wrote() -> None:
    # Written once, here, so three developers do not invent three voices.
    assert traffic_reason(1) == "Asked once this month, never matched"
    assert traffic_reason(9) == "Asked 9× this month, never matched"
    assert failed_reason(3, 0) == "Failed 3×"
    assert failed_reason(0, 2) == "Needed repairing 2×"
    assert failed_reason(3, 2) == "Failed 3×, repaired 2×"
    assert backfill_reason("TILE") == "From a dashboard tile you corrected"
    assert backfill_reason("REPORT_BLOCK") == "From a report block you corrected"


def test_a_flag_with_no_comment_still_says_something_true() -> None:
    assert flagged_reason("   ") == "Flagged as wrong"
    assert flagged_reason("this double-counts refunds") == "this double-counts refunds"


def test_a_long_list_of_unknown_words_is_summarised_rather_than_dumped() -> None:
    reason = unknown_reason(["churn", "cohort", "arpu", "ltv", "mrr"])
    assert reason.startswith("Nothing here is called “churn”, “cohort”, “arpu”")
    assert reason.endswith("and 2 more")
