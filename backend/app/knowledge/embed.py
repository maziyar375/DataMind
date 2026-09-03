"""Masked question similarity — the embedding matcher, with no I/O in it.

Phase 7 of `docs/learning-loop-plan.md`, and **a swap rather than a rewrite**,
which is the whole return on decision D3: `EmbeddingMatcher` implements the
same `TemplateMatcher` Protocol `LexicalMatcher` does, so the `match` node, the
thresholds, the short-circuit and the badge are untouched.

**Masked question similarity (DAIL-SQL).** Table names, column names and
literal values are replaced with generic tokens *before* embedding, so
*"revenue in July for West"* retrieves the template written for *"revenue in
March for East"*. Without the masking the two questions differ in exactly the
tokens that carry no information about what is being asked, and an embedding
model — which has no idea that `West` is a region and `July` a month — scores
them apart for the wrong reason.

Three tokens rather than one, because the *shape* is the thing being kept:
`revenue by <column>` and `revenue by <table>` are different questions, and
folding both to `<mask>` would teach the matcher to confuse a grouping with a
source. DAIL-SQL uses one token; it also masks a schema it fully controls.

**No vector database, and no new deployment unit.** Vectors travel through the
existing `LLMGateway` port — LiteLLM already speaks the embedding endpoints —
and are stored in a `double precision[]` column beside the template that
produced them. Cosine is computed here, over the connection's own store, for
the same reason `trigram_similarity` is: **the index narrows, the matcher
decides.** A store big enough for that to be the wrong call is one that has
outgrown a curator, which is a different problem.

**Staleness is derived, never tracked.** A stored vector carries the
`fingerprint` of the three things it was computed from — the masked text, the
model id and the dimension. Asking whether a vector is still valid is
recomputing that fingerprint and comparing, so a template edit, a schema
re-sync and a model change each invalidate exactly what they should, and there
is no invalidation call anybody can forget to make. A vector that fails the
check is *ignored*, not deleted: the next indexing pass replaces it, and until
then the lexical matcher answers.

**Availability is a capability, not a preference.** A connection with no
embedding model pinned has no index here, and `FallbackMatcher` runs the
lexical one. The learning loop degrades to lexical, never to nothing — and
`knowledge_template_hits.matcher` records which one actually answered, so the
question "is this feature doing anything?" is a query rather than an opinion.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.knowledge.matcher import (
    DEFAULT_LIMIT,
    EMBEDDING,
    Candidate,
)
from app.knowledge.models import KnowledgeTemplate
from app.knowledge.normalize import mask_literals

#: What a masked schema term becomes. Distinct tokens, in angle brackets so
#: they cannot collide with a word somebody types: a question containing the
#: literal text `<column>` is not a question anybody asks.
TABLE_MASK = "<table>"
COLUMN_MASK = "<column>"
VALUE_MASK = "<value>"

#: A schema term shorter than this is not masked. `id`, `at` and `on` are
#: column names in most databases and words in most questions, and masking
#: them would delete the sentence rather than generalise it.
MIN_TERM_CHARS = 4

#: Cosine below this is not a candidate at all. Well under
#: `SHORT_CIRCUIT_THRESHOLD` and under `FEW_SHOT_THRESHOLD`, because those two
#: are the *decisions*; this only stops the matcher returning the whole store
#: ranked by noise. Embedding cosines do not use the bottom of their range —
#: two unrelated English sentences score around 0.3 on most models — so a floor
#: near zero would filter nothing.
SIMILARITY_FLOOR = 0.5

#: `(connection_id) -> VectorIndex`. One round trip: the vocabulary, the pinned
#: model and the vectors all come from the same read, so the question cannot be
#: masked against one schema and compared against vectors built from another.
IndexSource = Callable[[UUID], Awaitable["VectorIndex"]]

#: `(texts) -> vectors`. Batched, because an embedding endpoint charges per
#: call as well as per token and a store is indexed all at once.
Embedder = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """The schema terms a question may be hiding — tables and columns.

    Held as phrases rather than identifiers: a table called `order_items` is
    written *"order items"* in every question anybody asks, so both spellings
    are masked and the underscore form is not privileged over the English one.
    """

    tables: frozenset[str] = frozenset()
    columns: frozenset[str] = frozenset()
    #: Values the *curators* declared, gathered from every template's parameter
    #: comments — `one of: EMEA, NA, APAC`. This is the piece without which the
    #: canonical example does not work, and it is the same information the
    #: lexical matcher already uses.
    #:
    #: `EMEA` is not detectably a literal from the outside: it is a word, and
    #: `mask_literals` masks quoted strings, numerals and `{slots}` only. So
    #: *"revenue for EMEA"* would embed against a template whose own question
    #: reads *"revenue for `{region}`"* — already `<value>` — and the two would
    #: differ in exactly the token that carries no information. Masking a
    #: declared value can only ever remove a difference a **curator** said was a
    #: value, which is why it never invents a match: a question this store was
    #: not written for contains none of these words.
    #:
    #: Connection-wide rather than per-template, because the question is
    #: embedded once and compared against everything. That is the one place
    #: this differs from `mask_declared_values`, and it is forced by the shape
    #: of an embedding search rather than chosen.
    values: frozenset[str] = frozenset()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any] | None,
        templates: Sequence[KnowledgeTemplate] = (),
    ) -> Vocabulary:
        """Read a schema snapshot's names, plus what the curators declared.

        The snapshot is the same document the guard's policy is built from —
        `app.knowledge` may not import `app.infra`, and reading two keys off a
        dict is not a reason to break that.
        """
        tables: set[str] = set()
        columns: set[str] = set()
        for table in (snapshot or {}).get("tables", []) or []:
            tables.update(_phrases(table.get("name", "")))
            for column in table.get("columns", []) or []:
                columns.update(_phrases(column.get("name", "")))

        values: set[str] = set()
        for template in templates:
            for param in template.params:
                for value in param.values():
                    folded = (value or "").strip().casefold()
                    if len(folded) >= MIN_TERM_CHARS:
                        values.add(folded)

        # A name that is both is a table: masking `<table>` where the question
        # means the table and `<column>` where it means the column is not
        # decidable from the string, and the coarser reading is the safer one.
        # A declared value that is also a schema name stays a schema name, for
        # the same reason and in the same direction.
        return cls(
            tables=frozenset(tables),
            columns=frozenset(columns - tables),
            values=frozenset(values - tables - columns),
        )

    @property
    def is_empty(self) -> bool:
        return not self.tables and not self.columns and not self.values


def _phrases(identifier: str) -> set[str]:
    """The ways a question might spell one identifier.

    `order_items` → `order_items` and `order items`. Nothing is stemmed and
    nothing is singularised: *"order"* and *"orders"* are different words to an
    embedding model too, and inventing a match the schema did not state is the
    failure mode this whole package is written against.
    """
    name = (identifier or "").strip().casefold()
    if len(name) < MIN_TERM_CHARS:
        return set()
    spaced = re.sub(r"[_\-.]+", " ", name).strip()
    return {n for n in (name, spaced) if len(n) >= MIN_TERM_CHARS}


def mask_question(question: str, vocabulary: Vocabulary) -> str:
    """The text that gets embedded: literals and schema terms, generalised.

    Literals first and through `mask_literals`, so this holds no second opinion
    about what a literal is. Then schema terms, longest first and on whole-word
    boundaries — masking the `order` inside `reorder` would be the matcher
    inventing a difference rather than removing one, which is the same rule
    `mask_declared_values` follows for a curator's declared values.

    Total: any string produces a string, and a question mentioning no schema
    term at all comes back as itself, casefolded. That case is not a failure —
    *"how many customers do we have"* is a perfectly good question and its
    embedding is a perfectly good key.
    """
    text = mask_literals(question, VALUE_MASK)
    for term in sorted(vocabulary.tables, key=len, reverse=True):
        text = _replace_term(text, term, TABLE_MASK)
    for term in sorted(vocabulary.columns, key=len, reverse=True):
        text = _replace_term(text, term, COLUMN_MASK)
    for term in sorted(vocabulary.values, key=len, reverse=True):
        text = _replace_term(text, term, VALUE_MASK)
    # Two adjacent masks are one slot, not two — `normalize_question` collapses
    # the same run for the same reason. "top <value> <value> stores" would
    # otherwise embed differently from "top <value> stores" over a difference
    # nobody typed.
    text = re.sub(rf"{re.escape(VALUE_MASK)}(?:\s+{re.escape(VALUE_MASK)})+",
                  VALUE_MASK, text)
    return re.sub(r"\s+", " ", text).strip()


def _replace_term(text: str, term: str, token: str) -> str:
    if not term:
        return text
    return re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", token, text)


def fingerprint(masked: str, model: str, dimension: int) -> str:
    """What a stored vector was computed from, in 64 hex characters.

    The three inputs that can invalidate a vector, hashed together: the masked
    text (which moves when the template is edited *or* the schema is re-synced,
    since the mask reads the schema's own names), the model id, and the
    dimension. Comparing this against a freshly computed one is the entire
    staleness rule — §3.8 asks for "a template edit invalidates its vector; a
    model change invalidates all of them", and both fall out of it rather than
    being two pieces of bookkeeping somebody has to remember to run.
    """
    payload = f"{model}\x00{dimension}\x00{masked}".encode()
    return hashlib.sha256(payload).hexdigest()


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, clamped to `[0, 1]`, and 0.0 on anything degenerate.

    Two vectors of different length are a bug somewhere upstream — a store half
    indexed by one model and half by another — and the honest answer to "how
    similar are they" is *not at all*, not an exception thrown at a person
    asking a question about revenue. The fingerprint check should have caught
    it first; this is the second door.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm <= 0.0:
        return 0.0
    # Negative cosines exist and are not "less similar than nothing" for this
    # purpose: every threshold in `matcher.py` reads as a fraction of a match,
    # so the range is clamped rather than shifted.
    return max(0.0, min(1.0, dot / norm))


@dataclass(frozen=True, slots=True)
class VectorEntry:
    """One template and the vector standing in for its question."""

    template: KnowledgeTemplate
    vector: list[float] = field(default_factory=list)
    #: The fingerprint stored beside the vector when it was written.
    stored_fingerprint: str = ""

    def is_fresh(self, vocabulary: Vocabulary, model: str, dimension: int) -> bool:
        """Whether this vector still stands for this template's question."""
        if not self.vector or len(self.vector) != dimension:
            return False
        current = fingerprint(
            mask_question(self.template.question, vocabulary), model, dimension
        )
        return bool(self.stored_fingerprint) and self.stored_fingerprint == current


@dataclass(frozen=True, slots=True)
class VectorIndex:
    """A connection's embedding index, as the matcher needs to see it."""

    vocabulary: Vocabulary = field(default_factory=Vocabulary)
    #: The model id pinned on the connection. Empty means embeddings are not
    #: available here, which is a state, not an error.
    model: str = ""
    dimension: int = 0
    entries: list[VectorEntry] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return bool(self.model) and self.dimension > 0

    def usable(self) -> list[VectorEntry]:
        """Entries whose vectors are still valid *and* still matchable.

        `is_matchable` is asked here as well as in the row source, for the
        reason `LexicalMatcher` asks it twice: a held-out row answering its own
        question measures nothing and a stale one answers with SQL the schema
        no longer supports, and neither failure is visible from the outside.
        """
        if not self.is_available:
            return []
        return [
            entry
            for entry in self.entries
            if entry.template.is_matchable
            and entry.is_fresh(self.vocabulary, self.model, self.dimension)
        ]


class EmbeddingMatcher:
    """Masked question similarity over a connection's stored vectors.

    Behind the same Protocol as `LexicalMatcher`, and returning the same
    `Candidate` against the same two thresholds — the point of D3 was that
    Phase 7 is a constructor change and nothing else.

    Returns `[]` rather than raising in every "cannot answer" case: no model
    pinned, no fresh vectors, the question masks to nothing, the embedding call
    failed. `FallbackMatcher` reads an empty list as *"ask the lexical one"*, so
    each of those degrades to today's behaviour instead of to a failed run.
    """

    __slots__ = ("_embed", "_index")

    def __init__(self, embed: Embedder, index: IndexSource) -> None:
        self._embed = embed
        self._index = index

    async def match(
        self, question: str, connection_id: UUID, *, limit: int = DEFAULT_LIMIT
    ) -> list[Candidate]:
        index = await self._index(connection_id)
        entries = index.usable()
        if not entries:
            return []

        masked = mask_question(question, index.vocabulary)
        if not masked:
            return []

        vectors = await self._embed([masked])
        if not vectors or len(vectors[0]) != index.dimension:
            # A provider that answered with a different width than the one
            # pinned has changed under us. Every stored vector is now
            # incomparable, and the honest move is to say nothing and let
            # lexical answer until the next indexing pass re-pins the model.
            return []

        asked = vectors[0]
        candidates = [
            Candidate(
                template=entry.template,
                score=cosine(asked, entry.vector),
                matcher=EMBEDDING,
            )
            for entry in entries
        ]
        found = [c for c in candidates if c.score >= SIMILARITY_FLOOR]
        return sorted(found, key=_rank, reverse=True)[:limit]


def _rank(candidate: Candidate) -> tuple[float, int]:
    """Score first, then the template people have actually used.

    The same tie-break `LexicalMatcher` uses, and deliberately the same
    function shape: two matchers that ranked ties differently would make the
    Phase 7 recall delta partly a measurement of the tie-break.
    """
    return candidate.score, candidate.template.hit_count


def to_index(
    snapshot: dict[str, Any] | None,
    model: str,
    dimension: int,
    entries: Sequence[VectorEntry],
) -> VectorIndex:
    """Assemble an index from the pieces a service layer has to hand.

    The vocabulary is built from the entries' own templates, so the declared
    values that masked a template at indexing time are the ones that mask the
    question at query time. Handing that job to the caller would make it two
    readings that have to agree, which is one more than can be relied on.
    """
    return VectorIndex(
        vocabulary=Vocabulary.from_snapshot(
            snapshot, [entry.template for entry in entries]
        ),
        model=model or "",
        dimension=max(0, dimension),
        entries=list(entries),
    )


def needs_embedding(
    template: KnowledgeTemplate,
    stored_fingerprint: str,
    vector_length: int,
    vocabulary: Vocabulary,
    model: str,
    dimension: int,
) -> str:
    """The masked text to embed for this template, or `""` if it is current.

    Returning the *text* rather than a boolean is deliberate: the caller needs
    it either way to build the batch, and computing the mask twice — once to
    decide and once to send — is how the decision and the payload drift apart.
    """
    if not model or dimension <= 0:
        return ""
    masked = mask_question(template.question, vocabulary)
    if not masked:
        return ""
    current = fingerprint(masked, model, dimension)
    if stored_fingerprint == current and vector_length == dimension:
        return ""
    return masked
