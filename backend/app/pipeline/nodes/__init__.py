"""The nodes.

Each is `async def node(state, deps) -> NodeResult`. Nodes mutate the typed
state and report status; they never touch persistence and never decide what
happens next beyond an optional `goto`. Ordering lives in the executor.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.errors import ConnectorError, LLMError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector
from app.domain.ports.llm import ChatMessage, LLMGateway, ResolvedLLM
from app.domain.value_objects import DisclosurePolicy, HintBudget
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
    CHART_USER,
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
)
from app.sqlguard import GuardPolicy, guard
from app.sqlguard.validator import ValidationReport

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
    failed = False
    try:
        async for delta in deps.llm_gateway.stream(deps.llm, messages):
            buffer.append(delta)
            await deps.emit("TEXT_DELTA", {"text": delta})
    except LLMError as err:
        log.warning(
            "describe_stream_failed", run_id=str(state.run_id), error=err.message
        )
        failed = True

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
    """
    if not deps.clarify_enabled:
        return NodeResult(status="SKIPPED", detail="Clarification off for this run")

    assert state.context is not None
    started = time.perf_counter()
    history_text = _render_history(state.context.history, state.disclosure_policy)

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
        )
    except (LLMError, ValueError) as err:
        log.warning("clarify_failed", run_id=str(state.run_id), error=str(err))
        return NodeResult(status="SKIPPED", detail="Clarification check unavailable")

    elapsed = int((time.perf_counter() - started) * 1000)
    question = " ".join(proposal.question.split())
    if proposal.answerable or not question:
        return NodeResult(detail=f"Answerable as asked, in {elapsed}ms")

    state.clarification = ClarificationRequest(
        question=question, options=_clean_options(proposal.options)
    )
    # The question *is* the answer for this turn: it becomes the assistant
    # message, so the thread reads as a conversation rather than a dead run.
    state.answer = question
    await deps.emit(
        "CLARIFICATION_REQUESTED", state.clarification.model_dump(mode="json")
    )
    return NodeResult(status="HALT", detail=f"Asked the user in {elapsed}ms")


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

    if attempt_no == 1:
        messages = [
            ChatMessage(
                role="system",
                content=_with_extra_rules(
                    GENERATE_SYSTEM.format(
                        dialect=state.dialect, schema=schema_text, history=history_text
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


async def present(state: RunState, deps: NodeDeps) -> NodeResult:
    from app.pipeline.disclosure import disclose

    assert state.execution is not None
    state.disclosed = disclose(state.execution, state.disclosure_policy)

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
    try:
        async for delta in deps.llm_gateway.stream(deps.llm, messages):
            buffer.append(delta)
            await deps.emit("TEXT_DELTA", {"text": delta})
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

    state.answer = "".join(buffer).strip()
    return NodeResult(detail="Answer written")


# ── chart ──────────────────────────────────────────────────────────────────
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
        ChartIntent,
        compile_vega_lite,
        plan_chart,
        plan_kpi,
        profile_result,
        unchartable_reason,
    )

    execution = state.execution
    if execution is None or execution.row_count == 0:
        return NodeResult(status="SKIPPED", detail="Nothing chartable")

    profile = profile_result(
        execution.columns, execution.rows, truncated=execution.truncated
    )

    # Ask the data before asking the model: a single row, a constant measure or
    # a result with no dimension cannot become a chart whatever the model says,
    # so the call is skipped entirely — no tokens, no latency, and the step
    # trail shows a fact about the result instead of "the model declined".
    if (blocked := unchartable_reason(profile)) is not None:
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

    # A provider error, or a model that cannot emit a valid nested ChartIntent
    # (common with small models), falls through to a deterministic choice from
    # the data shape, so a chart still appears and still varies by question.
    suggestion: ChartIntent | None = None
    try:
        suggestion = await deps.llm_gateway.structured(
            deps.llm,
            [
                ChatMessage(role="system", content=CHART_SYSTEM),
                ChatMessage(
                    role="user",
                    content=CHART_USER.format(
                        question=state.question,
                        row_count=execution.row_count,
                        truncated=(
                            " (capped: the query returned at least this many)"
                            if execution.truncated
                            else ""
                        ),
                        columns=profile.describe(state.disclosure_policy),
                    ),
                ),
            ],
            ChartIntent,
        )
    except LLMError as err:
        log.warning("chart_intent_failed", run_id=str(state.run_id), error=err.message)

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
