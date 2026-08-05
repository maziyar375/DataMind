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

from fastapi import APIRouter, status

from app.api.deps import CtxDep, DbDep, SettingsDep
from app.api.schemas import (
    ReportBlockCreate,
    ReportBlockRead,
    ReportBlockUpdate,
    ReportCreate,
    ReportRead,
    ReportSectionCreate,
    ReportSectionRead,
    ReportSectionUpdate,
    ReportSummaryRead,
    ReportUpdate,
)
from app.infra.db.models import Report
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports"])


async def _report_read(service: ReportService, report: Report) -> ReportRead:
    """The report, its outline, and the chips' labels — in three queries.

    Every field *except* `sections` is copied off the row. `Report.sections` is
    a lazy relationship, and `model_validate` would read it — a lazy load inside
    a response, in a context that cannot await, which is `MissingGreenlet`: a
    500 that appears only against a real database.
    """
    sections = await service.sections_of(report.id)
    blocks = await service.blocks_of([s.id for s in sections])
    connections, models = await service.display_names([report])

    by_section: dict[UUID, list[ReportBlockRead]] = {}
    for block in blocks:
        by_section.setdefault(block.section_id, []).append(
            ReportBlockRead.model_validate(block)
        )

    fields = {
        name: getattr(report, name)
        for name in ReportRead.model_fields
        if name not in ("sections", "connection_name", "llm_config_name")
    }
    return ReportRead(
        **fields,
        connection_name=connections.get(report.connection_id),  # type: ignore[arg-type]
        llm_config_name=models.get(report.llm_config_id),  # type: ignore[arg-type]
        sections=[
            _section_read(section, by_section.get(section.id, []))
            for section in sections
        ],
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
    ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> list[ReportSummaryRead]:
    service = ReportService(db, settings)
    reports = await service.list(ctx.user_id)
    connections, models = await service.display_names(reports)

    cards: list[ReportSummaryRead] = []
    for report in reports:
        card = ReportSummaryRead.model_validate(report)
        card.section_count = len(await service.sections_of(report.id))
        if report.connection_id is not None:
            card.connection_name = connections.get(report.connection_id)
        if report.llm_config_id is not None:
            card.llm_config_name = models.get(report.llm_config_id)
        cards.append(card)
    return cards


@router.post("", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> ReportRead:
    """Create a report. **The disclosure gate is here** — a connection that
    shares no result values cannot carry a document written from them."""
    service = ReportService(db, settings)
    report = await service.create(ctx.user_id, **payload.model_dump())
    return await _report_read(service, report)


@router.get("/{report_id}", response_model=ReportRead)
async def get_report(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> ReportRead:
    service = ReportService(db, settings)
    report = await service.get(report_id, ctx.user_id)
    return await _report_read(service, report)


@router.patch("/{report_id}", response_model=ReportRead)
async def update_report(
    report_id: UUID,
    payload: ReportUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> ReportRead:
    """Name, description, prompt, model, status. A **different** `connection_id`
    is 422 — the report is pinned to the one it was created against."""
    service = ReportService(db, settings)
    report = await service.update(
        report_id, ctx.user_id, **payload.model_dump(exclude_unset=True)
    )
    return await _report_read(service, report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: UUID, ctx: CtxDep, db: DbDep, settings: SettingsDep
) -> None:
    await ReportService(db, settings).delete(report_id, ctx.user_id)


# ── blocks ───────────────────────────────────────────────────────────────
# Declared above `/{report_id}/sections/...` so `/blocks/{block_id}` is never a
# candidate for a section path, and above nothing else that could shadow it.
@router.patch("/{report_id}/blocks/{block_id}", response_model=ReportBlockRead)
async def update_block(
    report_id: UUID,
    block_id: UUID,
    payload: ReportBlockUpdate,
    ctx: CtxDep,
    db: DbDep,
    settings: SettingsDep,
) -> ReportBlockRead:
    """Editing the question (or the window) resets the block to `UNCHECKED` and
    drops its SQL: the stored statement answered the previous question."""
    block = await ReportService(db, settings).update_block(
        report_id, block_id, ctx.user_id, **payload.model_dump(exclude_unset=True)
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
) -> None:
    await ReportService(db, settings).delete_block(report_id, block_id, ctx.user_id)


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
) -> ReportSectionRead:
    section = await ReportService(db, settings).add_section(
        report_id, ctx.user_id, **payload.model_dump()
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
) -> ReportSectionRead:
    service = ReportService(db, settings)
    section = await service.update_section(
        report_id, section_id, ctx.user_id, **payload.model_dump(exclude_unset=True)
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
) -> None:
    await ReportService(db, settings).delete_section(report_id, section_id, ctx.user_id)


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
) -> ReportBlockRead:
    block = await ReportService(db, settings).add_block(
        report_id, section_id, ctx.user_id, **payload.model_dump()
    )
    return ReportBlockRead.model_validate(block)
