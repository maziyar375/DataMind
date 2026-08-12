"""Plain-language authoring: a question in, an editable SQL draft out.

This is how a tile normally gets its SQL, and it is deliberately *not* a run.
There is no conversation, no `messages` row, no `runs` row, no SSE, no step
trail — a draft is a thing the user is looking at, and if they close the editor
it should leave nothing behind.

What it does reuse is the part that matters: `retrieve` → `generate ⇄
validate`, the same nodes a question in chat goes through **and now the same
compiled repair region** (`app/pipeline/graph.py`), so a drafted statement is
written against the same schema block, the same semantic layer and the same
disclosure budget, and refused by the same guard. The preview under the editor
is produced by `execute_saved_sql` — the code that will run the tile at 03:00 —
so what the user approves is what will actually run.

That sharing is Phase 2 of `docs/langgraph-migration.md`, and it is worth
knowing why. This module used to drive those nodes with a `for` loop of its
own, which made it a **second executor over one node set**: every change to
repair semantics had to be made twice or diverge silently, and it did —
`RunState.deadline_at` was enforced on the chat path and inert here until
someone noticed and closed the gap by hand. What a draft legitimately does
differently now travels in the invoke config (`_deadline_gate`, `_OUT_OF_SCOPE`,
no step sink) rather than living in a parallel implementation.

The second entry point, `validate_sql`, is the hand-written path *and* the "I
edited what the model gave me" path, because they are the same thing: guard,
preview, no model. A user with no LLM provider configured can build a whole
dashboard through it.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import LLMError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.ports.database import DatabaseConnector
from app.domain.value_objects import StepName
from app.infra.db.models import DatabaseConnection, LlmConfig
from app.infra.llm.litellm_gateway import LiteLLMGateway
from app.pipeline.graph import draft_statement
from app.pipeline.nodes import NodeDeps, propose_chart_intent
from app.pipeline.prompts import METRIC_SQL_RULES
from app.pipeline.state import RunState
from app.services.query_service import (
    TileResult,
    bind_connector,
    execute_saved_sql,
    latest_snapshot,
    policy_from_snapshot,
    resolve_llm,
    secret_box,
)
from app.services.semantic_service import load_document
from app.sqlguard import guard
from app.sqlguard.validator import ValidationReport

log = get_logger(__name__)

# A preview is a sanity check, not a result: fifty rows answer "did this do
# what I meant" and cost the customer's database almost nothing, however wide
# the connection's own cap is.
PREVIEW_MAX_ROWS = 50

# A draft is interactive and the user is watching, so it gets one repair, not
# the run pipeline's budget, and a short deadline of its own rather than
# `run_deadline_seconds` — nothing about a draft is a run.
#
# The deadline is checked by `_check_deadline` below rather than by the chat
# executor's rule, because they are deliberately different checks: a chat run
# is stopped before *every* node, a draft before each `generate` and nowhere
# else. It bounds the *model* calls: one `structured` call can legitimately
# take minutes once the gateway's transient-failure retries and their backoff
# are counted, so without a check between attempts the repair starts however
# long the first attempt took. The preview that follows is bound by the
# connection's own `statement_timeout_ms`, which is real containment rather
# than a budget.
#
# `DRAFT_MAX_REPAIRS` reaches the repair loop as `RunState.max_repairs` — set
# in `_draft_state` — and that is the *only* thing bounding it now that the
# hand-rolled `for` loop is gone. It always was the real bound: `validate` asks
# for a repair only while `repair_count < max_repairs`, so the loop counted to
# the same number twice.
DRAFT_MAX_REPAIRS = 1
DRAFT_DEADLINE_SECONDS = 120

# Why a classified question was refused, in the words the user reads on the
# block. `route`'s own answers are written for a chat reply — one of them is a
# whole table listing — and a stored verdict wants a reason, not a reply. Keyed
# by every label `route` can return except ANALYTICAL, which is the one that
# proceeds.
_OUT_OF_SCOPE = {
    "CHITCHAT": (
        "This is not a question about your data, so there is nothing for a "
        "figure to show. Ask something the tables can answer — “revenue by "
        "month for the last quarter”."
    ),
    "UNSUPPORTED": (
        "This is outside what the database can answer, or asks to change "
        "data. A report only ever reads, and only from the tables this "
        "connection has."
    ),
    "METADATA": (
        "This asks what the schema contains rather than what the data says. "
        "A block has to produce rows, and a list of table names is not a "
        "figure — describe the schema in the section's text instead."
    ),
}


@dataclass(slots=True)
class SqlDraft:
    """A statement the user is about to accept, edit, or throw away."""

    sql: str
    validation_status: str
    validation_report: dict[str, Any]
    referenced_tables: list[str]
    # What the tile should be drawn as, for defaulting the editor's chart
    # pickers. On the hand-written road this is the shape heuristic, free and
    # deterministic; on the plain-language road a model is asked, because the
    # question says things the column types cannot.
    chart_suggestion: dict[str, Any] | None = None
    # Who decided: `model`, `model_adjusted`, `heuristic`, `none`, or None when
    # there was nothing to decide. The editor moves its type picker off *Auto*
    # only for the first two — a heuristic pick is a default for the axes, not
    # an opinion about the type worth overriding Auto's re-planning for.
    chart_source: str | None = None
    # Which types this preview can actually be drawn as, and why not for the
    # rest. The picker disables what will not work rather than offering it and
    # letting the save path demote it with an apology.
    chart_options: list[dict[str, Any]] = field(default_factory=list)
    preview: TileResult | None = None
    question: str | None = None
    llm_config_id: UUID | None = None


def _sql_rules_for(extra_rules: str, tile_type: str | None) -> str:
    """The caller's own SQL constraints, plus any the tile type earns.

    The two sources are disjoint in practice — `extra_rules` is a report
    block's date arithmetic and a report has no tiles — but they compose rather
    than override, because a rule that silently replaced another would be found
    only by reading a prompt nobody prints.

    Order matters: `_with_extra_rules` appends this whole block after the
    prompt's own mandatory rules, so a caller can add to them and never restate
    them differently.
    """
    parts = [part.strip() for part in (extra_rules, _METRIC_RULES.get(tile_type or "", ""))]
    return "\n\n".join(part for part in parts if part)


#: Which tile types append SQL rules of their own. A dict rather than an `if`
#: so adding one is a line here and not a branch to find.
_METRIC_RULES = {"METRIC": METRIC_SQL_RULES}


@dataclass(frozen=True, slots=True)
class _ChartAsk:
    """What `_draft` needs in order to ask a model what to draw.

    Absent on the hand-written road, and that absence is the mechanism rather
    than an oversight: `validate_sql` has no question to ask about and no
    resolved model to ask, so it cannot accidentally acquire a model call. A
    dashboard stays buildable with no LLM provider configured because the type
    system says so, not because a branch remembers to check.
    """

    deps: NodeDeps
    state: RunState
    question: str


async def draft_sql(
    db: AsyncSession,
    settings: Settings,
    *,
    connection_id: UUID,
    llm_config_id: UUID,
    question: str,
    owner_id: UUID,
    extra_rules: str = "",
    classify: bool = False,
    compose_chart: bool = False,
    tile_type: str | None = None,
) -> SqlDraft:
    """Ask a model for SQL that answers `question`, then guard it and run it.

    `extra_rules` are constraints appended to every SQL-producing prompt for
    this draft only — a report block's statement is saved and re-run months
    later, so it may not carry a literal date. Empty by default, and empty
    means the prompt is byte-identical to what a tile draft has always sent.

    `classify` runs `route` first and refuses anything that is not ANALYTICAL,
    raising `QuestionOutOfScopeError`. **Off by default**, so a tile draft
    sends exactly the calls it always sent. It exists because the guard cannot
    answer the question a report block actually asks. The guard reads a
    *statement* — is it a SELECT, do its names resolve, is it safe — and "how
    is the weather" produces a statement that passes all three. Nothing
    downstream of `generate` ever asks whether the question had a data answer
    to begin with, and chat only escapes this because `route` halts the run
    before any SQL is written. This is that same halt, for the one other
    caller whose answer is stored rather than shown.

    `compose_chart` spends a second model call asking what the result should be
    *drawn* as. **Off by default, and the asymmetry with `classify` is the
    point**: that one is on for report blocks and off for tiles, this one is the
    reverse.

    The reason is where the answer *lands*, not whether it could be stored. A
    tile editor has a chart-type picker that a suggestion can pre-select, and
    the tile keeps `chart_config` for as long as it lives. A report block also
    has a `chart_config` column — that much was stated wrongly here at first —
    but it is not set while a block is authored: the block editor has no type
    control and reads neither `chart_suggestion` nor `chart_options` from the
    check response, because a report's chart type is chosen *after* a run, by
    redrawing on the result. So the call would be a token spent on a value with
    no reader. Turning it on for reports means building that picker first.

    `tile_type` is what the editor's type picker is set to, and it is the only
    thing on this path that tells the SQL prompt what the result will be *shown
    as*. It buys two things and costs no extra model call: `METRIC` appends
    `METRIC_SQL_RULES`, and it asks the preview for a KPI so the editor can show
    the big number it is actually going to draw. `None` — a caller that does not
    know, or a report block, which has no tiles — behaves exactly as before.
    """
    connection = await _owned(db, DatabaseConnection, connection_id, owner_id)
    llm_config = await _owned(db, LlmConfig, llm_config_id, owner_id)
    snapshot = await _snapshot_or_refuse(db, connection)

    box = secret_box(settings)
    # One connector for the whole draft: `retrieve`/`generate`/`validate` never
    # touch it, but the preview does, and opening a second one to run fifty
    # rows would be a connection the user never asked for.
    connector = bind_connector(connection, box)
    try:
        state = _draft_state(connection, question)
        deps = NodeDeps(
            llm_gateway=LiteLLMGateway.from_settings(settings),
            llm=resolve_llm(llm_config, box),
            connector=connector,
            snapshot=snapshot,
            # No conversation: a draft has no history to inherit, and inventing
            # one would put another connection's answers in this prompt.
            history=[],
            policy=policy_from_snapshot(snapshot, connection),
            emit=_no_emit,
            semantic=await _semantic(db, connection),
            extra_rules=_sql_rules_for(extra_rules, tile_type),
        )

        # `[route →] retrieve → generate ⇄ validate`, walked by the same
        # compiled region a chat run walks. `classify` selects the entry edge
        # — before `retrieve`, exactly where chat runs it, because the point of
        # the classifier is to spend nothing on a question with no data answer
        # — and the refusal is raised from inside the graph with this module's
        # own wording. `route` fails open to ANALYTICAL on a provider error, so
        # a flaky model widens nothing and refuses nothing.
        await draft_statement(
            state,
            deps,
            classify=classify,
            check_deadline=_deadline_gate,
            out_of_scope=_OUT_OF_SCOPE,
        )
        if state.error is not None and state.error.code == "E_LLM":
            # The one failure that is not a verdict. A *rejected* statement
            # comes back below with its report, because the editor renders the
            # guard's reasons inline; a provider that produced nothing at all
            # leaves no draft to render.
            raise LLMError(
                state.error.hint or "The model could not produce a query."
            )

        attempt = state.attempts[-1]
        return await _draft(
            db,
            settings,
            connection=connection,
            sql=attempt.raw_sql,
            report=attempt.report,
            snapshot=snapshot,
            connector=connector,
            owner_id=owner_id,
            question=question,
            llm_config_id=llm_config.id,
            want_kpi=tile_type == "METRIC",
            ask=(
                _ChartAsk(deps=deps, state=state, question=question)
                if compose_chart
                else None
            ),
        )
    finally:
        await connector.close()


def _deadline_gate(state: RunState, node: str) -> None:
    """The draft's deadline rule, handed to the graph: before each `generate`.

    Not before every node, which is the chat executor's rule and the reason
    this hook takes a node name at all. `validate` is the guard — pure CPU,
    microseconds — and stopping a draft there would throw away a statement the
    model has already been paid for and the guard would have accepted. `route`
    and `retrieve` come before any budget has been spent.

    The difference is deliberate and now has to be *written down* to exist,
    which is the whole point of Phase 2: this rule and the chat run's sit side
    by side in the invoke config instead of in two executors that drifted.
    """
    if node == StepName.GENERATE:
        _check_deadline(state)


def _check_deadline(state: RunState) -> None:
    """Stop before spending another model call the user is no longer waiting for.

    `RunState.deadline_at` is enforced for a chat run by the node adapter in
    `app/pipeline/graph.py`, under a different rule — see `_deadline_gate`
    above. This field was inert on the draft path until the check was written
    here. It is checked in the same place the executor checks it: *before* a
    node, never inside one, because an in-flight provider call is bounded by
    `llm_request_timeout_seconds` and an in-flight query by the connection's
    statement timeout.

    `LLMError` rather than `RunTimeoutError` deliberately: the callers already
    handle it. The tile editor renders it as "the model could not produce a
    query", and `report_service.check_block` stores it as the block's reason —
    which is the honest verdict, since the next click is a re-check either way.
    """
    if utcnow() >= state.deadline_at:
        raise LLMError(
            f"Drafting SQL took longer than {DRAFT_DEADLINE_SECONDS} seconds, "
            "so it was stopped before asking the model again. Try again, or "
            "narrow the question."
        )


async def validate_sql(
    db: AsyncSession,
    settings: Settings,
    *,
    connection_id: UUID,
    sql: str,
    owner_id: UUID,
    tile_type: str | None = None,
) -> SqlDraft:
    """Guard and preview a statement the user wrote or edited. No model.

    Passing here is not authorisation to save, and saving is not authorisation
    to run: the tile save path guards again, and so does every refresh.

    `tile_type` buys the preview's KPI here and nothing else — there is no
    prompt on this road to append rules to. Which is the point: a user writing
    their own `SELECT month, SUM(...)` for a big-number tile sees the delta and
    the sparkline in the editor exactly as the plain-language road does.
    """
    connection = await _owned(db, DatabaseConnection, connection_id, owner_id)
    snapshot = await _snapshot_or_refuse(db, connection)
    report, _ = guard(sql, policy_from_snapshot(snapshot, connection))

    return await _draft(
        db,
        settings,
        connection=connection,
        sql=sql,
        report=report,
        snapshot=snapshot,
        connector=None,
        owner_id=owner_id,
        want_kpi=tile_type == "METRIC",
    )


# ── the shared tail: preview, then chart ─────────────────────────────────
async def _draft(
    db: AsyncSession,
    settings: Settings,
    *,
    connection: DatabaseConnection,
    sql: str,
    report: ValidationReport,
    snapshot: dict[str, Any],
    connector: DatabaseConnector | None,
    owner_id: UUID,
    question: str | None = None,
    llm_config_id: UUID | None = None,
    want_kpi: bool = False,
    ask: _ChartAsk | None = None,
) -> SqlDraft:
    """Attach a preview and a chart suggestion to a statement and its report.

    A rejected statement gets no preview — there is nothing to run — and the
    report is the answer, not an exception: the editor renders the guard's
    reasons inline, exactly as the semantic-layer editor does.
    """
    preview: TileResult | None = None
    if report.status == "VALID":
        preview = await execute_saved_sql(
            db,
            settings,
            # The raw statement, not the guard's rewrite: `execute_saved_sql`
            # re-guards from scratch, and previewing the rewrite would preview
            # something the tile will never be asked to run.
            sql=sql,
            connection=connection,
            owner_id=owner_id,
            max_rows=PREVIEW_MAX_ROWS,
            connector=connector,
            snapshot=snapshot,
            # So the editor can show the big number it will actually draw.
            # `plan_kpi` is a pure function over rows already fetched, so the
            # only reason this was ever withheld is the one `execute_saved_sql`
            # states: profiling a wide TABLE tile to build a KPI nobody looks
            # at is work with no reader. A METRIC tile has the reader.
            want_kpi=want_kpi,
        )

    suggestion, source = await _chart_suggestion(preview, ask)
    return SqlDraft(
        sql=sql,
        validation_status=report.status,
        validation_report=report.model_dump(mode="json"),
        referenced_tables=list(report.referenced_tables),
        chart_suggestion=suggestion,
        chart_source=source,
        chart_options=_chart_options(preview),
        preview=preview,
        question=question,
        llm_config_id=llm_config_id,
    )


async def _chart_suggestion(
    preview: TileResult | None, ask: _ChartAsk | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """What this tile should be drawn as, and who decided.

    Two roads meet here. Without `ask` — the hand-written road, and every
    re-validation of edited SQL — this is the *heuristic* alone: no model is
    called, which is what lets a user with no LLM provider configured build a
    whole dashboard. With `ask`, the model is asked the question a shape cannot
    answer: not "what can this be drawn as" but "what did the person mean to
    see". The difference is the whole reason a tile written from a sentence used
    to come out as a bar chart every time — two columns *can* always be a bar,
    so the heuristic always said bar.

    Three properties, in the order they matter:

    1. **The veto still runs before the model**, exactly as it does in the chat
       node: a result no chart could serve costs no tokens and no latency.
    2. **It cannot fail the draft.** A provider error, a deadline already spent,
       an unparseable intent — each falls back to the heuristic's pick, which is
       what this function returned for every draft before the model was asked.
       The statement is already valid and its preview already ran; losing a
       picker default to a presentation failure would break the rule in
       [pipeline.md §4.1](../../../docs/pipeline.md) that a step which has
       produced correct data may not lose it to one.
    3. **The model's answer is never stored raw.** It goes through `plan_chart`
       like any other intent, so it gets the same name check and shape repair a
       user's explicit pick gets — and a model that ignores the composed rules
       and answers `"none"` is refused there and falls back to the heuristic,
       which is why asking is never worse than not asking.

    `source` is `plan_chart`'s own word for who decided (`model`,
    `model_adjusted`, `heuristic`, `none`). The editor needs it to tell a pick
    that read the question from one that read only the column types: only the
    first is worth moving the type picker off *Auto* for.
    """
    if preview is None or preview.status != "OK" or len(preview.columns) < 2:
        return None, None

    from app.charts import plan_chart, profile_result, unchartable_reason

    try:
        profile = profile_result(
            preview.columns, preview.rows, truncated=preview.truncated
        )
    except Exception:  # noqa: BLE001 — a defaulted picker is never worth a 500
        log.exception("draft_chart_profile_failed")
        return None, None

    suggestion = None
    if ask is not None and unchartable_reason(profile) is None:
        try:
            # The same check `generate` gets, for the same reason: one
            # `structured` call can take minutes once the gateway's retries and
            # their backoff are counted, and by here the user has already waited
            # for a draft and a preview.
            _check_deadline(ask.state)
            suggestion = await propose_chart_intent(
                ask.deps,
                question=ask.question,
                profile=profile,
                row_count=preview.row_count,
                truncated=preview.truncated,
                # No policy argument, deliberately: the narrowest budget, at
                # every policy. Shape is all the chart rules are written in, so
                # this costs the decision nothing and keeps "no result value
                # ever reaches a model on the dashboard path" true as stated.
                composed=True,
            )
        except Exception:  # noqa: BLE001 — the heuristic below is the fallback
            log.exception("draft_chart_intent_failed")

    try:
        plan = plan_chart(profile, suggestion)
    except Exception:  # noqa: BLE001 — a defaulted picker is never worth a 500
        log.exception("draft_chart_suggestion_failed")
        return None, None

    if plan.intent is None:
        return None, plan.source
    return plan.intent.model_dump(mode="json"), plan.source


def _chart_options(preview: TileResult | None) -> list[dict[str, Any]]:
    """Per-type verdicts for the editor's picker.

    Empty when there is nothing to judge — no preview, or a result too narrow
    to chart at all. The editor reads an empty list as "no opinion" and leaves
    every type enabled, which is the same thing it did before this existed: a
    picker that greys everything out because the preview failed would be worse
    than one that lets the save path answer.
    """
    if preview is None or preview.status != "OK" or len(preview.columns) < 2:
        return []

    from app.charts import chart_options, profile_result

    try:
        profile = profile_result(
            preview.columns, preview.rows, truncated=preview.truncated
        )
        return [asdict(option) for option in chart_options(profile)]
    except Exception:  # noqa: BLE001 — same posture as the suggestion above
        log.exception("draft_chart_options_failed")
        return []


# ── loading ──────────────────────────────────────────────────────────────
async def _owned(db: AsyncSession, model: type, entity_id: UUID, owner_id: UUID) -> Any:
    entity = await db.get(model, entity_id)
    if entity is None or entity.owner_id != owner_id:
        raise NotFoundError(f"{model.__name__} not found.")
    return entity


async def _snapshot_or_refuse(
    db: AsyncSession, connection: DatabaseConnection
) -> dict[str, Any]:
    """An unsynced connection is a message, not an empty prompt.

    Without this the model would be handed a schema block with no tables in it
    and asked to write SQL anyway — spending a call to produce a statement the
    guard is then guaranteed to reject.
    """
    snapshot = await latest_snapshot(db, connection.id)
    if not snapshot.get("tables"):
        raise ValidationError(
            "Sync this connection's schema before drafting SQL against it.",
            connection_id=str(connection.id),
        )
    return snapshot


async def _semantic(
    db: AsyncSession, connection: DatabaseConnection
) -> dict[str, Any] | None:
    """The connection's semantic layer, on exactly the run path's terms.

    A draft is not a loophole around the layer's switch, nor around the
    disclosure budget `retrieve` renders it under.
    """
    document = await load_document(db, connection)
    return document.model_dump(mode="json") if document else None


def _draft_state(connection: DatabaseConnection, question: str) -> RunState:
    """A `RunState` for something that is not a run.

    Known wart: `RunState` requires `run_id` and `conversation_id`, and a draft
    has neither. Synthetic UUIDs are the honest cheap answer — they are never
    persisted, never emitted, and never looked up. The alternative, making both
    fields optional, would weaken the type for every real run to serve a path
    that writes nothing.
    """
    return RunState(
        run_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        owner_id=connection.owner_id,
        connection_id=connection.id,
        question=question,
        dialect=connection.database_type,
        max_rows=PREVIEW_MAX_ROWS,
        max_repairs=DRAFT_MAX_REPAIRS,
        statement_timeout_ms=connection.statement_timeout_ms,
        disclosure_policy=connection.disclosure_policy,
        deadline_at=utcnow() + timedelta(seconds=DRAFT_DEADLINE_SECONDS),
    )


async def _no_emit(_event_type: str, _data: dict[str, Any]) -> None:
    """A draft has no run to attach events to and no client listening."""
    return None
