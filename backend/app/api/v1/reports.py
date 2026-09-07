"""HTTP shape for reports. No business logic — see
`services/report_service.py`.

Three things about this route table are deliberate:

* **Literal segments are declared above `/{...}` ones** — `/reports/{id}/blocks`
  before `/reports/{id}`'s siblings could swallow it — the house rule that keeps
  a literal from being read as a path parameter.
* **Every write returns the written row, resolved.** The page splices what comes
  back into its state instead of re-reading, which is the only way past the
  read-after-write race documented at the end of `docs/dashboards.md`.
* **Blocks are addressed flatly** (`/reports/{id}/blocks/{bid}`) while they are
  *created* under their section. Editing a block never needs its section, and
  the outline editor reorders blocks between sections often enough that a path
  carrying the section id would go stale in the client's hands.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.deps import AuthzDep, CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    ChartOptionRead,
    ReportBlockCheckRead,
    ReportBlockCreate,
    ReportBlockRead,
    ReportBlockResultRead,
    ReportBlockSqlUpdate,
    ReportBlockUpdate,
    ReportChartRead,
    ReportChartRequest,
    ReportCreate,
    ReportRead,
    ReportRunDetailRead,
    ReportRunRead,
    ReportSectionCreate,
    ReportSectionRead,
    ReportSectionResultRead,
    ReportSectionResultUpdate,
    ReportSectionUpdate,
    ReportSummaryRead,
    ReportUpdate,
    TileResultRead,
)
from app.api.v1.access import attach_access_routes, owner_names
from app.domain.ports.authz import ResourceRef
from app.domain.value_objects.authz import ResourceType
from app.infra.db.models import Report, ReportBlockResult
from app.services import restricted
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])

# `/grants`, `/actions` and `/transfer`, written once in `access.py`. A
# report is bound to exactly **one** connection, which is why it is the
# first artifact type to become shareable: there is no intersection to
# resolve, only the second check — `select` on the report, and `select` on
# the connection behind it.
attach_access_routes(router, ResourceType.REPORT, param="report_id")


async def _report_read(
    service: ReportService,
    report: Report,
    ctx: CtxDep,
    authz: AuthzDep,
) -> ReportRead:
    """The report, its outline, the chips' labels, and this reader's reach.

    Every field *except* `sections` is copied off the row. `Report.sections` is
    a lazy relationship, and `model_validate` would read it — a lazy load inside
    a response, in a context that cannot await, which is `MissingGreenlet`: a
    500 that appears only against a real database.

    `data_access` is the second half of the report's authorization and the
    reason this signature grew: reaching the **report** and reaching the
    **database it was built over** are two questions, and from Phase 8 they can
    have different answers. The editor reads it to disable Generate and Check
    rather than offering buttons whose only outcome is a refusal.
    """
    sections = await service.sections_of(report.id)
    blocks = await service.blocks_of([s.id for s in sections])
    connections, models = await service.display_names([report])
    held = await authz.privileges_on(ctx, ResourceRef.to(ResourceType.REPORT, report))

    by_section: dict[UUID, list[ReportBlockRead]] = {}
    for block in blocks:
        by_section.setdefault(block.section_id, []).append(
            ReportBlockRead.model_validate(block)
        )

    fields = {
        name: getattr(report, name)
        for name in ReportRead.model_fields
        if name
        not in (
            "sections",
            "connection_name",
            "llm_config_name",
            "data_access",
            "privileges",
        )
    }
    return ReportRead(
        **fields,
        connection_name=connections.get(report.connection_id),  # type: ignore[arg-type]
        llm_config_name=models.get(report.llm_config_id),  # type: ignore[arg-type]
        sections=[
            _section_read(section, by_section.get(section.id, []))
            for section in sections
        ],
        data_access=await service.may_read_data(ctx, report),
        privileges=sorted(str(p) for p in held),
    )


def _section_read(section: object, blocks: list[ReportBlockRead]) -> ReportSectionRead:
    fields = {
        name: getattr(section, name)
        for name in ReportSectionRead.model_fields
        if name != "blocks"
    }
    return ReportSectionRead(**fields, blocks=blocks)


# ── reports ──────────────────────────────────────────────────────────────
@router.get("", response_model=list[ReportSummaryRead])
async def list_reports(
    ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> list[ReportSummaryRead]:
    service = ReportService(db, settings, authz)
    reports = await service.list(ctx)
    connections, models = await service.display_names(reports)
    # One query for the page, not one per card: from Phase 8 this list can
    # hold somebody else's reports, and the card has to say whose.
    owners = await owner_names(db, {r.owner_id for r in reports if r.owner_id})

    cards: list[ReportSummaryRead] = []
    for report in reports:
        card = ReportSummaryRead.model_validate(report)
        card.section_count = len(await service.sections_of(report.id))
        if report.connection_id is not None:
            card.connection_name = connections.get(report.connection_id)
        if report.llm_config_id is not None:
            card.llm_config_name = models.get(report.llm_config_id)
        # A badge, not a gate: `visible` already decided this row is
        # reachable, and this only says whether to name an owner.
        card.shared = report.owner_id != ctx.user_id  # authz-ok: a badge
        # The owner's name only on a card the reader does not own — "shared
        # with you by you" is noise on every card somebody made themselves.
        card.owner_name = owners.get(report.owner_id) if card.shared else None
        cards.append(card)
    return cards


@router.post("", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> ReportRead:
    """Create a report. **The disclosure gate is here** — a connection that
    shares no result values cannot carry a document written from them."""
    service = ReportService(db, settings, authz)
    report = await service.create(ctx, **payload.model_dump())
    return await _report_read(service, report, ctx, authz)


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> ReportRead:
    service = ReportService(db, settings, authz)
    report = await service.get(ctx, report_id)
    return await _report_read(service, report, ctx, authz)


@router.patch("/{report_id}", response_model=ReportRead)
async def update_report(
    report_id: UUID,
    payload: ReportUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportRead:
    """Name, description, prompt, model, status. A **different** `connection_id`
    is 422 — the report is pinned to the one it was created against."""
    service = ReportService(db, settings, authz)
    report = await service.update(ctx, report_id, **payload.model_dump(exclude_unset=True))
    return await _report_read(service, report, ctx, authz)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> None:
    await ReportService(db, settings, authz).delete(ctx, report_id)


# ── the outline ──────────────────────────────────────────────────────────
@router.post("/{report_id}/outline", response_model=ReportRead)
async def propose_outline(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> ReportRead:
    """Propose a structure from the request. One model call, synchronous.

    **This replaces the outline.** Returns the whole report so the editor
    renders the proposal from the response rather than re-reading it.
    """
    service = ReportService(db, settings, authz)
    report = await service.propose_outline(ctx, report_id)
    return await _report_read(service, report, ctx, authz)


# ── blocks ───────────────────────────────────────────────────────────────
# Declared above `/{report_id}/sections/...` so `/blocks/{block_id}` is never a
# candidate for a section path, and `/check` above the bare `/{block_id}` for
# the same reason one level down.
@router.post("/{report_id}/blocks/{block_id}/check", response_model=ReportBlockCheckRead)
async def check_block(
    report_id: UUID,
    block_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportBlockCheckRead:
    """*Can this be produced, and if not, why.* Synchronous, one block.

    Answers with a stored verdict rather than an error: `INFEASIBLE` is a
    result, and the guard's own reason travels with it verbatim. "Check all" is
    the frontend looping this with per-block progress.
    """
    block, draft = await ReportService(db, settings, authz).check_block(
        ctx, report_id, block_id
    )
    return ReportBlockCheckRead(
        block=ReportBlockRead.model_validate(block),
        preview=(
            TileResultRead.model_validate(draft.preview.to_payload())
            if draft and draft.preview
            else None
        ),
        chart_suggestion=draft.chart_suggestion if draft else None,
        chart_options=list(draft.chart_options) if draft else [],
    )


@router.put("/{report_id}/blocks/{block_id}/sql", response_model=ReportBlockCheckRead)
async def edit_block_sql(
    report_id: UUID,
    block_id: UUID,
    payload: ReportBlockSqlUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportBlockCheckRead:
    """Write the block's statement by hand. Guarded and previewed, no model.

    The same response as `/check`, because it answers the same question — *can
    this be produced, and if not why* — by the other road. PUT rather than
    PATCH: the statement is replaced whole, and sending it twice is sending it
    once.
    """
    block, draft = await ReportService(db, settings, authz).edit_block_sql(
        ctx, report_id, block_id, sql=payload.sql
    )
    return ReportBlockCheckRead(
        block=ReportBlockRead.model_validate(block),
        preview=(
            TileResultRead.model_validate(draft.preview.to_payload())
            if draft.preview
            else None
        ),
        chart_suggestion=draft.chart_suggestion,
        chart_options=list(draft.chart_options),
    )


@router.patch("/{report_id}/blocks/{block_id}", response_model=ReportBlockRead)
async def update_block(
    report_id: UUID,
    block_id: UUID,
    payload: ReportBlockUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportBlockRead:
    """Editing the question (or the window) resets the block to `UNCHECKED` and
    drops its SQL: the stored statement answered the previous question."""
    block = await ReportService(db, settings, authz).update_block(
        ctx, report_id, block_id, **payload.model_dump(exclude_unset=True)
    )
    return ReportBlockRead.model_validate(block)


@router.delete(
    "/{report_id}/blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_block(
    report_id: UUID,
    block_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> None:
    await ReportService(db, settings, authz).delete_block(ctx, report_id, block_id)


# ── sections ─────────────────────────────────────────────────────────────
@router.post(
    "/{report_id}/sections",
    response_model=ReportSectionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_section(
    report_id: UUID,
    payload: ReportSectionCreate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportSectionRead:
    section = await ReportService(db, settings, authz).add_section(
        ctx, report_id, **payload.model_dump()
    )
    return _section_read(section, [])


@router.patch("/{report_id}/sections/{section_id}", response_model=ReportSectionRead)
async def update_section(
    report_id: UUID,
    section_id: UUID,
    payload: ReportSectionUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportSectionRead:
    service = ReportService(db, settings, authz)
    section = await service.update_section(
        ctx, report_id, section_id, **payload.model_dump(exclude_unset=True)
    )
    blocks = await service.blocks_of([section.id])
    return _section_read(section, [ReportBlockRead.model_validate(b) for b in blocks])


@router.delete(
    "/{report_id}/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_section(
    report_id: UUID,
    section_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> None:
    await ReportService(db, settings, authz).delete_section(ctx, report_id, section_id)


@router.post(
    "/{report_id}/sections/{section_id}/blocks",
    response_model=ReportBlockRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_block(
    report_id: UUID,
    section_id: UUID,
    payload: ReportBlockCreate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportBlockRead:
    block = await ReportService(db, settings, authz).add_block(
        ctx, report_id, section_id, **payload.model_dump()
    )
    return ReportBlockRead.model_validate(block)


# ── runs ─────────────────────────────────────────────────────────────────
# `/runs/{run_id}/cancel` is declared above `/runs/{run_id}` for the reason the
# module docstring gives one level up: the literal must not be read as an id.
@router.post(
    "/{report_id}/runs",
    response_model=ReportRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    report_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportRunRead:
    """Start a generation and return immediately.

    202, not 200: a report is minutes of queries and model calls, so the answer
    to "did it work" lives on the run row the client then polls — the same
    trade `semantic_jobs` makes.
    """
    service = ReportService(db, settings, authz)
    run = await service.create_run(ctx, report_id)
    read = ReportRunRead.model_validate(run)
    # Committed before the worker starts, or the worker races the transaction
    # that created the row it is about to load.
    await db.commit()
    await request.app.state.report_executor.submit(run.id)
    return read


@router.get("/{report_id}/runs", response_model=list[ReportRunRead])
async def list_runs(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep, authz: AuthzDep
) -> list[ReportRunRead]:
    """The report's history, newest first. Rows only — a past run's results are
    read one run at a time."""
    runs = await ReportService(db, settings, authz).runs_of(ctx, report_id)
    return [ReportRunRead.model_validate(run) for run in runs]


@router.post("/{report_id}/runs/{run_id}/cancel", response_model=ReportRunRead)
async def cancel_run(
    report_id: UUID,
    run_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportRunRead:
    """Ask the run to stop. Results already computed are kept.

    The row is marked cancelled here rather than by the worker, so the next
    poll says so even while an in-flight query is still finishing.
    """
    service = ReportService(db, settings, authz)
    await service.cancel_run(ctx, report_id, run_id)
    await db.commit()
    await request.app.state.report_executor.cancel(run_id)
    return ReportRunRead.model_validate(await service.run(ctx, report_id, run_id))


@router.post(
    "/{report_id}/runs/{run_id}/sections/{section_id}/retry",
    response_model=ReportRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_section(
    report_id: UUID,
    run_id: UUID,
    section_id: UUID,
    request: Request,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportRunRead:
    """Rebuild one section of a finished run: its queries, then its paragraph.

    202 and back to the poll the viewer is already running, rather than a
    request held open for half a minute. The rest of the document stays on
    screen, and the run's status is **re-derived** when this lands — which is
    how a successful retry turns `PARTIAL` into `SUCCEEDED` with no state
    machine anywhere.
    """
    service = ReportService(db, settings, authz)
    run = await service.request_section_retry(ctx, report_id, run_id, section_id)
    read = ReportRunRead.model_validate(run)
    await db.commit()
    await request.app.state.report_executor.submit_retry(run_id, section_id)
    return read


@router.post(
    "/{report_id}/runs/{run_id}/blocks/{result_id}/chart",
    response_model=ReportChartRead,
)
async def redraw_block_chart(
    report_id: UUID,
    run_id: UUID,
    result_id: UUID,
    payload: ReportChartRequest,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportChartRead:
    """Draw one saved block a different way, from the rows the run kept.

    Persisted onto the run — a report is printed from its saved run, so a
    chart that lived only in the browser would not survive the export — and
    onto the run *only*, for the reason `edited_prose` is on the run too: a
    refinement made while reading one generation must not rewrite the template
    the next one is produced from.
    """
    row, options, reason = await ReportService(db, settings, authz).redraw_block_chart(
        ctx, report_id, run_id, result_id, chart_type=payload.chart_type
    )
    return ReportChartRead(
        spec=row.vega_spec if reason is None else None,
        chart_source=row.chart_source or "none",
        chart_note=row.chart_note,
        reason=reason,
        options=[ChartOptionRead(**option) for option in options],
    )


@router.patch(
    "/{report_id}/runs/{run_id}/sections/{section_id}",
    response_model=ReportSectionResultRead,
)
async def edit_section_prose(
    report_id: UUID,
    run_id: UUID,
    section_id: UUID,
    payload: ReportSectionResultUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportSectionResultRead:
    """Write over a paragraph. The model's own words are kept beside it.

    The edit belongs to the **run**, not the template: regenerating writes a
    new run and leaves this one's writing exactly where it is.
    """
    row = await ReportService(db, settings, authz).edit_prose(
        ctx, report_id, run_id, section_id, edited_prose=payload.edited_prose
    )
    return ReportSectionResultRead.model_validate(row)


@router.get("/{report_id}/runs/{run_id}", response_model=ReportRunDetailRead)
async def get_run(
    report_id: UUID,
    run_id: UUID,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
    authz: AuthzDep,
) -> ReportRunDetailRead:
    """**The poll target.** The run, and every result written so far.

    Not "the finished document": a run half-way through returns the half it
    has, which is what makes the progressive render need no protocol of its own
    and lets a browser that reloads mid-run resume exactly where it was.
    """
    service = ReportService(db, settings, authz)
    run = await service.run(ctx, report_id, run_id)
    report = await service.get(ctx, report_id)
    blocks, sections = await service.run_results(run.id)
    # Which figures rest on a different query than they did last time. One
    # extra pair of small queries per poll, and it is what makes two
    # generations of the same report comparable rather than merely adjacent.
    previous = await service.previous_block_hashes(run)

    # ── the intersection rule, on a document (§19.2) ──
    #
    # `run_results` reads `report_block_results`, which holds rows taken out of
    # the customer's database — the report's equivalent of the tile cache, and
    # subject to the same rule for the same reason. A reader shared the report
    # and not its connection keeps the structure, the headings and the prose,
    # and every figure becomes a named placeholder. Asked once for the whole
    # document, because a report has exactly one connection.
    reachable = await service.may_read_data(ctx, report)
    message = ""
    if not reachable:
        withheld = (
            {report.connection_id} if report.connection_id is not None else set()
        )
        names = await restricted.connection_names(db, withheld)
        message = restricted.no_access_message(names.get(report.connection_id))
        await restricted.record_denials(
            db,
            ctx,
            connection_ids=withheld,
            on_type=ResourceType.REPORT,
            on_id=report_id,
        )

    return ReportRunDetailRead(
        **{
            name: getattr(run, name)
            for name in ReportRunRead.model_fields
        },
        blocks=[
            _block_result_read(b, previous)
            if reachable
            else _withheld_block(b, message)
            for b in blocks
        ],
        sections=[ReportSectionResultRead.model_validate(s) for s in sections],
    )


def _withheld_block(
    row: ReportBlockResult, message: str
) -> ReportBlockResultRead:
    """A figure the reader may not see, keeping everything that is not data.

    The heading, the caption, the position and the timing survive — so the
    document still reads as a document, the figure numbers still mean
    something, and the reader can say *which* exhibit they need access to. The
    statement goes with the rows: a stored `SELECT` names columns and filter
    values out of a database this reader was not given, which is the same
    disclosure the rows are.
    """
    return ReportBlockResultRead(
        id=row.id,
        block_id=row.block_id,
        section_id=row.section_id,
        position=row.position,
        heading_snapshot=row.heading_snapshot,
        title_snapshot=row.title_snapshot,
        question_snapshot=row.question_snapshot,
        computed_at=row.computed_at,
        # The row's **own** verdict survives: a block that failed to run stays
        # failed. `restricted` is the flag the renderer branches on first, so
        # the two facts stay separable — "this figure is broken" and "this
        # figure is not yours to see" are different things to tell a reader.
        status=row.status,
        restricted=True,
        error_code=restricted.NO_DATA_ACCESS,
        error_message=message,
    )


def _block_result_read(
    row: ReportBlockResult, previous: dict[UUID, str]
) -> ReportBlockResultRead:
    """A result, plus whether its statement moved since the last generation.

    Left null when there is nothing to compare against — a first run, or a
    block that did not exist last time. Saying "unchanged" there would be an
    answer, and it would be one nobody checked.
    """
    read = ReportBlockResultRead.model_validate(row)
    if row.block_id is not None:
        was = previous.get(row.block_id)
        if was is not None:
            read.sql_changed = was != row.sql_hash
    return read
