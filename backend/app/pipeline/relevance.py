"""Ranking tables on the sentences somebody already wrote about them.

`docs/plans/hybrid-retrieval.md` Phase 1 — mvp2 **B2**, the half of it that
needs no provider and no new column.

Retrieval's `RANKED_MATCH` branch has four signals and every one of them is a
**word the question repeats**: the table's own name, a column's name, a name
the conversation already queried, and — since A5 — a label, synonym, metric
name or glossary term a curator wrote down. A question phrased in none of those
words selects nothing at all, and falls to `fit_to_budget`'s last rule, *"take
the biggest tables". On a wide warehouse that is a coin toss weighted by row
count.

Meanwhile the answer is sitting in the same snapshot. `COMMENT ON TABLE
order_items IS 'one row per line of an order; negative quantities are returns'`
reaches the model today only once `order_items` has already been chosen, which
is exactly backwards. So does every `description` and `grain` in the semantic
layer. **This module reads those sentences and scores them against the
question**, and the score is what `fit_to_budget` ranks the tail on.

Three properties, each load-bearing:

**It reads nothing another tier reads.** Names, labels, synonyms, metric names
and glossary terms belong to `match_tables` and `match_by_terms`; this takes
descriptions, grains, value meanings and glossary *meanings*. A word scored
twice is a word weighted twice for a reason nobody could defend, and the two
kinds of evidence are matched by deliberately different rules — a name has to
be repeated, a sentence is weighed.

**With no comments and no semantic layer, every score is zero.** Which makes
the ranking byte-identical to the one before this existed: the score enters the
sort key ahead of row count, and a constant key changes no order. That is the
*off-by-absence* property CLAUDE.md states for the two curated documents,
extended to the thing that ranks them — and it is what lets one install's
measurements stay comparable to another's.

**The weights are measured, not listed.** A stopword list is wrong per
language and per customer: "order" is noise in an order-management database and
signal in a payroll one. `df` is counted over this connection's own prose, so
the corpus says which words discriminate and nobody has to.
"""
from __future__ import annotations

import math
from typing import Any

from app.pipeline.metadata import qualified, question_tokens

#: Shorter than this is not a word anybody weighed a question with. The floor
#: `semantic.terms` and `metadata._names_for` both apply, for the same reason.
MIN_TOKEN_CHARS = 3

#: The share of a question's own weight a table's prose must carry to count as
#: being *about* it. A five-content-word question needs one of its words; a
#: twenty-word question needs four. Longer questions demand more, which is
#: right: one word in twenty is noise, and the tier this admits a table to
#: ranks above "everything else" — a claim that should cost something.
PROSE_FLOOR = 0.2

#: How many prose-only tables may seed the foreign-key hop. Prose is the
#: weakest of the five signals and ranks below every other one, so a table past
#: the twentieth would have to survive a cut that every named, carried,
#: column-named and bridge table precedes. The cap bounds the *expansion*,
#: never the scoring: the score still orders the whole tail.
PROSE_MAX_SEEDS = 20


def table_text(
    table: dict[str, Any],
    *,
    layer: dict[str, tuple[str, ...]] | None = None,
    include_comments: bool = True,
) -> tuple[str, ...]:
    """Every sentence written about one table, from both places they are kept.

    The DDL comments first — the table's, then its columns' — and then whatever
    `semantic.table_prose` holds for it. Two sources, one bag: a curator's
    description and a DBA's comment are the same kind of evidence and nothing
    downstream needs to tell them apart.

    **`include_comments=False` means the comments are not read at all.** That
    is `database_connections.include_db_comments`, and the rule is deliberately
    blunt: the flag exists so a connection can refuse to send its DDL comments
    to a provider, and a comment that decides *which tables* are sent has
    reached the provider's answer by another road. Ranking is not rendering,
    but a rule with an exception in it is a rule somebody gets wrong in six
    months. Comments off, comments unread — the layer's prose still counts,
    because that is the customer's own document and the flag says nothing about
    it.
    """
    out: list[str] = []
    if include_comments:
        comment = str(table.get("comment") or "").strip()
        if comment:
            out.append(comment)
        for column in table.get("columns") or []:
            text = str(column.get("comment") or "").strip()
            if text:
                out.append(text)
    if layer:
        out.extend(layer.get(qualified(table).lower(), ()))
    return tuple(out)


def rank_tables(
    question: str,
    tables: list[dict[str, Any]],
    *,
    layer: dict[str, tuple[str, ...]] | None = None,
    include_comments: bool = True,
) -> dict[str, float]:
    """How much of the question each table's prose accounts for, in [0, 1].

    Keyed by `metadata.qualified`, and **tables scoring zero are absent** — the
    caller reads it with `.get(key, 0.0)`, and on a snapshot with no prose at
    all the return is `{}` and costs one pass.

    The weight of a token is `ln((N + 1) / df)` over the tables being ranked:
    a word in every table's prose weighs `ln(1 + 1/N)` — about nothing, and
    exactly nothing in the limit — while a word in one weighs about `ln(N)`.
    `df` is counted here rather than looked up because the
    corpus *is* this customer's schema, and that is the only place the question
    "is 'order' a content word?" has a correct answer.

    The score is the matched share of the question's weight, and **the
    denominator counts only question tokens this corpus has ever seen**. A
    question full of words nothing wrote about — "how many", "last month" —
    would otherwise be normalised by a weight no table could ever carry, and
    every score would collapse toward zero for a reason that says nothing about
    any table. What is left is an honest reading: *of the words this schema has
    prose about, how many does this table's prose cover?*

    Deterministic, pure, and no I/O: the caller supplies the layer's sentences
    (`semantic.table_prose`) already parsed, exactly as `match_by_terms` is
    handed `table_terms`.
    """
    asked = {t for t in question_tokens(question) if len(t) >= MIN_TOKEN_CHARS}
    if not asked or not tables:
        return {}

    bags: list[tuple[str, set[str]]] = []
    df: dict[str, int] = {}
    for table in tables:
        bag: set[str] = set()
        for sentence in table_text(table, layer=layer, include_comments=include_comments):
            bag |= {t for t in question_tokens(sentence) if len(t) >= MIN_TOKEN_CHARS}
        bags.append((qualified(table), bag))
        # Only the question's own words can ever score, so the rest of a
        # comment is counted no further than this.
        for token in bag & asked:
            df[token] = df.get(token, 0) + 1

    total = len(tables)
    idf = {token: math.log((total + 1) / count) for token, count in df.items()}
    weight = sum(idf.values())
    if weight <= 0:
        # Every question word this corpus knows is in every table's prose, so
        # none of them tells the tables apart. No score rather than a tie.
        return {}

    scores: dict[str, float] = {}
    for key, bag in bags:
        matched = sum(idf[t] for t in bag & asked)
        if matched:
            scores[key] = matched / weight
    return scores


def prose_seeds(
    tables: list[dict[str, Any]], scores: dict[str, float]
) -> list[dict[str, Any]]:
    """The tables whose prose is about the question, in snapshot order.

    Above `PROSE_FLOOR`, the best `PROSE_MAX_SEEDS` of them, returned in
    snapshot order like every other selector on this path — the cut is by
    score, the order is not, because a caller that wants rank asks `scores`.
    """
    over = [t for t in tables if scores.get(qualified(t), 0.0) >= PROSE_FLOOR]
    if len(over) <= PROSE_MAX_SEEDS:
        return over
    keep = {
        qualified(t)
        for t in sorted(over, key=lambda t: -scores[qualified(t)])[:PROSE_MAX_SEEDS]
    }
    return [t for t in over if qualified(t) in keep]
