"""The nodes.

Each is `async def node(state, deps) -> NodeResult`. Nodes mutate the typed
state and report status; they never touch persistence and never decide what
happens next beyond an optional `goto`. Ordering lives in the executor.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.clock import utcnow
from app.core.errors import ConnectorError, LLMError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector
from app.domain.ports.llm import ChatMessage, LLMGateway, ResolvedLLM
from app.domain.value_objects import DisclosurePolicy, HintBudget
from app.knowledge.bind import bind_params, bind_sql
from app.knowledge.matcher import TemplateMatcher, best
from app.pipeline.checks import Finding
from app.pipeline.contracts import ClarificationProposal, SqlProposal
from app.pipeline.disclosure import disclose_history
from app.pipeline.metadata import (
    answer_metadata,
    census,
    select_tables,
    table_chars,
)
from app.pipeline.prompts import (
    ANSWER_SYSTEM,
    ANSWER_USER,
    CHART_SYSTEM,
    CHART_SYSTEM_COMPOSED,
    CHART_USER,
    CHART_USER_COMPOSED,
    CLARIFY_SYSTEM,
    CLARIFY_USER,
    DESCRIBE_SYSTEM,
    DESCRIBE_USER,
    GENERATE_SYSTEM,
    GENERATE_USER,
    REPAIR_SYSTEM,
    REVIEW_SYSTEM,
    ROUTE_SYSTEM,
    ROUTE_SYSTEM_WITH_HISTORY,
)
from app.pipeline.state import (
    ClarificationRequest,
    ExecutionResult,
    NodeResult,
    RetrievedContext,
    RunError,
    RunState,
    SqlAttempt,
    TemplateExample,
)
from app.sqlguard import GuardPolicy, guard
from app.sqlguard.validator import ValidationReport

if TYPE_CHECKING:  # `app.charts` is imported lazily inside the nodes that use it
    from app.charts import ChartIntent, ResultProfile

log = get_logger(__name__)


@dataclass(slots=True)
class NodeDeps:
    llm_gateway: LLMGateway
    llm: ResolvedLLM
    connector: DatabaseConnector
    snapshot: dict[str, Any]
    history: list[dict[str, str]]
    policy: GuardPolicy
    emit: Any  # async callable(event_type: str, data: dict) -> None
    # The connection's semantic layer, or None when it has none or has
    # switched it off. Passed through `retrieve` into the schema block.
    semantic: dict[str, Any] | None = None
    # Whether `clarify` may stop the run to ask. False both when the
    # connection has the switch off and when this run *is* the answer to a
    # question we already asked — see `run_service.execute_run`.
    clarify_enabled: bool = False
    # Whether the database's own catalog descriptions reach the schema block.
    # On by default like the column it comes from; off is byte-identical to the
    # prompt from before comments existed. Defaults False here for the same
    # reason `clarify_enabled` does — a `NodeDeps` built without the connection
    # in hand renders what it always rendered.
    include_db_comments: bool = False
    # The knowledge matcher, or None. **None is the pre-feature behaviour
    # exactly**: `match` reports SKIPPED, nothing is read, and the prompt the
    # generator receives is byte-identical to the one it received before this
    # node existed — which is why `PROMPT_VERSION` does not move in Phase 2.
    # The draft graph and the eval runner both leave it None.
    matcher: TemplateMatcher | None = None
    # Whether this run may consult the store at all. False when the reader
    # pressed *Generate a fresh answer instead* on a verified answer: that is
    # the one control that makes a Verified badge safe to show.
    templates_enabled: bool = True
    # Whether a *near* match may reach the generate prompt as an example
    # (Phase 5). Distinct from `templates_enabled`, which governs the
    # short-circuit: answering from a stored template and showing one to the
    # model are different bets with different failure modes, and only the
    # second can make the product worse.
    #
    # **False is byte-identical to v8** — `match` collects nothing, `retrieve`
    # carries nothing, the `{examples}` slot collapses. False by default here
    # for the same reason `clarify_enabled` is: a `NodeDeps` built without the
    # connection in hand renders what it always rendered. It is also the
    # *column's* default until the eval gate in `docs/eval.md` §6.1 has been run.
    examples_enabled: bool = False
    # Extra constraints appended to every SQL-producing prompt, for callers
    # whose SQL has to satisfy something a chat question does not. Today that
    # is exactly one caller: a report block, whose statement is saved and
    # re-run months later and therefore may not contain a literal date
    # (`app/reports/prompts.py`, §6 of `docs/reports-plan.md`).
    #
    # **Empty by default, and empty means byte-identical.** With no report in
    # play every SQL prompt is exactly what it was before this field existed,
    # which is why `PROMPT_VERSION` does not move — the same discipline
    # `semantic_layer_enabled` and `clarify_enabled` follow, and there is a
    # test asserting it.
    extra_rules: str = ""


# ── route ────────────────────────────────────────────────────────────────
async def route(state: RunState, deps: NodeDeps) -> NodeResult:
    """Classify before spending a schema-sized prompt on small talk.

    Reads the conversation when there is one. A follow-up carries almost none
    of its own subject — "and by month?" is nine characters with no data noun
    in them — so classifying it alone put the two cheapest labels within reach
    of a question that plainly needs the database, and CHITCHAT or UNSUPPORTED
    halts the run before a single line of SQL is written. The turns before it
    are what make it readable.

    A first turn has no history and takes the prompt it always took, so the
    opening question of every conversation is unchanged.
    """
    started = time.perf_counter()
    # Same policy filter as every other prompt: `route` sees an earlier answer
    # only on the terms the connection's disclosure policy allows now.
    history_text = _render_history(deps.history, state.disclosure_policy)
    system = (
        ROUTE_SYSTEM_WITH_HISTORY.format(history=history_text)
        if history_text
        else ROUTE_SYSTEM
    )
    try:
        completion = await deps.llm_gateway.complete(
            deps.llm,
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=state.question),
            ],
        )
        state.llm_latency_ms += completion.latency_ms
        state.prompt_tokens += completion.prompt_tokens
        state.completion_tokens += completion.completion_tokens
        label = completion.text.strip().upper().split()[0] if completion.text else ""
    except LLMError:
        # A routing failure must not fail the run; assume the common case.
        label = "ANALYTICAL"

    state.intent = label if label in {
        "ANALYTICAL", "METADATA", "CHITCHAT", "UNSUPPORTED"
    } else "ANALYTICAL"

    elapsed = int((time.perf_counter() - started) * 1000)

    if state.intent == "CHITCHAT":
        state.answer = (
            "I answer questions about the data in your connected database. "
            'Ask me something like "What was total revenue last month?"'
        )
        return NodeResult(status="HALT", detail=f"Classified CHITCHAT in {elapsed}ms")

    if state.intent == "UNSUPPORTED":
        # Answer gracefully instead of failing the run: an out-of-scope or
        # write request is not an error the user needs to debug, so we reply
        # like CHITCHAT (a HALT with an answer) rather than surfacing E_*.
        state.answer = (
            "I can only answer questions about the data in your connected "
            "database, and I can read that data but never change it. "
            'Try something like "What was total revenue last month?"'
        )
        return NodeResult(status="HALT", detail="Classified UNSUPPORTED")

    # METADATA falls through with ANALYTICAL, as far as `describe`, which
    # answers it from the schema block `retrieve` is about to build and halts
    # there. What it must never reach is `generate`: asked for SQL, the model
    # queries information_schema, which the guard always rejects as a system
    # table — the run would fail before an answer could exist.
    return NodeResult(detail=f"Classified {state.intent} in {elapsed}ms")


# ── match ────────────────────────────────────────────────────────────────
async def match(state: RunState, deps: NodeDeps) -> NodeResult:
    """Has somebody already answered this question? — the short-circuit.

    The first node that can change an answer, and it does it **without
    changing a single byte of the prompt**. On a hit it fills
    `state.attempts` with the bound statement and hands over to `validate`,
    which is the guard's own entry point for the pipeline and already feeds
    `execute`. So a stored template reuses every guarantee the generated path
    has — re-validation against the *current* snapshot, the rewriter, the row
    cap — and gets **no exemption**. On a miss it changes nothing at all and
    the run continues to `retrieve` exactly as it always did.

    Four ways this node declines, and each is recorded rather than swallowed:

    * **no matcher, or templates disabled** — SKIPPED, no verdict, nothing
      logged. This is the pre-feature path and it has to be free.
    * **nothing close enough** — a miss. A near-miss is not a hit: the cost of
      a miss is today's behaviour and the cost of a false hit is a confident
      wrong answer.
    * **a parameter would not bind** — `REJECTED_UNBOUND`. Logged, because
      that log is how we learn which date phrasings to teach the binder next.
      A half-bound template is the failure class this product exists to avoid.
    * **the SQL no longer passes the guard** — `REJECTED_STALE`. The schema
      moved underneath a template that was legal when it was written. The run
      does **not** fail and the row is **not** deleted: it falls through to
      generation, and Phase 4's worker is what marks the row. *Fail as a
      value.*

    Validating here and again in `validate` is not waste. This one decides
    *whether the template is still usable*; that one produces *the statement to
    run*, with the row cap applied. Same function, two questions.
    """
    if deps.matcher is None or not deps.templates_enabled:
        return NodeResult(status="SKIPPED", detail="No knowledge store consulted")
    if state.intent != "ANALYTICAL":
        # METADATA is answered from the schema block by `describe`; a taught
        # question is about the data, not about the shape of it.
        return NodeResult(status="SKIPPED", detail=f"Not analytical ({state.intent})")

    try:
        candidates = await deps.matcher.match(state.question, state.connection_id)
    except Exception:
        # A matcher failure must never fail a run. The question is answerable
        # without the store — that is what the store being an accelerator,
        # rather than a dependency, means.
        log.warning("template_match_failed", run_id=str(state.run_id))
        return NodeResult(status="SKIPPED", detail="Knowledge store unavailable")

    # Which matcher produced these, recorded before the verdict rather than
    # after it. Phase 7's `FallbackMatcher` answers with whichever half found
    # something, so "was this connection's store searched by meaning?" is a
    # fact about the candidates and not about how the matcher was built — and
    # a *miss* that offered few-shot examples is exactly as much a retrieval
    # as a short-circuit is, so it has to be recorded on both paths.
    if candidates:
        state.match_kind = candidates[0].matcher

    hit = best(candidates)
    if hit is None:
        near = candidates[0].score if candidates else 0.0
        shown = _collect_examples(state, deps, candidates)
        detail = f"No template matched (best {near:.2f})"
        if shown:
            detail += f" · {shown} offered as {'an example' if shown == 1 else 'examples'}"
        return NodeResult(detail=detail)

    template = hit.template
    state.match_score = hit.score
    state.match_kind = hit.matcher

    binding = bind_params(state.question, template.params, now=utcnow())
    if not binding.bound:
        state.match_outcome = "REJECTED_UNBOUND"
        state.matched_template_id = template.id
        return NodeResult(
            detail=f"Matched, but {', '.join(binding.missing)} would not bind"
        )

    bound_sql = bind_sql(template.sql, binding.values, dialect=deps.policy.dialect)
    report, _ = guard(bound_sql or "", deps.policy) if bound_sql else (None, None)
    if report is None or report.status != "VALID":
        state.match_outcome = "REJECTED_STALE"
        state.matched_template_id = template.id
        reason = report.errors[0].message if report and report.errors else "unparseable"
        return NodeResult(detail=f"Matched, but the template is stale: {reason}")

    state.matched_template_id = template.id
    state.matched_question = template.question
    state.bound_params = {k: str(v) for k, v in binding.values.items()}
    state.match_outcome = "SHORT_CIRCUIT"
    state.attempts.append(
        SqlAttempt(attempt_no=1, raw_sql=bound_sql, report=ValidationReport())
    )
    await deps.emit(
        "SQL_GENERATED", {"attempt_no": 1, "sql": bound_sql}
    )
    return NodeResult(
        goto="validate",
        detail=f"Answered from a saved question ({hit.score:.2f})",
    )


def _collect_examples(
    state: RunState, deps: NodeDeps, candidates: list[Any]
) -> int:
    """Put the near misses on the state as few-shot examples. Returns how many.

    **Only on a miss.** A run answered from a stored template has no generator
    to teach, and offering the template it just used as an example of itself
    would be a prompt about a call that never happens.

    Two filters and no third:

    * `examples_enabled`, the per-connection switch, which is off by default
      until the eval gate has been run. Off collects nothing, so `retrieve`
      carries nothing and the prompt is byte-identical to v8.
    * `few_shot`, the candidate's own threshold (0.45) — well below the
      short-circuit's 0.85, because "close enough to be worth showing" and
      "close enough to answer with" are genuinely different questions.

    The disclosure gate is deliberately **not** here. A template's literals are
    gated at *render* time (`RetrievedContext.render_examples`), like every
    other rung of the ladder, so tightening a connection's policy takes effect
    on the next question rather than on the next match.
    """
    if not deps.examples_enabled:
        return 0
    state.examples = [
        TemplateExample(
            question=candidate.template.question,
            sql=candidate.template.sql,
            literal_provenance=str(candidate.template.literal_provenance),
        )
        for candidate in candidates
        if candidate.few_shot and candidate.template.sql
    ]
    return len(state.examples)


def _tables_from_history(
    history: list[dict[str, str]], tables: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The tables the recent turns actually queried, in snapshot order.

    Matched on the qualified name inside the SQL behind an earlier answer,
    which is exact rather than approximate: `_SQL_RULES` requires every table
    to be schema-qualified, so `public.orders` appears verbatim in any
    statement the guard let through. That is the whole reason this reads the
    SQL and not the prose — "revenue rose in June" names no table, and a
    substring search over narration matches `id` in "identify".

    The result never leaves the process: it selects rows *from the snapshot*,
    which `RetrievedContext.render` then gates by the disclosure policy like
    any other schema block. So this reads the raw history, before
    `disclose_history` — no policy governs which of the customer's own tables
    the customer's own question may be answered from.
    """
    statements = " ".join(
        turn["sql"].lower() for turn in history if turn.get("sql")
    )
    if not statements:
        return []
    return [
        t for t in tables
        if f"{t['schema']}.{t['name']}".lower() in statements
    ]


def _expand_by_fk(
    seed: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Grow a seed set by one foreign-key hop, in either direction.

    Junction and bridge tables reference the entities they connect, so a table
    one hop from a matched entity is exactly the join path the question implies
    but does not spell out. Order is preserved (snapshot order) for a stable
    prompt.
    """
    seed_names = {f"{t['schema']}.{t['name']}" for t in seed}
    reachable = set(seed_names)
    # Condition reads the frozen seed only, so this is exactly one hop —
    # deterministic and independent of relationship order.
    for r in relationships:
        if r["from_table"] in seed_names:
            reachable.add(r["to_table"])
        if r["to_table"] in seed_names:
            reachable.add(r["from_table"])
    return [t for t in tables if f"{t['schema']}.{t['name']}" in reachable]


def _describe_schema(
    tables: list[dict[str, Any]], policy: str = DisclosurePolicy.NONE
) -> str:
    """Every table and column name, for a model-facing prompt.

    `policy` defaults to NONE so a caller that forgets one emits structure
    only — the same fail-closed default as `RetrievedContext.render`, and for
    the same reason: a row count is derived from the customer's data, and
    `HintBudget` withholds it under NONE. Names are not gated here because they
    never were: the schema block goes to the model on every question under
    every policy, and a question cannot be answered against a schema the model
    cannot see.
    """
    if not tables:
        return "This connection has no tables in its current schema snapshot."
    budget = HintBudget.from_policy(policy)
    lines = [f"You have {len(tables)} table{'' if len(tables) == 1 else 's'}:"]
    for table in tables:
        cols = ", ".join(c["name"] for c in table.get("columns", []))
        rows = table.get("approx_row_count")
        suffix = f" (~{rows:,} rows)" if rows and budget.row_counts else ""
        lines.append(f"- {table['schema']}.{table['name']}{suffix}: {cols}")
    return "\n".join(lines)


# ── retrieve ─────────────────────────────────────────────────────────────
# How much estimated schema text may go to the model before retrieval starts
# selecting. Raised 24k -> 50k, because the fallback below is the worse path
# rather than the safer one: it seeds on raw substring matches against catalog
# names, so it misses `order_items` for a user who typed "order items" while
# matching every table carrying a column called `id`. Sending a whole schema
# costs tokens; taking that branch costs answers. Roughly 12k tokens of schema
# at the ceiling, before the semantic layer adds up to 8k chars more.
#
# A module constant, not a local, so a test can lower it to exercise the
# fallback without needing a schema larger than whatever the fixture happens
# to be — the `sales` fixture sits at ~26.5k, which straddled the old value.
_RETRIEVE_BUDGET_CHARS = 50_000


async def retrieve(state: RunState, deps: NodeDeps) -> NodeResult:
    """Naive by design: send the whole snapshot when it fits the budget.

    Exact-name matching is the fallback. Trigram, FTS, and embeddings are
    later strategies behind the same `RetrievedContext` shape; the generator
    never learns which one produced its context.
    """
    tables = deps.snapshot.get("tables", [])
    relationships = deps.snapshot.get("relationships", [])

    approx_chars = sum(table_chars(t) for t in tables)

    if approx_chars <= _RETRIEVE_BUDGET_CHARS:
        selected, strategy = tables, "FULL_SNAPSHOT"
    elif state.intent == "METADATA":
        # A schema question is *about* the snapshot, not answerable from a
        # corner of it, so the branch below is the wrong selector twice over:
        # it seeds on words the question shares with a table name — and "what
        # is in this database?" shares none — then falls back to an arbitrary
        # twenty. `select_tables` describes what the question named and spends
        # the rest of the budget on the largest tables, and `describe` states
        # the total and names what was left out, so an answer written over a
        # truncated block is still an answer about the whole schema.
        selected = select_tables(
            state.question, tables, budget_chars=_RETRIEVE_BUDGET_CHARS
        )
        strategy = "SCHEMA_QUESTION"
    else:
        needle = state.question.lower()
        matched = [
            t for t in tables
            if t["name"].lower() in needle
            or any(c["name"].lower() in needle for c in t.get("columns", []))
        ]
        # A follow-up names nothing: "and by month?" matches no table, and on
        # its own would fall through to `tables[:20]` — an arbitrary twenty
        # that need not include the table the question it continues was
        # answered from. The tables the previous statement ran against are the
        # subject it inherits, so they seed retrieval alongside anything this
        # question named itself.
        carried = _tables_from_history(deps.history, tables)
        seen = {f"{t['schema']}.{t['name']}" for t in matched}
        seed = matched + [
            t for t in carried if f"{t['schema']}.{t['name']}" not in seen
        ]
        # A question names its entities ("orders", "products") but almost never
        # the junction/bridge tables that join them ("order_items",
        # "product_tags"). Pull in every table one foreign-key hop from a matched
        # table so those bridges reach the generator; substring matching alone
        # structurally cannot find them.
        selected = _expand_by_fk(seed, tables, relationships) if seed else tables[:20]
        strategy = "EXACT_MATCH"

    names = {f"{t['schema']}.{t['name']}" for t in selected}
    state.context = RetrievedContext(
        dialect=state.dialect,
        tables=selected,
        relationships=[
            r for r in relationships
            if r["from_table"] in names or r["to_table"] in names
        ],
        history=deps.history,
        strategy=strategy,
        semantic=deps.semantic,
        catalog_meta=deps.snapshot.get("catalog_meta") or {},
        include_db_comments=deps.include_db_comments,
        # Whatever `match` left behind, which is nothing at all unless the
        # connection has the feature on *and* something scored above the
        # few-shot threshold *and* the run was not answered from the store.
        examples=list(state.examples),
    )
    described = (
        sum(
            1 for e in (deps.semantic or {}).get("entities", [])
            if e.get("table", "").lower() in {n.lower() for n in names}
        )
        if deps.semantic else 0
    )
    detail = f"{len(selected)} tables via {strategy}"
    return NodeResult(
        detail=detail + (f" · {described} described" if described else "")
    )


# ── conversation history ─────────────────────────────────────────────────
# A turn is trimmed, not summarised: there is no summarisation anywhere in the
# pipeline, and a truncated real sentence is a safer input than a paraphrase
# nothing verified.
_HISTORY_CONTENT_CHARS = 300
_HISTORY_SQL_CHARS = 400


def _render_history(
    history: list[dict[str, str]], policy: str = DisclosurePolicy.NONE
) -> str:
    """The recent turns as the model sees them, or `""` when there are none.

    An assistant turn carries the SQL that produced it when one is known.
    Without it the model reads its own narration ("Revenue was $1.2M in
    June…") and has to re-derive the query behind it, which is the whole
    difficulty of a follow-up like "now break that down by month" — the answer
    it is building on is a sentence, not a statement it can extend.

    The turns are first put through `disclose_history`, because an earlier
    answer's prose is result data the model wrote down: the policy that gated
    the result has to gate the transcript that quotes it, or tightening a
    connection would take effect for one turn and be undone by the next. As
    everywhere else in the pipeline, `policy` defaults to the narrowest so a
    caller that forgets one cannot widen a disclosure.

    The SQL is whitespace-collapsed onto one line so a multi-line statement
    cannot break the `role: content` structure the turns are read by.
    """
    if not history:
        return ""
    lines: list[str] = []
    for turn in disclose_history(history, policy):
        lines.append(f"{turn['role']}: {turn['content'][:_HISTORY_CONTENT_CHARS]}")
        sql = turn.get("sql")
        if sql:
            lines.append(f"  SQL: {' '.join(sql.split())[:_HISTORY_SQL_CHARS]}")
    return "Earlier in this conversation:\n" + "\n".join(lines)


# ── thinking out loud ────────────────────────────────────────────────────
# Reasoning arrives a token at a time, and one event per token is a bus
# publish and a re-render each for a channel nobody reads word by word. At
# this cadence the text still visibly moves — which is the entire point of
# showing it — for a fortieth of the traffic.
_REASONING_FLUSH_SECONDS = 0.4


class _Thinking:
    """Coalesces a model's reasoning channel into paced `REASONING_DELTA` events.

    Shared by the two nodes that stream prose, and written because of what a
    reasoning model does to them: it can spend a minute on `reasoning_content`
    before its first word of `content`, during which the old loop — which read
    `content` and nothing else — emitted nothing at all. The reader saw a step
    chip and a still cursor, which is what a hung run looks like. Nothing was
    hung; the answer had simply not been started yet.

    It keeps no transcript. Pieces are flushed and forgotten, because none of
    this is the answer: `state.answer` is built from `text` chunks only, and
    the deliberation is gone the moment it has been shown.

    The first piece flushes immediately — the indicator's job is to appear at
    the same moment the wait does — and every piece after it waits its turn.
    """

    __slots__ = ("_emit", "_started", "_last", "_flushed_at", "_pending")

    def __init__(self, emit: Any) -> None:
        self._emit = emit
        self._started: float | None = None
        self._last = 0.0
        self._flushed_at = 0.0
        self._pending: list[str] = []

    @property
    def happened(self) -> bool:
        """Whether this model thinks out loud at all. Most do not."""
        return self._started is not None

    @property
    def elapsed_ms(self) -> int:
        """How long the reasoning phase ran — measured to the last thought,
        not to now, so it stops climbing once the prose starts."""
        if self._started is None:
            return 0
        return int((self._last - self._started) * 1000)

    async def add(self, piece: str) -> None:
        now = time.perf_counter()
        if self._started is None:
            self._started = now
        self._last = now
        self._pending.append(piece)
        if now - self._flushed_at >= _REASONING_FLUSH_SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self._pending:
            return
        text = "".join(self._pending)
        self._pending.clear()
        self._flushed_at = time.perf_counter()
        await self._emit(
            "REASONING_DELTA", {"text": text, "elapsed_ms": self.elapsed_ms}
        )

    def note(self) -> str:
        """The step detail's share of it: durable, unlike the text itself.

        `REASONING_DELTA` is deliberately never written down, so without this
        a reopened thread would show a node that took ninety seconds and no
        hint of where they went. The trail keeps the number; only the words
        are transient.
        """
        if not self.happened:
            return ""
        return f" · thought for {self.elapsed_ms / 1000:.1f}s"


# ── describe ─────────────────────────────────────────────────────────────
async def describe(state: RunState, deps: NodeDeps) -> NodeResult:
    """Answer a question about the schema, from the schema. Never any SQL.

    The one node that exists for a single intent, and it is placed here — after
    `retrieve`, before `clarify` — because what a schema question needs is
    exactly what `retrieve` has just built: the tables, their columns and keys,
    and the semantic layer scoped to them. Those are what make "what does
    `order_items` count?" answerable at all; the grain of a table and the
    metrics defined over it live in the layer and nowhere else, and the
    inventory this node replaced could not read them.

    It widens no disclosure. The block is `RetrievedContext.render` under the
    run's own policy, the same bytes `generate` would have been sent, and the
    transcript goes through the same `disclose_history` filter as every other
    prompt. `census` adds counts and names — structure, which travels under
    every policy — and deliberately no totals derived from the data.

    Fails backwards onto `answer_metadata`, the rendering this node replaced:
    a provider that breaks mid-sentence, one that streams nothing, and a
    connection whose snapshot is empty all end with the snapshot rendered
    directly rather than with an apology. The empty-snapshot case never calls
    the model at all — there is nothing for it to read.

    HALTs on every path. A METADATA question has its answer here, and every
    node after this one is about a result that will never exist.
    """
    if state.intent != "METADATA":
        return NodeResult(status="SKIPPED", detail="Not a schema question")

    assert state.context is not None
    tables = deps.snapshot.get("tables", [])
    if not tables:
        state.answer = answer_metadata(state.question, tables)
        await deps.emit("TEXT_DELTA", {"text": state.answer})
        return NodeResult(status="HALT", detail="No tables to describe")

    messages = [
        ChatMessage(
            role="system",
            content=DESCRIBE_SYSTEM.format(
                schema=state.context.render(state.disclosure_policy),
                census=census(tables, state.context.tables),
                history=_render_history(
                    state.context.history, state.disclosure_policy
                ),
            ),
        ),
        ChatMessage(role="user", content=DESCRIBE_USER.format(question=state.question)),
    ]

    started = time.perf_counter()
    buffer: list[str] = []
    thinking = _Thinking(deps.emit)
    failed = False
    try:
        async for chunk in deps.llm_gateway.stream(deps.llm, messages):
            if chunk.reasoning:
                await thinking.add(chunk.reasoning)
                continue
            # Whatever is still pending goes out ahead of the first word of
            # prose, so the thought that produced it is on screen before the
            # answer it produced.
            await thinking.flush()
            buffer.append(chunk.text)
            await deps.emit("TEXT_DELTA", {"text": chunk.text})
    except LLMError as err:
        log.warning(
            "describe_stream_failed", run_id=str(state.run_id), error=err.message
        )
        failed = True
    await thinking.flush()

    text = "" if failed else "".join(buffer).strip()
    if not text:
        if buffer:
            # Every delta is already on the live bus *and* durably stored for
            # Last-Event-ID replay, so the fallback cannot simply be appended:
            # a client would render half a sentence with a table listing glued
            # onto the end. Same contract, and the same handler, as `present`.
            await deps.emit("TEXT_RESET", {"reason": "stream_failed"})
        text = answer_metadata(state.question, tables)
        await deps.emit("TEXT_DELTA", {"text": text})

    state.answer = text
    elapsed = int((time.perf_counter() - started) * 1000)
    described = len(state.context.tables)
    return NodeResult(
        status="HALT",
        detail=(
            f"Described {described} of {len(tables)} tables in {elapsed}ms"
            + thinking.note()
            + (" (from the snapshot)" if failed else "")
        ),
    )


# ── clarify ──────────────────────────────────────────────────────────────
def _clean_options(options: list[str], limit: int = 4) -> list[str]:
    """De-duplicated, trimmed, capped. A chip the user cannot read is noise."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for option in options:
        text = " ".join(str(option).split())[:120]
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) == limit:
            break
    return cleaned


async def clarify(state: RunState, deps: NodeDeps) -> NodeResult:
    """Ask, once, rather than answer a question the user did not ask.

    Placed after `retrieve` so the judgement is made against the same schema
    block and semantic layer the generator will see: "which revenue column?"
    is only answerable with the columns in hand, and a metric definition that
    already settles the question must be visible or this node invents doubt.

    Fails open in every direction. A model error, a malformed proposal, or an
    empty question all mean "proceed" — an unanswered question is a worse
    outcome than a guessed one, and the guessed one is still shown with its
    SQL for the user to check.

    **SKIPPED means the check did not run**, and nothing else. That is one
    case: the switch is off for this run — the connection has it disabled, or
    this run is the *answer* to a question already asked, which may not ask
    again. Every other outcome is a check that happened and cost what it cost,
    including the one that failed open on a provider error, and each reports
    itself as a step that ran. The distinction is not cosmetic: a step trail
    that greys out a node which just spent forty seconds on a timing-out
    provider hides the slowest thing in the run behind the word "skipped".
    """
    if not deps.clarify_enabled:
        return NodeResult(status="SKIPPED", detail="Clarification off for this run")

    assert state.context is not None
    started = time.perf_counter()
    history_text = _render_history(state.context.history, state.disclosure_policy)

    # Streamed for the reasoning channel, not for the JSON. This node asks a
    # reasoning model a judgement question and then shows the reader nothing
    # until it has finished thinking — measured on a real install at eight
    # seconds typically and thirty-five on a bad day, against a step chip with
    # no text under it, which is what a hung run looks like. The reply is still
    # one validated `ClarificationProposal`: `on_reasoning` changes the
    # transport and nothing else, so the prompt is byte-identical and
    # `PROMPT_VERSION` does not move.
    thinking = _Thinking(deps.emit)

    try:
        proposal = await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(
                    role="system",
                    content=CLARIFY_SYSTEM.format(
                        schema=state.context.render(state.disclosure_policy),
                        history=history_text,
                    ),
                ),
                ChatMessage(
                    role="user", content=CLARIFY_USER.format(question=state.question)
                ),
            ],
            ClarificationProposal,
            on_reasoning=thinking.add,
        )
    except (LLMError, ValueError) as err:
        log.warning("clarify_failed", run_id=str(state.run_id), error=str(err))
        await thinking.flush()
        elapsed = int((time.perf_counter() - started) * 1000)
        return NodeResult(
            detail=(
                f"Clarification check unavailable, after {elapsed}ms — proceeded"
                f"{thinking.note()}"
            )
        )

    # Whatever is still pending goes out before the node's own verdict, so the
    # last thought is on screen before the step that produced it turns green.
    await thinking.flush()
    elapsed = int((time.perf_counter() - started) * 1000)
    question = " ".join(proposal.question.split())
    if proposal.answerable or not question:
        return NodeResult(
            detail=f"Answerable as asked, in {elapsed}ms{thinking.note()}"
        )

    state.clarification = ClarificationRequest(
        question=question, options=_clean_options(proposal.options)
    )
    # The question *is* the answer for this turn: it becomes the assistant
    # message, so the thread reads as a conversation rather than a dead run.
    state.answer = question
    await deps.emit(
        "CLARIFICATION_REQUESTED", state.clarification.model_dump(mode="json")
    )
    return NodeResult(
        status="HALT", detail=f"Asked the user in {elapsed}ms{thinking.note()}"
    )


# ── generate ─────────────────────────────────────────────────────────────
def _with_extra_rules(system: str, extra_rules: str) -> str:
    """A caller's own constraints, after the prompt's.

    Returns `system` unchanged when there are none — identity, not a rebuild —
    so a chat run's prompt is byte-for-byte what it was before `extra_rules`
    existed. Appended last so the prompt's own mandatory rules are read first
    and a caller can only add to them, never restate them differently.
    """
    if not extra_rules.strip():
        return system
    return f"{system}\n\n{extra_rules.strip()}"


async def generate(state: RunState, deps: NodeDeps) -> NodeResult:
    assert state.context is not None
    attempt_no = len(state.attempts) + 1
    # The schema block carries column content hints (value lists, ranges,
    # null fractions) that are customer data, so it is rendered against the
    # same disclosure policy that governs the result in `present`.
    schema_text = state.context.render(state.disclosure_policy)

    # Every SQL-producing prompt gets the same history, on the same disclosure
    # terms as the schema block above it. A repair is a fresh
    # two-message conversation with the model, so anything the first attempt
    # was told and the repair is not is simply lost — which is how the repair
    # path came to be the only one that could not see what the user asked two
    # turns ago.
    history_text = _render_history(state.context.history, state.disclosure_policy)

    # Phase 5's whole surface on this path: one string, empty on every run that
    # matched nothing and on every connection with the feature off — in which
    # case the slot collapses and the prompt is byte-for-byte v8's. Rendered
    # against the same disclosure policy as the schema block above it, because
    # a template's literals are a rung of the same ladder (`docs/security.md`
    # §3.3) and the gate applies at render time, not at retrieval.
    #
    # First attempt only, deliberately. A repair is a fresh conversation about
    # a statement that was *rejected*, and adding four more statements to the
    # prompt that produced the rejected one is the opposite of narrowing.
    examples_text = state.context.render_examples(state.disclosure_policy)

    if attempt_no == 1:
        messages = [
            ChatMessage(
                role="system",
                content=_with_extra_rules(
                    GENERATE_SYSTEM.format(
                        dialect=state.dialect,
                        schema=schema_text,
                        examples=examples_text,
                        history=history_text,
                    ),
                    deps.extra_rules,
                ),
            ),
            ChatMessage(
                role="user", content=GENERATE_USER.format(question=state.question)
            ),
        ]
    else:
        previous = state.attempts[-1]
        # A repair driven by structural checks is a different conversation
        # from a repair driven by rejection: the SQL was legal and it ran, so
        # telling the model it was "rejected by a validator" would be a lie
        # that invites it to fix the wrong thing.
        triggering = [f for f in previous.findings if f.retry]
        if previous.report.status == "VALID" and triggering:
            # Only the finding that *earned* the retry is quoted back. An
            # advisory finding is advisory in both directions: it may not
            # start a regeneration, and it may not steer one that some other
            # check started. Passing the whole list is what let an advisory
            # soft-delete note add a `WHERE is_deleted = false` the question
            # never asked for, turning a correct answer into a wrong one.
            feedback = "\n".join(f.to_feedback() for f in triggering)
            system = REVIEW_SYSTEM.format(
                feedback=feedback,
                schema=schema_text,
                dialect=state.dialect,
                history=history_text,
            )
            preamble = "Your previous SQL was:"
        else:
            feedback = previous.report.to_feedback()
            if previous.db_error:
                feedback += f"\nThe database also reported: {previous.db_error}"
            system = REPAIR_SYSTEM.format(
                feedback=feedback,
                schema=schema_text,
                dialect=state.dialect,
                history=history_text,
            )
            preamble = "Your rejected SQL was:"
        messages = [
            # A repair is a fresh conversation, so anything the first attempt
            # was told and the repair is not is simply lost. That is exactly
            # how the mandatory rules once went missing from this path — and a
            # report block repaired without its time rules would come back
            # with the literal date the rules exist to forbid.
            ChatMessage(
                role="system", content=_with_extra_rules(system, deps.extra_rules)
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Question: {state.question}\n\n"
                    f"{preamble}\n{previous.raw_sql}"
                ),
            ),
        ]

    started = time.perf_counter()
    try:
        proposal = await deps.llm_gateway.structured(deps.llm, messages, SqlProposal)
    except LLMError as err:
        state.error = RunError(
            code="E_LLM",
            message="The model could not produce a query.",
            hint=err.message,
        )
        return NodeResult(status="FAILED", detail=err.message)

    state.llm_latency_ms += int((time.perf_counter() - started) * 1000)

    state.attempts.append(
        SqlAttempt(
            attempt_no=attempt_no,
            raw_sql=proposal.sql.strip().rstrip(";"),
            report=ValidationReport(),
        )
    )
    await deps.emit(
        "SQL_GENERATED",
        {"attempt_no": attempt_no, "sql": state.attempts[-1].raw_sql},
    )
    return NodeResult(detail=f"Attempt {attempt_no} drafted")


# ── validate ─────────────────────────────────────────────────────────────
async def validate(state: RunState, deps: NodeDeps) -> NodeResult:
    attempt = state.attempts[-1]
    report, executable = guard(attempt.raw_sql, deps.policy)
    attempt.report = report
    attempt.rewritten_sql = executable

    if report.status != "VALID":
        codes = [i.rule_id for i in report.errors]
        await deps.emit(
            "SQL_REJECTED",
            {
                "attempt_no": attempt.attempt_no,
                "issues": [i.model_dump() for i in report.errors],
            },
        )
        if state.repair_count < state.max_repairs:
            return NodeResult(
                status="OK", goto="generate", detail=f"Rejected: {', '.join(codes)}"
            )
        if (restored := _restore_superseded(state)) is not None:
            return restored
        first = report.errors[0]
        state.error = RunError(code=first.rule_id, message=first.message, hint=first.hint)
        return NodeResult(status="FAILED", detail=f"Rejected: {', '.join(codes)}")

    await deps.emit(
        "SQL_VALIDATED",
        {
            "attempt_no": attempt.attempt_no,
            "sql": executable,
            "referenced_tables": report.referenced_tables,
            "limit_applied": report.limit_applied,
        },
    )
    return NodeResult(detail=f"Valid · {len(report.referenced_tables)} tables")


# ── execute ──────────────────────────────────────────────────────────────
async def execute(state: RunState, deps: NodeDeps) -> NodeResult:
    attempt = state.attempts[-1]
    sql = attempt.rewritten_sql
    assert sql is not None

    scanned = await deps.connector.explain(sql)

    try:
        result = await deps.connector.execute(
            sql,
            max_rows=state.max_rows,
            statement_timeout_ms=state.statement_timeout_ms,
        )
    except ConnectorError as err:
        attempt.db_error = err.message
        if state.repair_count < state.max_repairs:
            return NodeResult(status="OK", goto="generate", detail=err.message)
        if (restored := _restore_superseded(state)) is not None:
            return restored
        state.error = RunError(
            code="E_QUERY_FAILED",
            message="The query could not be run against the database.",
            hint=err.message,
        )
        return NodeResult(status="FAILED", detail=err.message)

    state.db_latency_ms += result.duration_ms
    state.execution = ExecutionResult(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        duration_ms=result.duration_ms,
        rows_scanned_estimate=scanned,
    )
    await deps.emit(
        "QUERY_COMPLETED",
        {
            "row_count": result.row_count,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "rows_scanned_estimate": scanned,
        },
    )
    # The rows themselves, live — `present` is the slowest node in the run and
    # it is writing a sentence *about* this table, so there is no reason for
    # the reader to wait out the paragraph before seeing the numbers. Transient
    # (`TRANSIENT_RUN_EVENTS`): the durable copy is the TABLE artifact
    # `_finalise` writes, and this is deliberately the same shape so one
    # renderer serves the preview and the record.
    #
    # A repair re-enters this node and emits again, so the last preview is
    # always the last result that actually ran — and a retry that *fails* never
    # reaches here, which is what leaves the restored earlier result on screen
    # rather than a table the run went on to discard.
    await deps.emit(
        "RESULT_PREVIEW",
        {
            "columns": [
                {"name": c.name, "db_type": c.db_type,
                 "semantic_type": c.semantic_type}
                for c in result.columns
            ],
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
        },
    )
    return NodeResult(detail=f"{result.row_count} rows in {result.duration_ms}ms")


# ── inspect ──────────────────────────────────────────────────────────────
def _restore_superseded(state: RunState) -> NodeResult | None:
    """Undo a check-driven retry that ended worse than where it started.

    A structural check is a suspicion, so it is never allowed to cost the user
    a working answer: if the retry it prompted cannot be validated or run, the
    result that triggered the check is put back and the run continues to
    `present` as if the retry had not happened.
    """
    if state.superseded_execution is None:
        return None
    state.execution = state.superseded_execution
    state.superseded_execution = None
    state.error = None
    return NodeResult(
        status="OK", goto="present", detail="Retry failed; kept the earlier result"
    )


async def inspect(state: RunState, deps: NodeDeps) -> NodeResult:
    """Structural checks over a result that ran. No model call, no result data.

    Fail-open like `chart`: a check that cannot run leaves the answer alone.
    """
    execution = state.execution
    if execution is None or not state.attempts:
        return NodeResult(status="SKIPPED", detail="Nothing to inspect")

    from app.pipeline.checks import inspect_result

    attempt = state.attempts[-1]
    findings = inspect_result(
        question=state.question,
        sql=attempt.rewritten_sql or attempt.raw_sql,
        dialect=state.dialect,
        tables=state.context.tables if state.context else [],
        row_count=execution.row_count,
        column_count=len(execution.columns),
        truncated=execution.truncated,
    )
    attempt.findings = findings

    if not findings:
        # A retry that cleared the findings has done its job; drop the fallback.
        state.superseded_execution = None
        return NodeResult(detail="No issues found")

    await deps.emit(
        "RESULT_CHECKED",
        {
            "attempt_no": attempt.attempt_no,
            "findings": [f.model_dump() for f in findings],
        },
    )
    codes = ", ".join(f.code for f in findings)

    retryable = [f for f in findings if f.retry]
    if (
        retryable
        and not state.check_repair_used
        and state.repair_count < state.max_repairs
    ):
        # One retry only, and only from a clean repair budget — a check must
        # never eat the allowance the guard and the database have first claim
        # on, and two rounds of structural nudging is guesswork.
        state.check_repair_used = True
        state.superseded_execution = execution
        return NodeResult(status="OK", goto="generate", detail=f"Retrying: {codes}")

    # Out of budget, or the retry came back with findings of its own. Either
    # way the result stands; the findings are on the record via the event and
    # the step trail.
    state.superseded_execution = None
    return NodeResult(detail=f"Noted: {codes}")


# ── present ──────────────────────────────────────────────────────────────
def _render_caveats(findings: list[Finding]) -> str:
    """Inspect's findings, in the words the reader of the answer needs.

    Only `.message` is used. `.hint` is repair guidance addressed to the
    generator ("use a LEFT JOIN unless…"), which is meaningless to a business
    user and would read as an instruction the answer failed to follow.

    Returns "" when there is nothing to say, and the leading blank line is part
    of the block, so a run with no findings renders a prompt byte-identical to
    the one this node sent before caveats existed.
    """
    lines = [f"- {f.message}" for f in findings if f.message]
    if not lines:
        return ""
    return "\n\nCaveats about this result:\n" + "\n".join(lines)


@dataclass(slots=True)
class _ChartAhead:
    """A chart intent already being fetched while the answer is being written.

    `chart` and `present` ask two independent questions of the same model —
    *what should this be drawn as* and *what does it say* — and neither reads
    the other's answer: the chart is planned from the executed result, and the
    prose is written from the same rows. Run one after the other they cost the
    reader the sum, and the second half of that wait is spent staring at a
    finished answer while a step chip spins. Measured on this install: a median
    7.7s, and 106s at the tail.

    So `present` starts the call and `chart` awaits it. What is deliberately
    *not* moved is the veto: `unchartable_reason` still runs first, so a result
    no chart can describe still costs no tokens — starting the call earlier
    must not mean starting one that would never have been made.

    `profile` travels with the task because the veto needed it anyway, and
    profiling walks every returned row.
    """

    profile: Any
    #: `unchartable_reason`'s verdict. Not None means no call was started.
    blocked: str | None
    task: asyncio.Task[Any] | None

    async def intent(self) -> Any:
        """The model's suggestion, or None — never an exception.

        `propose_chart_intent` is fail-open by contract, so this is defensive
        rather than load-bearing: a head start that turned an unrelated bug
        into a failed run would have made the product worse to make it faster.
        """
        if self.task is None:
            return None
        try:
            return await self.task
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.warning("chart_intent_ahead_failed", exc_info=True)
            return None

    def abandon(self) -> None:
        """Stop the call nobody is going to read."""
        if self.task is not None and not self.task.done():
            self.task.cancel()


def _start_chart_intent(state: RunState, deps: NodeDeps) -> None:
    """Ask what this result should be drawn as, without waiting for the answer.

    Called by `present`, which then streams for as long as the model takes to
    write a paragraph — the whole of which this call now runs inside. It is
    idempotent: `present` can be re-entered by a restore edge, and a second
    head start would strand the first task.
    """
    from app.charts import profile_result, unchartable_reason

    if state.chart_ahead is not None:
        return
    execution = state.execution
    if execution is None or execution.row_count == 0:
        return

    profile = profile_result(
        execution.columns, execution.rows, truncated=execution.truncated
    )
    blocked = unchartable_reason(profile)
    task = (
        None
        if blocked is not None
        else asyncio.create_task(
            propose_chart_intent(
                deps,
                question=state.question,
                profile=profile,
                row_count=execution.row_count,
                truncated=execution.truncated,
                policy=state.disclosure_policy,
                log_context={"run_id": str(state.run_id)},
            )
        )
    )
    state.chart_ahead = _ChartAhead(profile=profile, blocked=blocked, task=task)


def cancel_chart_ahead(state: RunState) -> None:
    """Drop a head start the run never reached `chart` to collect.

    Called from the pipeline facade's `finally`, so a run that failed, timed
    out, looped or was cancelled between `present` and `chart` does not leave a
    provider call running with nobody to read it.
    """
    ahead = state.chart_ahead
    if ahead is not None:
        state.chart_ahead = None
        ahead.abandon()


async def present(state: RunState, deps: NodeDeps) -> NodeResult:
    from app.pipeline.disclosure import disclose

    assert state.execution is not None
    state.disclosed = disclose(state.execution, state.disclosure_policy)

    # Before the stream opens, not after it closes: the chart's model call runs
    # inside the time this node spends writing the answer. See `_ChartAhead`.
    _start_chart_intent(state, deps)

    # Only the attempt being presented. Findings are recorded on the attempt
    # `inspect` looked at and never accumulated across retries, so a suspicion
    # a retry cleared cannot resurface in the sentence the user reads.
    attempt = state.last_attempt
    caveats = _render_caveats(attempt.findings if attempt else [])

    messages = [
        ChatMessage(role="system", content=ANSWER_SYSTEM),
        ChatMessage(
            role="user",
            content=ANSWER_USER.format(
                question=state.question,
                sql=state.executable_sql or "",
                row_count=state.execution.row_count,
                result=state.disclosed.render(),
                caveats=caveats,
            ),
        ),
    ]

    buffer: list[str] = []
    thinking = _Thinking(deps.emit)
    try:
        async for chunk in deps.llm_gateway.stream(deps.llm, messages):
            if chunk.reasoning:
                await thinking.add(chunk.reasoning)
                continue
            await thinking.flush()
            buffer.append(chunk.text)
            await deps.emit("TEXT_DELTA", {"text": chunk.text})
    except LLMError as err:
        # The data is already correct; a narration failure should not lose it.
        log.warning("answer_stream_failed", error=err.message)
        if buffer:
            # Every delta above is already on the live bus *and* durably stored
            # for Last-Event-ID replay, so discarding them from `state.answer`
            # is not enough: a client would show half a sentence with the
            # fallback stitched onto the end. This says "clear what you have
            # rendered for this answer, then render what follows". Nothing is
            # emitted when the stream failed before yielding — there is then no
            # partial text to clear. The web client handles it in
            # `frontend/src/pages/ChatPage.tsx`; any other consumer that does
            # not will simply ignore an unknown event type and keep today's
            # concatenated text.
            await deps.emit("TEXT_RESET", {"reason": "stream_failed"})
        fallback = (
            f"The query returned {state.execution.row_count} rows. "
            "I could not generate a written summary for this result."
        )
        buffer = [fallback]
        await deps.emit("TEXT_DELTA", {"text": fallback})

    await thinking.flush()
    state.answer = "".join(buffer).strip()
    return NodeResult(detail="Answer written" + thinking.note())


# ── chart ──────────────────────────────────────────────────────────────────
async def propose_chart_intent(
    deps: NodeDeps,
    *,
    question: str,
    profile: ResultProfile,
    row_count: int,
    truncated: bool,
    policy: str = DisclosurePolicy.NONE,
    composed: bool = False,
    log_context: dict[str, str] | None = None,
) -> ChartIntent | None:
    """Ask the model what this result should be drawn as. None if it could not say.

    The one place `CHART_SYSTEM` is sent, for both of its triggers: the `chart`
    node at the end of a chat run, and a dashboard tile's draft. Keeping it one
    function is what keeps [security.md §2](../../../docs/security.md)'s
    inventory of call sites true — a second trigger is a row's worth of change
    there, a second `structured` call would be a new line to audit.

    **`composed` is the difference between the two questions.** Chat asks "does
    this result deserve a picture?" and `"none"` is a good answer; a tile has
    already been told there will be one, and `validate_intent` refuses `"none"`
    outright — so on that path declining does not produce a table, it produces
    the shape heuristic's pick with the question's meaning thrown away. The
    composed rules say so in the prompt; `plan_chart` catches a model that
    ignores them, because a refused intent falls back to that same heuristic and
    is therefore never worse than not asking.

    **`policy` defaults to the narrowest**, the same convention `describe`,
    `disclose_history` and `HintBudget.from_policy` follow. Every rule in the
    prompt is written in counts, ratios and grain, which are facts about shape
    and travel under every policy; the one row value in that block is a numeric
    column's `min`/`max`, and a caller that wants it shared has to say so. The
    chat node passes the run's policy because a chat result already reaches a
    model through `present`. A tile draft passes nothing, which is what lets
    [pipeline-dashboard.md §5](../../../docs/pipeline-dashboard.md) keep saying
    that no result value ever reaches a model on the dashboard path — at any
    policy, including `FULL`.

    Fail-open by contract: a provider error, or a model that cannot emit a valid
    nested `ChartIntent` (common with small models), returns None and every
    caller falls through to the deterministic shape heuristic. This function
    never raises `LLMError`.
    """
    from app.charts import ChartIntent

    system = CHART_SYSTEM_COMPOSED if composed else CHART_SYSTEM
    template = CHART_USER_COMPOSED if composed else CHART_USER
    try:
        return await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(role="system", content=system),
                ChatMessage(
                    role="user",
                    content=template.format(
                        question=question,
                        row_count=row_count,
                        truncated=(
                            " (capped: the query returned at least this many)"
                            if truncated
                            else ""
                        ),
                        columns=profile.describe(policy),
                    ),
                ),
            ],
            ChartIntent,
        )
    except LLMError as err:
        log.warning("chart_intent_failed", error=err.message, **(log_context or {}))
        return None


async def chart(state: RunState, deps: NodeDeps) -> NodeResult:
    """Let the model choose a chart for the result, then fit it and compile.

    Best-effort and fail-closed for the chart alone: the answer and the table
    are already persisted, so any failure here just yields no chart.

    The model sees the result's *shape* — column names and types, cardinality,
    time grain, and the crossing and scale arithmetic the chart rules are
    written in terms of. Counts and ratios are shared under every policy; the
    one part of that block that is a row value, a numeric column's `min`/`max`,
    is gated by the same `HintBudget` the schema block uses, which is why
    `state.disclosure_policy` is passed to `describe` rather than the block
    being assumed safe. Charting therefore widens nothing the connection had
    not already agreed to.

    The model's answer is a suggestion, not a verdict: `plan_chart` measures the
    result first, refuses outright when the data cannot say anything, repairs a
    salvageable intent, and falls back to the shape heuristic otherwise.
    """
    from app.charts import (
        compile_vega_lite,
        plan_chart,
        plan_kpi,
        profile_result,
        unchartable_reason,
    )

    execution = state.execution
    if execution is None or execution.row_count == 0:
        return NodeResult(status="SKIPPED", detail="Nothing chartable")

    # Usually already done: `present` profiled the result and started the model
    # call before it wrote a word. The fallback below is not dead code — the
    # draft graph reaches this node without a `present`, and so does any future
    # caller — so this node still works alone, it just waits longer.
    ahead = state.chart_ahead
    state.chart_ahead = None
    if ahead is not None:
        profile, blocked = ahead.profile, ahead.blocked
    else:
        profile = profile_result(
            execution.columns, execution.rows, truncated=execution.truncated
        )
        blocked = unchartable_reason(profile)

    # Ask the data before asking the model: a single row, a constant measure or
    # a result with no dimension cannot become a chart whatever the model says,
    # so the call is skipped entirely — no tokens, no latency, and the step
    # trail shows a fact about the result instead of "the model declined".
    # `_start_chart_intent` applies this same verdict before it starts
    # anything, which is what keeps the head start from spending the tokens
    # this veto exists to save.
    if blocked is not None:
        # One veto is not really about the data being uninteresting. "A single
        # row is a value, not a chart" is a correct statement about plotting
        # and a poor outcome for the reader, because a single-row result is the
        # shape a KPI is *made* of. So that one case gets a second question —
        # is there a number here worth drawing large? — before the turn ends
        # with nothing to look at.
        #
        # Only that case. The other vetoes describe results where no number is
        # worth enlarging either: a thousand tied totals drawn big is still a
        # thousand tied totals, and an id is not a metric however large it is
        # set. Rescuing those would trade "no picture" for a confident wrong
        # one.
        if execution.row_count != 1:
            return NodeResult(status="SKIPPED", detail=blocked)
        kpi = plan_kpi(profile, execution.columns, execution.rows)
        if kpi is None:
            return NodeResult(status="SKIPPED", detail=blocked)
        state.kpi = kpi.model_dump(mode="json")
        await deps.emit("ARTIFACT_CREATED", {"kind": "KPI"})
        return NodeResult(detail="big number")

    suggestion = (
        await ahead.intent()
        if ahead is not None
        else await propose_chart_intent(
            deps,
            question=state.question,
            profile=profile,
            row_count=execution.row_count,
            truncated=execution.truncated,
            policy=state.disclosure_policy,
            log_context={"run_id": str(state.run_id)},
        )
    )

    plan = plan_chart(profile, suggestion)
    if plan.intent is None:
        return NodeResult(status="SKIPPED", detail=plan.reason or "No chart fits")

    state.chart = compile_vega_lite(
        plan.intent, profile, execution.columns, execution.rows
    )
    await deps.emit(
        "ARTIFACT_CREATED",
        {"kind": "CHART", "chart_type": plan.intent.chart_type, "source": plan.source},
    )
    return NodeResult(detail=f"{plan.intent.chart_type} chart ({plan.source})")
