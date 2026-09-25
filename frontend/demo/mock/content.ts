/**
 * The demo's dashboards, reports and knowledge store, as the API serves them.
 *
 * Everything here is read from `fixtures/*.generated.ts` — recorded by
 * `scripts/build.py` through the real guard, connectors, chart planner, report
 * checks and knowledge validator — and only shaped into the API's types:
 * ids, owners, privileges and timestamps on the demo's clock. Nothing is
 * computed that the backend would have computed; `client.ts` answers the
 * endpoints from these functions and refuses every write.
 */
import type {
  Dashboard, DashboardDocument, DashboardSummary, DashboardTile, KnowledgeHealth, KnowledgeTemplate, Report,
  ReportBlock, ReportBlockResult, ReportRun, ReportRunDetail, ReportSection, ReportSectionResult, ReportSummary,
  Suggestion, TileResult,
} from '../../src/api/types'
import { daysAgo, demoIso } from './clock'
import { BOARDS } from './fixtures/dashboards.generated'
import { SUGGESTIONS, TEMPLATES } from './fixtures/knowledge.generated'
import { REPORTS } from './fixtures/reports.generated'
import { SNAPSHOT_VERSION } from './fixtures/schema.generated'
import { CONNECTIONS, DEMO_PERSON, IDS, LLM_CONFIGS } from './fixtures/world'
import type { RecordedResult, ScriptedBoard, ScriptedReport, ScriptedReportBlock } from './script-types'

/** UUID-shaped and stable across reloads, so a deep link to a board keeps working. */
const uid = (group: number, n: number) =>
  `${group.toString(16).padStart(8, '0')}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`

const connectionOf = (key: 'sales' | 'sakila') => CONNECTIONS.find((c) => c.id === IDS.connections[key])!
const isMine = (owner: string) => owner === DEMO_PERSON.name

// ── dashboards ───────────────────────────────────────────────────────────────
const boardId = (i: number) => uid(0x10, i + 1)
const tileId = (i: number, position: number) => uid(0x11, (i + 1) * 100 + position + 1)

/**
 * When each board's tiles were last computed — on the **visitor's** clock, the
 * one place the demo does not use its own. The dashboard's scheduler
 * (`dashboard-schedule.ts`) asks `Date.now() - computed_at`, so a result
 * stamped on the demo's day would be hours overdue for anyone visiting on
 * another and be asked for again on every tick. As on the backend, a refresh
 * that is due (or forced) recomputes, and one that is not is served from cache.
 */
const computedAt = new Map<string, number>()
const OPENED = Date.now() - 4 * 60_000

function lastComputed(id: string, interval: number, force: boolean): number {
  const now = Date.now()
  const last = computedAt.get(id) ?? OPENED
  const next = force || now - last >= interval * 1000 ? now : last
  computedAt.set(id, next)
  return next
}

function boardAt(id: string): [ScriptedBoard, number] | null {
  const i = BOARDS.findIndex((_, n) => boardId(n) === id)
  return i === -1 ? null : [BOARDS[i], i]
}

export function dashboardSummaries(): DashboardSummary[] {
  return BOARDS.map((board, i) => ({
    id: boardId(i),
    name: board.name,
    description: board.description,
    status: 'ACTIVE',
    default_refresh_interval_seconds: board.refresh_seconds,
    tile_count: board.tiles.length,
    last_refreshed_at: new Date(computedAt.get(boardId(i)) ?? OPENED).toISOString(),
    created_at: daysAgo(board.days_old, 10.5),
    updated_at: daysAgo(Math.min(board.days_old, 3), 16),
    shared: !isMine(board.owner),
    owner_name: isMine(board.owner) ? null : board.owner,
    privileges: board.privileges,
  }))
}

export function dashboardOf(id: string): Dashboard | null {
  const found = boardAt(id)
  if (!found) return null
  const [board, i] = found
  const connection = connectionOf(board.connection)
  const created = daysAgo(board.days_old, 10.5)
  const tiles: DashboardTile[] = board.tiles.map((t) => ({
    id: tileId(i, t.position),
    dashboard_id: id,
    connection_id: t.tile_type === 'TEXT' ? null : connection.id,
    connection_name: t.tile_type === 'TEXT' ? null : connection.name,
    llm_config_id: t.tile_type === 'TEXT' ? null : LLM_CONFIGS[0].id,
    llm_config_name: t.tile_type === 'TEXT' ? null : LLM_CONFIGS[0].name,
    title: t.title,
    tile_type: t.tile_type,
    question: t.question,
    sql: t.sql,
    sql_origin: t.sql_origin,
    chart_config: t.chart_config,
    table_config: t.table_config,
    max_rows: null,
    refresh_interval_seconds: null,
    effective_refresh_interval_seconds: board.refresh_seconds,
    grid_x: t.grid_x,
    grid_y: t.grid_y,
    grid_w: t.grid_w,
    grid_h: t.grid_h,
    position: t.position,
    created_at: created,
    updated_at: created,
  }))
  return {
    id,
    name: board.name,
    description: board.description,
    status: 'ACTIVE',
    grid_columns: 12,
    row_height_px: 60,
    gap_px: 12,
    compact_mode: 'VERTICAL',
    palette: 'default',
    theme_override: 'INHERIT',
    default_refresh_interval_seconds: board.refresh_seconds,
    created_at: created,
    updated_at: daysAgo(Math.min(board.days_old, 3), 16),
    tiles,
    privileges: board.privileges,
    owner_name: isMine(board.owner) ? null : board.owner,
  }
}

function stamped(result: RecordedResult, at: string): TileResult {
  return { ...result, computed_at: at }
}

/**
 * What a refresh returns. The statements are the recorded ones over data that
 * does not move, so a forced refresh returns the same rows — computed now.
 */
export function tileResults(id: string, tileIds: string[], force: boolean): Record<string, TileResult> | null {
  const found = boardAt(id)
  if (!found) return null
  const [board, i] = found
  const at = new Date(lastComputed(id, board.refresh_seconds, force)).toISOString()
  const out: Record<string, TileResult> = {}
  for (const t of board.tiles) {
    const key = tileId(i, t.position)
    if (!t.result || (tileIds.length > 0 && !tileIds.includes(key))) continue
    out[key] = stamped(t.result, at)
  }
  return out
}

/** A board as a file: the statements and the layout, no ids and no results. */
export function dashboardDocument(id: string): DashboardDocument | null {
  const dashboard = dashboardOf(id)
  const found = boardAt(id)
  if (!dashboard || !found) return null
  const connection = connectionOf(found[0].connection)
  return {
    format: 'datamind.dashboard',
    version: 1,
    exported_at: demoIso(),
    dashboard: {
      name: dashboard.name,
      description: dashboard.description,
      grid_columns: dashboard.grid_columns,
      row_height_px: dashboard.row_height_px,
      gap_px: dashboard.gap_px,
      compact_mode: dashboard.compact_mode,
      palette: dashboard.palette,
      theme_override: dashboard.theme_override,
      default_refresh_interval_seconds: dashboard.default_refresh_interval_seconds,
    },
    connections: [{ ref: 'db1', name: connection.name, database_type: connection.database_type }],
    tiles: dashboard.tiles.map((t) => ({
      connection_ref: t.connection_id ? 'db1' : null,
      title: t.title,
      tile_type: t.tile_type,
      question: t.question,
      sql: t.sql,
      sql_origin: t.sql_origin,
      chart_config: t.chart_config,
      table_config: t.table_config,
      max_rows: t.max_rows,
      refresh_interval_seconds: t.refresh_interval_seconds,
      grid_x: t.grid_x,
      grid_y: t.grid_y,
      grid_w: t.grid_w,
      grid_h: t.grid_h,
      position: t.position,
    })),
  }
}

/** Whose a board is and what the demo person holds on it, for the access endpoints. */
export function boardAccess(id: string): { owner: string; privileges: string[] } | null {
  const found = boardAt(id)
  return found ? { owner: found[0].owner, privileges: found[0].privileges } : null
}

export const boardIdFor = (scriptId: string) => boardId(BOARDS.findIndex((b) => b.id === scriptId))

// ── reports ──────────────────────────────────────────────────────────────────
const reportId = (i: number) => uid(0x12, i + 1)
const sectionId = (i: number, s: number) => uid(0x13, (i + 1) * 100 + s)
const blockId = (i: number, s: number, b: number) => uid(0x14, (i + 1) * 10000 + s * 100 + b + 1)
const runId = (i: number) => uid(0x15, i + 1)
const blockResultId = (i: number, s: number, b: number) => uid(0x16, (i + 1) * 10000 + s * 100 + b + 1)
const sectionResultId = (i: number, s: number) => uid(0x17, (i + 1) * 100 + s)

function reportAt(id: string): [ScriptedReport, number] | null {
  const i = REPORTS.findIndex((_, n) => reportId(n) === id)
  return i === -1 ? null : [REPORTS[i], i]
}

/** The executive summary is section 0, then the report's own sections from 1. */
function sectionCount(report: ScriptedReport): number {
  return report.sections.length + 1
}

export function reportAccess(id: string): { owner: string; privileges: string[] } | null {
  const found = reportAt(id)
  return found ? { owner: found[0].owner, privileges: found[0].privileges } : null
}

export const reportIdFor = (scriptId: string) => reportId(REPORTS.findIndex((r) => r.id === scriptId))

export function reportSummaries(): ReportSummary[] {
  return REPORTS.map((report, i) => {
    const connection = connectionOf(report.connection)
    return {
      id: reportId(i),
      name: report.name,
      description: report.description,
      connection_id: connection.id,
      connection_name: connection.name,
      llm_config_id: LLM_CONFIGS[0].id,
      llm_config_name: LLM_CONFIGS[0].name,
      language: report.language,
      section_target: report.sections.length,
      status: 'ACTIVE',
      section_count: sectionCount(report),
      created_at: daysAgo(report.days_old, 11),
      updated_at: daysAgo(report.run_days_ago, 9),
      shared: !isMine(report.owner),
      owner_name: isMine(report.owner) ? null : report.owner,
      privileges: report.privileges,
    }
  })
}

export function reportOf(id: string): Report | null {
  const found = reportAt(id)
  if (!found) return null
  const [report, i] = found
  const connection = connectionOf(report.connection)
  const created = daysAgo(report.days_old, 11)
  const checked = daysAgo(report.run_days_ago, 9)
  const summary: ReportSection = {
    id: sectionId(i, 0), report_id: id, position: 0, heading: report.summary.heading,
    intent: report.summary.intent, kind: 'EXECUTIVE_SUMMARY', created_at: created, updated_at: created, blocks: [],
  }
  const sections: ReportSection[] = report.sections.map((s, n) => ({
    id: sectionId(i, n + 1),
    report_id: id,
    position: n + 1,
    heading: s.heading,
    intent: s.intent,
    kind: s.kind,
    created_at: created,
    updated_at: created,
    blocks: s.blocks.map((b, k): ReportBlock => ({
      id: blockId(i, n + 1, k),
      section_id: sectionId(i, n + 1),
      position: k,
      question: b.question,
      title: b.title,
      sql: b.sql,
      sql_hash: b.sql_hash,
      sql_origin: 'GENERATED',
      block_type: b.block_type,
      chart_config: b.chart_config,
      time_window: b.time_window,
      feasibility_status: 'FEASIBLE',
      feasibility_reason: null,
      feasibility_checked_at: checked,
      max_rows: null,
      created_at: created,
      updated_at: created,
    })),
  }))
  return {
    id,
    name: report.name,
    description: report.description,
    prompt: report.prompt,
    connection_id: connection.id,
    connection_name: connection.name,
    llm_config_id: LLM_CONFIGS[0].id,
    llm_config_name: LLM_CONFIGS[0].name,
    language: report.language,
    section_target: report.sections.length,
    status: 'ACTIVE',
    created_at: created,
    updated_at: checked,
    sections: [summary, ...sections],
    data_access: true,
    privileges: report.privileges,
    owner_name: isMine(report.owner) ? null : report.owner,
  }
}

function runOfReport(report: ScriptedReport, i: number): ReportRun {
  const started = Date.parse(daysAgo(report.run_days_ago, 9))
  const total = report.sections.reduce((n, s) => n + s.blocks.length, 0) + sectionCount(report)
  const model = LLM_CONFIGS[0]
  return {
    id: runId(i),
    report_id: reportId(i),
    status: 'SUCCEEDED',
    phase: 'Done',
    progress_current: total,
    progress_total: total,
    llm_config_id: model.id,
    model_snapshot: { provider: model.provider, model: model.model, name: model.name },
    prompt_version: 'r5',
    language: report.language,
    error_message: null,
    started_at: demoIso(started),
    finished_at: demoIso(started + 71_000),
    created_at: demoIso(started - 900),
  }
}

export function reportRuns(id: string): ReportRun[] | null {
  const found = reportAt(id)
  return found ? [runOfReport(...found)] : null
}

export function reportRunDetail(id: string, run: string): ReportRunDetail | null {
  const found = reportAt(id)
  if (!found || runId(found[1]) !== run) return null
  const [report, i] = found
  const head = runOfReport(report, i)
  const computed = demoIso(Date.parse(head.started_at!) + 18_000)
  const blocks: ReportBlockResult[] = []
  const sections: ReportSectionResult[] = [{
    id: sectionResultId(i, 0),
    section_id: sectionId(i, 0),
    position: 0,
    heading_snapshot: report.summary.heading,
    prose: report.summary.prose,
    edited_prose: null,
    numeric_check: { ...report.summary.numeric_check, claims: undefined },
    claims: null,
    status: 'OK',
    error_message: null,
    created_at: head.finished_at!,
  }]
  report.sections.forEach((s, n) => {
    const ids = s.blocks.map((_, k) => blockResultId(i, n + 1, k))
    s.blocks.forEach((b, k) => {
      blocks.push({
        id: ids[k],
        block_id: blockId(i, n + 1, k),
        section_id: sectionId(i, n + 1),
        position: k,
        heading_snapshot: s.heading,
        title_snapshot: b.title,
        question_snapshot: b.question,
        sql_text: b.sql,
        sql_hash: b.sql_hash,
        columns: b.result.columns,
        rows: b.result.rows,
        row_count: b.result.row_count,
        truncated: b.result.truncated,
        vega_spec: b.result.vega_spec,
        chart_source: b.result.chart_source,
        chart_note: b.result.chart_note,
        kpi: b.result.kpi,
        computed_at: computed,
        duration_ms: b.result.duration_ms,
        status: 'OK',
        error_code: null,
        error_message: null,
        restricted: false,
        sql_changed: null,
      })
    })
    // The stored edge is claim → result id, resolved when the run was written.
    const cite = <T extends { cites: number | null }>(claim: T) => ({
      ...claim, block_result_id: claim.cites ? ids[claim.cites - 1] ?? null : null,
    })
    sections.push({
      id: sectionResultId(i, n + 1),
      section_id: sectionId(i, n + 1),
      position: n + 1,
      heading_snapshot: s.heading,
      prose: s.prose,
      edited_prose: null,
      numeric_check: { ...s.numeric_check, claims: s.numeric_check.claims?.map(cite) },
      claims: s.claims.map(cite),
      status: 'OK',
      error_message: null,
      created_at: head.finished_at!,
    })
  })
  return { ...head, blocks, sections }
}

/** A recorded block, for *Change chart* on a report figure. */
export function reportBlockResult(id: string, run: string, resultId: string): ScriptedReportBlock | null {
  const found = reportAt(id)
  if (!found || runId(found[1]) !== run) return null
  const [report, i] = found
  for (const [n, s] of report.sections.entries()) {
    for (const [k, b] of s.blocks.entries()) if (blockResultId(i, n + 1, k) === resultId) return b
  }
  return null
}

// ── the knowledge store ──────────────────────────────────────────────────────
/** Past this many days without a match a template is *unused* — surfaced, never enforced. */
const UNUSED_AFTER_DAYS = 60

function keyOf(connectionId: string): 'sales' | 'sakila' | null {
  if (connectionId === IDS.connections.sales) return 'sales'
  if (connectionId === IDS.connections.sakila) return 'sakila'
  return null
}

export function templatesFor(connectionId: string): KnowledgeTemplate[] {
  const key = keyOf(connectionId)
  return TEMPLATES.filter((t) => t.connection === key).map((t) => ({
    id: t.id,
    connection_id: connectionId,
    question: t.question,
    question_normalized: t.question_normalized,
    sql: t.sql,
    params: t.params,
    note: t.note,
    source: t.source,
    literal_provenance: t.literal_provenance,
    role: t.role,
    status: t.status,
    status_reason: t.status_reason,
    schema_version: SNAPSHOT_VERSION,
    referenced_tables: t.referenced_tables,
    conflicts_with: [],
    conflict_evidence: {},
    hit_count: t.hit_count,
    last_hit_at: t.last_hit_days === null ? null : daysAgo(t.last_hit_days, 14),
    verified_at: t.verified_days === null ? null : daysAgo(t.verified_days, 11),
    last_validated_at: daysAgo(0, 9.25),
    created_at: daysAgo(t.created_days, 11),
    updated_at: daysAgo(t.verified_days ?? t.created_days, 11),
  }))
}

export function knowledgeHealth(connectionId: string): KnowledgeHealth {
  const templates = TEMPLATES.filter((t) => t.connection === keyOf(connectionId))
  return {
    total: templates.length,
    stale: [],
    conflicted: [],
    unused: templates.filter((t) => t.hit_count === 0 && t.created_days > UNUSED_AFTER_DAYS).map((t) => t.id),
    conflict_checks_enabled: CONNECTIONS.find((c) => c.id === connectionId)?.conflict_checks_enabled ?? false,
    unused_after_days: UNUSED_AFTER_DAYS,
  }
}

export function suggestionsFor(connectionId: string): Suggestion[] {
  const key = keyOf(connectionId)
  if (!key) return []
  // A backfill links to the tile it came from, by that tile's id here.
  return SUGGESTIONS[key].map((s) => {
    const [board, position] = s.origin_id.split('#')
    const i = BOARDS.findIndex((b) => b.id === board)
    return s.kind === 'BACKFILL' && i !== -1 ? { ...s, origin_id: tileId(i, Number(position)) } : s
  })
}
