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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.embed import cosine, fingerprint
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


def prose_by_table(
    tables: list[dict[str, Any]],
    *,
    layer: dict[str, tuple[str, ...]] | None = None,
    include_comments: bool = True,
) -> dict[str, tuple[str, ...]]:
    """`table_text` for every table, keyed by `qualified`.

    The bag is wanted twice on the vector path — once to score and once to
    recompute the fingerprint a stored vector is checked against — and building
    it twice would be building it twice.
    """
    return {
        qualified(table): table_text(
            table, layer=layer, include_comments=include_comments
        )
        for table in tables
    }


def rank_tables(
    question: str,
    tables: list[dict[str, Any]],
    *,
    layer: dict[str, tuple[str, ...]] | None = None,
    include_comments: bool = True,
    texts: dict[str, tuple[str, ...]] | None = None,
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
    handed `table_terms`. `texts` lets a caller that already built the bags —
    the vector path does, for the fingerprint check — hand them over instead of
    having them built again; it must be `prose_by_table`'s own output or the
    two halves of the score stop describing one document.
    """
    asked = {t for t in question_tokens(question) if len(t) >= MIN_TOKEN_CHARS}
    if not asked or not tables:
        return {}

    prose = (
        texts
        if texts is not None
        else prose_by_table(tables, layer=layer, include_comments=include_comments)
    )
    bags: list[tuple[str, set[str]]] = []
    df: dict[str, int] = {}
    for table in tables:
        key = qualified(table)
        bag: set[str] = set()
        for sentence in prose.get(key, ()):
            bag |= {t for t in question_tokens(sentence) if len(t) >= MIN_TOKEN_CHARS}
        bags.append((key, bag))
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


# ── vectors (Phase 2) ────────────────────────────────────────────────────
#
# The same bag, embedded. Phase 1 asks whether a table's prose uses the
# question's words; this asks whether it *means* what the question means, which
# is the one thing no lexical score can do. Everything here is still pure — the
# store, the provider and the timeout live in `app/services/retrieval_index.py`
# and in the node.

#: Cosine below this is not evidence. Two unrelated English sentences score
#: around 0.3 on most embedding models, so the bottom of the range carries no
#: information and a floor near zero would filter nothing. The same constant
#: and the same argument as `knowledge.embed.SIMILARITY_FLOOR`; kept separate
#: because these are sentences about tables and those are questions, and one
#: number moving for one of them must not move the other.
VECTOR_FLOOR = 0.5


def rescale(similarity: float) -> float:
    """A cosine in `[VECTOR_FLOOR, 1]`, read as a share in `[0, 1]`.

    So a vector hit and a lexical hit are on one scale and `blend` can compare
    them without a weight nobody could derive. Below the floor is 0.0 — not
    "slightly relevant", because at that end of the range the number is noise.
    """
    if similarity <= VECTOR_FLOOR:
        return 0.0
    return min(1.0, (similarity - VECTOR_FLOOR) / (1.0 - VECTOR_FLOOR))


def blend(lexical: dict[str, float], cosines: dict[str, float]) -> dict[str, float]:
    """One score per table: whichever kind of evidence is stronger.

    **`max`, not a weighted sum**, and this is the one number in Phase 2 worth
    arguing about. A weighted sum needs a weight, and the only honest way to
    choose one is the measurement this feature still owes — so a sum would be
    two unfalsifiable claims instead of one. `max` says *either kind of
    evidence is enough*, which is what a **recall** step is for: precision is
    the budget cut's job, not the scorer's.

    It also keeps Phase 1 whole. A table the words already found cannot be
    demoted by a vector that disagrees, so turning embeddings on can add a
    table to the block and never remove one — which is what makes the arm a
    reader can interpret.
    """
    if not cosines:
        return lexical
    out = dict(lexical)
    for key, similarity in cosines.items():
        scaled = rescale(similarity)
        if scaled > out.get(key, 0.0):
            out[key] = scaled
    return out


#: `(texts) -> vectors`. Batched, and the same shape `knowledge.embed` uses,
#: because it is the same port underneath and a second convention would be a
#: second thing to get wrong.
Embedder = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


def prose_fingerprint(sentences: Sequence[str], model: str, dimension: int) -> str:
    """What a stored table vector was computed from, in 64 hex characters.

    The three things that can invalidate it, hashed together: the prose (which
    moves when a DDL comment is re-synced *or* a curator edits a description),
    the model id, and the dimension. Asking whether a vector is still valid is
    recomputing this and comparing — there is no invalidation call for anybody
    to forget. `knowledge.embed.fingerprint` is the hash, shared rather than
    copied: two indexes in one product with two ideas of what a fingerprint is
    would be a bug waiting for whoever changes one of them.
    """
    return fingerprint("\n".join(sentences), model, dimension)


@dataclass(frozen=True, slots=True)
class TableVector:
    """One stored vector and the fingerprint it was written with."""

    vector: tuple[float, ...] = ()
    stored_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class VectorIndex:
    """A connection's schema vectors, as `retrieve` needs to see them.

    Built by `services/retrieval_index.load_vector_index` and carried on
    `NodeDeps`, so `app.pipeline` never learns that a database is involved —
    the same seam `NodeDeps.matcher` uses for the knowledge store.

    **Availability is a capability, not a preference.** No pinned embedding
    model is `is_available is False`, and that is a *state*: the lexical score
    answers alone, which is the whole feature on most connections. There is no
    switch, because a switch implies it could be on where it cannot work.
    """

    #: The model id pinned on the connection. Empty means there is no index.
    model: str = ""
    dimension: int = 0
    tables: dict[str, TableVector] = field(default_factory=dict)
    #: `None` when nothing can embed the question, which makes the whole path
    #: skip rather than fail.
    embed: Embedder | None = None

    @property
    def is_available(self) -> bool:
        return bool(self.model) and self.dimension > 0 and self.embed is not None

    def usable(self, texts: dict[str, tuple[str, ...]]) -> dict[str, tuple[float, ...]]:
        """The vectors that still stand for the prose they were made from.

        A row whose fingerprint does not recompute is **ignored, not deleted** —
        the next indexing pass overwrites it, and until then this table is
        scored lexically like any other. So a schema re-sync degrades the index
        table by table rather than all at once, and never wrongly: a vector is
        either current or absent, never quietly describing an older comment.

        A table with no prose at all has no vector and wants none: there is
        nothing to embed, and a vector of the empty string would match every
        question equally.
        """
        if not self.is_available:
            return {}
        out: dict[str, tuple[float, ...]] = {}
        for key, sentences in texts.items():
            entry = self.tables.get(key)
            if entry is None or not sentences or len(entry.vector) != self.dimension:
                continue
            current = prose_fingerprint(sentences, self.model, self.dimension)
            if entry.stored_fingerprint and entry.stored_fingerprint == current:
                out[key] = entry.vector
        return out


def cosines(
    question_vector: Sequence[float], vectors: dict[str, tuple[float, ...]]
) -> dict[str, float]:
    """How close each table's prose is to the question, in `[0, 1]`.

    `knowledge.embed.cosine` does the arithmetic — clamped, and 0.0 on anything
    degenerate, because two vectors of different width are a bug upstream and
    the honest answer to "how similar are they" is *not at all*, not an
    exception thrown at somebody asking about revenue.
    """
    if not question_vector:
        return {}
    return {key: cosine(question_vector, vector) for key, vector in vectors.items()}
