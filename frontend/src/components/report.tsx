/**
 * The outline editor — the structure a report is generated from.
 *
 * This is the screen the whole feature turns on. A report is not a question
 * answered once; it is a **document whose structure a human approved**, and
 * this is where that approval happens (docs/reports-plan.md §2). The model
 * proposes headings and questions, the user rewrites them, and only then is a
 * single model call spent per section on prose.
 *
 * Three things shape the layout:
 *
 *  - **The question is the unit, not the SQL.** A block is one plain-language
 *    question that becomes one query and one chart, and in v1 that question is
 *    what the user edits. Editing it drops the stored statement — the SQL
 *    answered the *previous* question — so the chip returns to "Not checked"
 *    on its own rather than the page pretending otherwise. (The SQL editor is
 *    Phase 11.)
 *  - **Feasibility is mechanical, and it is the guard's answer, not ours.**
 *    `POST .../check` gives back `FEASIBLE | EMPTY | INFEASIBLE` and, when it
 *    refuses, the guard's own sentence. That sentence is rendered verbatim: a
 *    re-worded rejection is one the user cannot act on, which is the rule
 *    `semantic.tsx` already follows for metric expressions.
 *  - **"Check all" is a loop, not a job.** One check is five to ten seconds of
 *    guard work on one heading. Looping it in the page with per-block progress
 *    reads better than a job with a progress bar, and it needs no extra table
 *    — the reason `api/v1/reports.py` makes the route synchronous and per
 *    block. It can be stopped, because a user who sees the first two answers
 *    is often done reading.
 *
 * Every free-text field is `dir="auto"`: a report is written in Persian as
 * often as English, and the two mix inside one heading.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  isReportRunInFlight, llmConfigs as modelsApi, reports as api,
} from '../api/client'
import type {
  ChartOption, LlmConfig, NumericFinding, Report, ReportBlock, ReportBlockCheck,
  ReportBlockResult,
  ReportBlockType, ReportFeasibility, ReportLanguage, ReportRun, ReportRunDetail,
  ReportSection, ReportSectionResult, ReportSummary, ReportTimeWindow,
} from '../api/types'
import { ChartGlyph, ChartTypePicker } from './chart-picker'
import {
  assembleDocument, chartTypeOf, figureNumbers, isEdited, keyFigures, proseOf,
  renderKindOf, summaryParts,
  type DocumentSection,
} from './report-document'
// One vocabulary for a run's status across the two screens that show one.
import { RUN_TONE } from './report-history'
import { printReport } from './report-print'
import { VegaChart } from './VegaChart'
import {
  Chip, CopyButton, DangerButton, EmptyState, ErrorNote, GhostButton, Icon, InlineEdit,
  Kpi, Modal, PrimaryButton, ProgressBar, ResultTable, Spinner, TextArea,
  relativeTime,
} from './ui'
import type { ChipTone } from './ui'

/** Wide enough for a heading and its questions, narrow enough to read as prose. */
const CONTENT_WIDTH = 880

/**
 * The windows a block may carry.
 *
 * A *label*, and only a label — §6. It drives the prompt when the block is
 * checked, and the window itself ends up inside the SQL as relative date
 * arithmetic the database resolves on every run. Nothing here is ever
 * substituted into a statement, which is exactly why a report re-run in Mehr
 * describes Mehr.
 */
const TIME_WINDOWS: { value: ReportTimeWindow; label: string }[] = [
  { value: 'none', label: 'No time limit' },
  { value: 'last_7_days', label: 'Last 7 days' },
  { value: 'last_30_days', label: 'Last 30 days' },
  { value: 'last_month', label: 'Last month' },
  { value: 'last_3_months', label: 'Last 3 months' },
  { value: 'last_12_months', label: 'Last 12 months' },
  { value: 'previous_quarter', label: 'Previous quarter' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'custom', label: 'Stated in the question' },
]

const BLOCK_TYPES: { value: ReportBlockType; label: string }[] = [
  { value: 'CHART', label: 'Chart' },
  { value: 'TABLE', label: 'Table' },
  { value: 'METRIC', label: 'One figure' },
]

/**
 * What each verdict is called on screen.
 *
 * `EMPTY` is amber rather than red on purpose: the query works and no rows fall
 * in the window, which is a fact about the data and often the finding itself.
 * A report that says "no returns were recorded in this period" is correct.
 */
const FEASIBILITY: Record<
  ReportFeasibility,
  { label: string; tone: ChipTone; rail: string }
> = {
  UNCHECKED: { label: 'Not checked', tone: 'neutral', rail: 'var(--border-strong)' },
  FEASIBLE: { label: 'Ready', tone: 'green', rail: 'var(--green)' },
  EMPTY: { label: 'No rows yet', tone: 'amber', rail: 'var(--amber)' },
  INFEASIBLE: { label: 'Cannot be produced', tone: 'red', rail: 'var(--red)' },
}

/**
 * The document's own furniture, in the language the document is written in.
 *
 * A report whose paragraphs are Persian and whose figure captions say "Figure 3"
 * is not a Persian report — it is an English product with Persian text pasted
 * into it, and that is exactly the seam a reader notices first. The prose is
 * pinned per report (`reports.language`); so is everything the page writes
 * around it.
 *
 * Deliberately small. Only what appears *inside the document* is here — the app
 * chrome above it (Back, Stop, the status chip) stays in the interface language,
 * because it is the application talking, not the report.
 */
const LABELS: Record<ReportLanguage, Record<string, string>> = {
  en: {
    eyebrow: 'Analytical report',
    dataSource: 'Data source',
    generated: 'Generated',
    model: 'Model',
    keyFigures: 'Key figures',
    figure: 'Figure',
    method: 'Method and data notes',
    methodBody:
      'Every figure in this document was produced by one query, run read-only '
      + 'against the data source above and validated before execution. The '
      + 'queries are listed here with the row counts they returned.',
    question: 'Question',
    rows: 'rows',
    row: 'row',
    capped: 'result capped',
    computed: 'computed',
    query: 'Query',
    hideQuery: 'Hide query',
    print: 'Print',
    edited: 'edited by you',
    retry: 'Retry',
    changeChart: 'Change chart',
    done: 'Done',
    edit: 'Edit',
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    revert: 'Revert',
    queryChanged: 'query changed since the previous generation',
    queryChangedNote:
      'The statement behind this figure is not the one the previous '
      + 'generation ran, so the two numbers are not a like-for-like '
      + 'comparison.',
    asOf: 'This document reflects the data as it stood on',
    asOfTail:
      'Generating again writes a new run and leaves this one exactly as it is.',
  },
  fa: {
    eyebrow: 'گزارش تحلیلی',
    dataSource: 'منبع داده',
    generated: 'تاریخ تولید',
    model: 'مدل',
    keyFigures: 'شاخص‌های کلیدی',
    figure: 'شکل',
    method: 'روش و ملاحظات داده',
    methodBody:
      'هر شکل این سند نتیجهٔ یک کوئری است که فقط‌خواندنی روی منبع دادهٔ بالا '
      + 'اجرا و پیش از اجرا اعتبارسنجی شده است. کوئری‌ها همراه با تعداد سطرهای '
      + 'بازگشتی در این بخش فهرست شده‌اند.',
    question: 'پرسش',
    rows: 'سطر',
    row: 'سطر',
    capped: 'نتیجه محدود شده',
    computed: 'محاسبه در',
    query: 'کوئری',
    hideQuery: 'بستن کوئری',
    print: 'چاپ',
    edited: 'ویرایش‌شده توسط شما',
    retry: 'اجرای دوباره',
    changeChart: 'تغییر نمودار',
    done: 'پایان',
    edit: 'ویرایش',
    save: 'ذخیره',
    saving: 'در حال ذخیره…',
    cancel: 'انصراف',
    revert: 'بازگردانی',
    queryChanged: 'کوئری نسبت به تولید قبلی تغییر کرده است',
    queryChangedNote:
      'عبارتی که این شکل از آن به دست آمده، همان عبارت تولید قبلی نیست؛ '
      + 'بنابراین این دو عدد قابل مقایسهٔ مستقیم نیستند.',
    asOf: 'این سند وضعیت داده‌ها را در تاریخ زیر نشان می‌دهد:',
    asOfTail: 'تولید دوبارهٔ گزارش، اجرای تازه‌ای می‌سازد و این اجرا دست‌نخورده می‌ماند.',
  },
}

/** The document's own labels, falling back to English for an unknown code. */
function labelsFor(language: string): Record<string, string> {
  return LABELS[language as ReportLanguage] ?? LABELS.en
}

// ── the editor ────────────────────────────────────────────────────────────
export function ReportOutlineEditor({
  reportId, onBack, onChanged, onOpenRun, onHistory,
}: {
  reportId: string
  onBack: () => void
  /** Every generation this report has produced. */
  onHistory: () => void
  /** Hand the written row back so the index card never shows a stale name. */
  onChanged?: (report: Report) => void
  /**
   * Open a run in the viewer — the one this editor just started, or the last
   * one it produced.
   *
   * The editor owns the `startRun` call rather than the caller, so a refusal
   * (a second concurrent generation, a policy tightened since, a block with no
   * query) lands in the same place every other error on this page does.
   */
  onOpenRun: (runId: string) => void
}) {
  const [report, setReport] = useState<Report | null>(null)
  const [models, setModels] = useState<LlmConfig[]>([])
  const [error, setError] = useState<string | null>(null)
  const [proposing, setProposing] = useState(false)
  const [confirmPropose, setConfirmPropose] = useState(false)
  const [checking, setChecking] = useState<string[]>([])
  const [sweep, setSweep] = useState<{ done: number; total: number; label: string } | null>(null)
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const [latestRun, setLatestRun] = useState<ReportRun | null>(null)
  // How many documents this report has produced. The header shows it because
  // "History 7" is an invitation and "History" is a menu item.
  const [runCount, setRunCount] = useState(0)
  const [starting, setStarting] = useState(false)
  const stopSweep = useRef(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        // The run list is rows only — no result of any run is read here. It is
        // what lets a report that has already been generated offer its document
        // instead of stranding the reader in the editor.
        const [loaded, configs, history] = await Promise.all([
          api.get(reportId), modelsApi.list(), api.runs(reportId),
        ])
        if (cancelled) return
        setReport(loaded)
        setModels(configs)
        setLatestRun(history[0] ?? null)
        setRunCount(history.length)
        // A generation still in flight is what the reader came for.
        if (history[0] && isReportRunInFlight(history[0].status)) onOpenRun(history[0].id)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not open this report.')
      }
    })()
    return () => {
      cancelled = true
    }
    // `onOpenRun` is deliberately not a dependency: the caller passes an inline
    // arrow, and re-running this on every render would re-fetch the document.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportId])

  /** One place errors surface, and they surface as the API worded them. */
  const guard = useCallback(async <T,>(run: () => Promise<T>): Promise<T | null> => {
    setError(null)
    try {
      return await run()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
      return null
    }
  }, [])

  /**
   * Tell the index about the document, once per change, from an effect.
   *
   * Not from inside the `setReport` updater, which is where this started: an
   * updater runs in React's render phase, so calling the parent's setter from
   * one is "cannot update a component while rendering another" — and it fires
   * twice under StrictMode. The callback is held in a ref so the effect can
   * depend on `report` alone; depending on the callback itself would re-fire on
   * every render, because the caller passes an inline arrow, and each fire
   * would re-render the caller.
   */
  const notify = useRef(onChanged)
  useEffect(() => {
    notify.current = onChanged
  })
  useEffect(() => {
    if (report) notify.current?.(report)
  }, [report])

  const patchSections = useCallback(
    (change: (sections: ReportSection[]) => ReportSection[]) =>
      setReport((current) =>
        current ? { ...current, sections: change(current.sections) } : current,
      ),
    [],
  )

  const putBlock = useCallback(
    (block: ReportBlock) =>
      patchSections((sections) =>
        sections.map((section) =>
          section.id === block.section_id
            ? {
                ...section,
                blocks: section.blocks.map((b) => (b.id === block.id ? block : b)),
              }
            : section,
        ),
      ),
    [patchSections],
  )

  // ── the report itself ───────────────────────────────────────────────────
  async function patchReport(payload: Record<string, unknown>) {
    const next = await guard(() => api.update(reportId, payload))
    // Spliced in; never re-read the document to see an edit.
    if (next) setReport(next)
  }

  /** 202, then straight to the viewer — the run is minutes, not a request. */
  async function generate() {
    setStarting(true)
    const run = await guard(() => api.startRun(reportId))
    setStarting(false)
    if (run) onOpenRun(run.id)
  }

  async function propose() {
    setConfirmPropose(false)
    setProposing(true)
    const next = await guard(() => api.proposeOutline(reportId))
    if (next) {
      setReport(next)
      setPreviews({})
    }
    setProposing(false)
  }

  // ── sections ────────────────────────────────────────────────────────────
  async function addSection() {
    const section = await guard(() =>
      api.addSection(reportId, { heading: 'New section', intent: '' }),
    )
    if (section) patchSections((sections) => [...sections, section])
  }

  async function patchSection(sectionId: string, payload: Record<string, unknown>) {
    const next = await guard(() => api.updateSection(reportId, sectionId, payload))
    if (next) {
      patchSections((sections) => sections.map((s) => (s.id === sectionId ? next : s)))
    }
  }

  async function removeSection(sectionId: string) {
    const done = await guard(() => api.removeSection(reportId, sectionId))
    if (done !== null) patchSections((sections) => sections.filter((s) => s.id !== sectionId))
  }

  /**
   * Move a section, then renumber whatever the move displaced.
   *
   * `PATCH .../sections/{id}` sets one position and shifts nothing else, which
   * is the honest shape for a route that knows about one row. So the order is
   * applied here first — the list moves under the cursor — and the rows whose
   * index actually changed are written after. An adjacent swap is two calls.
   */
  async function moveSection(from: number, to: number) {
    if (!report || to < 0 || to >= report.sections.length) return
    const before = report.sections
    const after = reorder(before, from, to)
    patchSections(() => after.map((section, index) => ({ ...section, position: index })))
    await guard(async () => {
      for (const [index, section] of after.entries()) {
        if (before[index].id !== section.id) {
          await api.updateSection(reportId, section.id, { position: index })
        }
      }
    })
  }

  // ── blocks ──────────────────────────────────────────────────────────────
  async function addBlock(sectionId: string) {
    const block = await guard(() =>
      api.addBlock(reportId, sectionId, { question: 'New question', block_type: 'CHART' }),
    )
    if (block) {
      patchSections((sections) =>
        sections.map((s) => (s.id === sectionId ? { ...s, blocks: [...s.blocks, block] } : s)),
      )
    }
  }

  async function patchBlock(blockId: string, payload: Record<string, unknown>) {
    const next = await guard(() => api.updateBlock(reportId, blockId, payload))
    if (next) putBlock(next)
  }

  async function removeBlock(sectionId: string, blockId: string) {
    const done = await guard(() => api.removeBlock(reportId, blockId))
    if (done !== null) {
      patchSections((sections) =>
        sections.map((s) =>
          s.id === sectionId ? { ...s, blocks: s.blocks.filter((b) => b.id !== blockId) } : s,
        ),
      )
    }
  }

  async function moveBlock(sectionId: string, from: number, to: number) {
    const section = report?.sections.find((s) => s.id === sectionId)
    if (!section || to < 0 || to >= section.blocks.length) return
    const before = section.blocks
    const after = reorder(before, from, to)
    patchSections((sections) =>
      sections.map((s) =>
        s.id === sectionId
          ? { ...s, blocks: after.map((block, index) => ({ ...block, position: index })) }
          : s,
      ),
    )
    await guard(async () => {
      for (const [index, block] of after.entries()) {
        if (before[index].id !== block.id) {
          await api.updateBlock(reportId, block.id, { position: index })
        }
      }
    })
  }

  // ── feasibility ─────────────────────────────────────────────────────────
  /**
   * Ask the guard about one block.
   *
   * Every outcome is an answer, never a thrown error — `INFEASIBLE` included —
   * so the only thing that reaches `guard` here is a request that never
   * arrived. The preview is kept beside the verdict rather than in it: it is
   * about *this check*, not about the block, and it is gone when the page is.
   */
  const record = useCallback(
    async (blockId: string, ask: () => Promise<ReportBlockCheck>) => {
      setChecking((current) => [...current, blockId])
      try {
        const answer = await guard(ask)
        if (answer) {
          putBlock(answer.block)
          setPreviews((current) => ({
            ...current,
            [blockId]: answer.preview
              ? `${answer.preview.row_count.toLocaleString()} ${
                  answer.preview.row_count === 1 ? 'row' : 'rows'
                }${answer.preview.truncated ? ' (capped)' : ''} in ${answer.preview.duration_ms} ms`
              : '',
          }))
        }
        return answer
      } finally {
        setChecking((current) => current.filter((id) => id !== blockId))
      }
    },
    [guard, putBlock],
  )

  const check = useCallback(
    (blockId: string) => record(blockId, () => api.checkBlock(reportId, blockId)),
    [record, reportId],
  )

  /**
   * The other road to the same verdict: the statement is the user's, and the
   * guard reads it instead of a model writing it.
   *
   * Deliberately the *same* landing as `check` — one row spliced in, one
   * preview line replaced — because the two answer the same question and a
   * second way of showing an answer is a second way of showing it wrong.
   */
  const saveSql = useCallback(
    (blockId: string, sql: string) =>
      record(blockId, () => api.editBlockSql(reportId, blockId, sql)),
    [record, reportId],
  )

  /** Every block that has never been near the guard, one at a time. */
  async function checkAll(pending: ReportBlock[]) {
    stopSweep.current = false
    for (const [index, block] of pending.entries()) {
      if (stopSweep.current) break
      setSweep({ done: index, total: pending.length, label: block.question })
      const answer = await check(block.id)
      // A failed *request* — not a refusal, which arrives as a verdict — means
      // the next nine will fail the same way. Stop and say so once.
      if (!answer) break
    }
    setSweep(null)
  }

  const sections = report?.sections ?? []
  const state = useMemo(() => readinessOf(sections), [sections])
  const unchecked = useMemo(
    () => sections.flatMap((s) => s.blocks).filter((b) => b.feasibility_status === 'UNCHECKED'),
    [sections],
  )

  if (!report) {
    return <EditorSkeleton onBack={onBack} error={error} />
  }

  const busy = proposing || sweep !== null

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <header className="rm-dash-header" style={headerStyle}>
        <button
          onClick={onBack}
          aria-label="Back to reports"
          className="rm-icon-btn"
          style={backButton}
        >
          <Icon.ArrowLeft size={15} />
        </button>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0, flex: 1, maxWidth: 460 }}>
          <InlineEdit
            ariaLabel="Report name"
            value={report.name}
            required
            style={{ fontSize: 16.5, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}
            onCommit={(name) => void patchReport({ name })}
          />
          <InlineEdit
            ariaLabel="Report description"
            value={report.description ?? ''}
            placeholder={`${sections.length} ${sections.length === 1 ? 'section' : 'sections'} — add a description`}
            style={{ fontSize: 12, color: 'var(--text-dim)' }}
            onCommit={(description) => void patchReport({ description: description || null })}
          />
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {sections.length > 0 && (
            <GhostButton
              onClick={() => setConfirmPropose(true)}
              disabled={busy}
              style={toolbarBtn}
              title="Ask the model for a fresh structure. This replaces the outline below."
            >
              <Icon.Sparkle size={13} /> Propose again
            </GhostButton>
          )}
          {unchecked.length > 0 && (
            <GhostButton
              onClick={() => void checkAll(unchecked)}
              disabled={busy}
              style={toolbarBtn}
            >
              <Icon.Check size={13} />
              {`Check ${unchecked.length} question${unchecked.length === 1 ? '' : 's'}`}
            </GhostButton>
          )}
          {/* A report already generated has documents to go back to, and the
              editor is not where you read one. Two doors rather than one: the
              latest is what "the report" usually means, and the history is the
              use case reports exist for — this quarter against last quarter.
              Rows only; opening one is what reads its results. */}
          {latestRun && (
            <GhostButton
              onClick={() => onOpenRun(latestRun.id)}
              style={toolbarBtn}
              title={`The last generation ${relativeTime(latestRun.created_at)}.`}
            >
              <Icon.Doc size={13} /> Last run
            </GhostButton>
          )}
          {runCount > 0 && (
            <GhostButton
              onClick={onHistory}
              style={toolbarBtn}
              title={`All ${runCount} generation${runCount === 1 ? '' : 's'} of this report.`}
            >
              <Icon.List size={13} /> History
              <span style={{ opacity: 0.6 }}>{runCount}</span>
            </GhostButton>
          )}
          {/* Enabled only when the outline can actually produce a document, and
              the reason it cannot travels on the tooltip — a disabled control
              that will not say why is how a user concludes it is broken. */}
          <PrimaryButton
            onClick={() => void generate()}
            disabled={busy || starting || !state.runnable}
            title={state.blocked ?? 'Generate this report from the outline below.'}
            style={{ padding: '8px 14px' }}
          >
            <Icon.Play size={12} /> {starting ? 'Starting…' : 'Generate'}
          </PrimaryButton>
        </div>
      </header>

      <div className="rm-page-pad" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <div
          style={{
            maxWidth: CONTENT_WIDTH,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {error && <ErrorNote>{error}</ErrorNote>}

          {report.connection_id === null && (
            <Note tone="amber">
              This report’s database connection has been removed, so its outline cannot be
              checked and it cannot be generated. Past runs stay readable.
            </Note>
          )}

          {/* The status panel leads, because it is the answer to the question a
              user opens this page with — how far along is this, and what is the
              next thing to do. */}
          {!sweep && !proposing && (
            <OutlineStatus report={report} state={state} sections={sections} />
          )}

          <RequestCard
            report={report}
            models={models}
            onPrompt={(prompt) => void patchReport({ prompt })}
            onModel={(llm_config_id) => void patchReport({ llm_config_id })}
          />

          {sweep && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '12px 14px',
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 12,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <ProgressBar
                  current={sweep.done}
                  total={sweep.total}
                  label={
                    <span dir="auto">
                      Checking “{sweep.label}”
                    </span>
                  }
                />
              </div>
              <GhostButton
                onClick={() => {
                  stopSweep.current = true
                }}
                style={{ ...toolbarBtn, flexShrink: 0 }}
              >
                Stop
              </GhostButton>
            </div>
          )}

          {/* Proposing over an existing outline replaces the whole page a few
              seconds from now, so it has to be visible from the middle of the
              screen — the header buttons going grey is not an answer to "did
              that do anything". */}
          {proposing && sections.length > 0 && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                padding: '11px 13px',
                fontSize: 12.5,
                color: 'var(--text-dim)',
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 12,
              }}
            >
              <Spinner /> Reading your schema and proposing a structure…
            </div>
          )}

          {sections.length === 0 ? (
            <EmptyState
              icon={<Icon.Doc size={20} />}
              title={proposing ? 'Proposing a structure…' : 'No outline yet'}
              body={
                proposing
                  ? 'The model is reading your schema and your request. This takes a few seconds.'
                  : report.prompt.trim()
                    ? 'A report starts as a structure you approve: headings, and under each one the questions that will become its charts. Propose one from your request, or build it by hand.'
                    : 'An outline is proposed from your request, and there is not one yet — say what this report should cover in the box above, or build the structure by hand.'
              }
              action={
                proposing ? (
                  <Spinner />
                ) : (
                  <div style={{ display: 'flex', gap: 8 }}>
                    <PrimaryButton onClick={() => void propose()} disabled={!report.prompt.trim()}>
                      <Icon.Sparkle size={14} /> Propose an outline
                    </PrimaryButton>
                    <GhostButton onClick={() => void addSection()}>Add a section</GhostButton>
                  </div>
                )
              }
            />
          ) : (
            sections.map((section, index) => (
              <SectionCard
                key={section.id}
                section={section}
                index={index}
                count={sections.length}
                busy={busy}
                checking={checking}
                previews={previews}
                onHeading={(heading) => void patchSection(section.id, { heading })}
                onIntent={(intent) => void patchSection(section.id, { intent })}
                onRemove={() => void removeSection(section.id)}
                onMove={(to) => void moveSection(index, to)}
                onAddBlock={() => void addBlock(section.id)}
                onBlock={(blockId, payload) => void patchBlock(blockId, payload)}
                onRemoveBlock={(blockId) => void removeBlock(section.id, blockId)}
                onMoveBlock={(from, to) => void moveBlock(section.id, from, to)}
                onCheck={(blockId) => void check(blockId)}
                onSaveSql={(blockId, sql) => saveSql(blockId, sql)}
              />
            ))
          )}

          {sections.length > 0 && (
            <GhostButton
              onClick={() => void addSection()}
              disabled={busy}
              style={{ alignSelf: 'flex-start', padding: '9px 14px' }}
            >
              <Icon.Plus size={14} /> Add a section
            </GhostButton>
          )}
        </div>
      </div>

      {confirmPropose && (
        <Modal
          title="Propose a new outline?"
          subtitle="One model call, from the request above."
          onClose={() => setConfirmPropose(false)}
          footer={
            <>
              <GhostButton onClick={() => setConfirmPropose(false)}>Cancel</GhostButton>
              <PrimaryButton onClick={() => void propose()}>Replace the outline</PrimaryButton>
            </>
          }
        >
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text2)' }}>
            This <strong>replaces</strong> every section and question below, including anything
            you have written or checked. Past runs are untouched — they keep their own copy of
            the structure they were generated from.
          </p>
        </Modal>
      )}
    </div>
  )
}

// ── the request ───────────────────────────────────────────────────────────
/**
 * What the report is for, and who writes it.
 *
 * The request is kept verbatim and is what the outline is proposed *from*, so
 * it is editable here rather than frozen at creation: rewriting it and
 * proposing again is the loop a user actually runs. The connection and the
 * language are not editable and say so — one because a report keyed to two
 * connections would cross disclosure policies, the other because every past
 * run's prose is written in it.
 */
function RequestCard({
  report, models, onPrompt, onModel,
}: {
  report: Report
  models: LlmConfig[]
  onPrompt: (prompt: string) => void
  onModel: (id: string) => void
}) {
  const [draft, setDraft] = useState(report.prompt)
  useEffect(() => setDraft(report.prompt), [report.prompt])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 11,
        padding: 15,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--text-strong)' }}>
          What this report covers
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          the outline is proposed from this, and every paragraph is written towards it
        </span>
      </div>

      <TextArea
        value={draft}
        rows={2}
        placeholder="e.g. an analysis of the last three months of sales, with the trend and the products that carried it"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft.trim() !== report.prompt) onPrompt(draft.trim())
        }}
      />

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          paddingTop: 10,
          borderTop: '1px solid var(--border)',
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <Icon.Database size={12} />
          {report.connection_name ?? 'Connection removed'}
        </span>
        <span aria-hidden style={{ opacity: 0.4 }}>·</span>
        <span>{report.language === 'fa' ? 'فارسی' : 'English'}</span>
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          title="The database and the language are fixed for the life of a report."
        >
          <Icon.Lock size={11} /> fixed
        </span>

        {/* The model is the one pinned-looking choice that is not: it decides
            who writes the prose, not what is in it. */}
        <label style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <span>Model</span>
          <select
            aria-label="Model"
            className="rm-toolbar-select"
            value={report.llm_config_id ?? ''}
            onChange={(event) => onModel(event.target.value)}
            style={{ fontSize: 12 }}
          >
            {report.llm_config_id === null && <option value="">Choose a model</option>}
            {models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}

// ── readiness ─────────────────────────────────────────────────────────────
interface ReadinessState {
  blocks: number
  ready: number
  empty: number
  infeasible: number
  unchecked: number
  /** Why the outline cannot be generated, or null when it can. */
  blocked: string | null
  runnable: boolean
}

function readinessOf(sections: ReportSection[]): ReadinessState {
  const blocks = sections.flatMap((section) => section.blocks)
  const count = (status: ReportFeasibility) =>
    blocks.filter((block) => block.feasibility_status === status).length
  const infeasible = blocks.filter((block) => block.feasibility_status === 'INFEASIBLE')
  const unchecked = count('UNCHECKED')
  const withSql = blocks.filter((block) => block.sql.trim() !== '').length

  let blocked: string | null = null
  if (infeasible.length > 0) {
    // The count, and the first reason — verbatim, as the guard wrote it.
    blocked =
      `${infeasible.length} question${infeasible.length === 1 ? '' : 's'} cannot be produced. `
      + `First: ${infeasible[0].feasibility_reason ?? 'no reason was given.'}`
  } else if (withSql === 0) {
    blocked = 'No question has a query yet — check them first.'
  }

  return {
    blocks: blocks.length,
    ready: count('FEASIBLE'),
    empty: count('EMPTY'),
    infeasible: infeasible.length,
    unchecked,
    blocked,
    runnable: blocked === null,
  }
}

/**
 * Where the report is in the four things building one actually involves.
 *
 * The page used to be a stack of identical cards with one coloured sentence
 * somewhere in it, and a user could not tell from looking whether they were
 * three clicks from a document or thirty. Describe → Structure → Check →
 * Generate is not invented ceremony: it is exactly the sequence the API
 * enforces, and naming it is the difference between a form and a product.
 *
 * Every step's caption is a count read off the outline, so the panel is also
 * the answer to "how much is left".
 */
type StepState = 'done' | 'current' | 'todo'

function OutlineStatus({
  report, state, sections,
}: {
  report: Report
  state: ReadinessState
  sections: ReportSection[]
}) {
  const checked = state.ready + state.empty
  const described = report.prompt.trim().length > 0
  const structured = sections.length > 0 && state.blocks > 0
  const verified = structured && state.unchecked === 0 && state.infeasible === 0

  const steps: { label: string; caption: string; done: boolean }[] = [
    {
      label: 'Describe',
      caption: described ? 'request written' : 'say what it covers',
      done: described,
    },
    {
      label: 'Structure',
      caption: structured
        ? `${sections.length} section${sections.length === 1 ? '' : 's'}, `
          + `${state.blocks} question${state.blocks === 1 ? '' : 's'}`
        : 'no outline yet',
      done: structured,
    },
    {
      label: 'Check',
      caption: state.blocks === 0
        ? 'nothing to check'
        : verified
          ? 'all questions checked'
          : `${checked} of ${state.blocks} checked`,
      done: verified,
    },
    {
      label: 'Generate',
      caption: state.runnable ? 'ready to run' : 'not ready yet',
      done: false,
    },
  ]
  // The first unfinished step is the one you are on. Generate is never "done"
  // here — it is an action, and a report is never finished with being run.
  const current = steps.findIndex((step) => !step.done)

  const counts = (
    [
      { label: 'ready', value: state.ready, tone: 'green' },
      { label: 'no rows', value: state.empty, tone: 'amber' },
      { label: 'cannot be produced', value: state.infeasible, tone: 'red' },
      { label: 'not checked', value: state.unchecked, tone: 'neutral' },
    ] as { label: string; value: number; tone: ChipTone }[]
  ).filter((count) => count.value > 0)

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 13,
        padding: '14px 16px',
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
      }}
    >
      {/* Fragments rather than list items with `display: contents`: that
          declaration lays the steps out correctly and removes them from the
          accessibility tree in more than one browser. */}
      <div
        className="rm-outline-steps"
        aria-label="Progress towards a generated report"
        style={{ display: 'flex', alignItems: 'center', gap: 4 }}
      >
        {steps.map((step, index) => (
          <Fragment key={step.label}>
            <Step
              index={index}
              label={step.label}
              caption={step.caption}
              state={step.done ? 'done' : index === current ? 'current' : 'todo'}
            />
            {index < steps.length - 1 && (
              <span
                aria-hidden
                className="rm-outline-step-line"
                style={{
                  flex: 1,
                  minWidth: 10,
                  height: 1,
                  background: step.done ? 'var(--accent-border)' : 'var(--border)',
                }}
              />
            )}
          </Fragment>
        ))}
      </div>

      {state.blocks > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap',
            paddingTop: 11,
            borderTop: '1px solid var(--border)',
          }}
        >
          {counts.map((count) => (
            <Chip key={count.label} tone={count.tone}>
              {count.value} {count.label}
            </Chip>
          ))}
          <span
            style={{
              flex: 1,
              minWidth: 220,
              fontSize: 11.5,
              lineHeight: 1.6,
              color: 'var(--text-dim)',
            }}
          >
            {advice(state)}
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * The one sentence that says what to do next, and why it matters.
 *
 * Each of these is a real failure someone would otherwise meet inside a
 * finished document: an unchecked question becomes an error message in the
 * report, and an empty result becomes a section that says so plainly — which is
 * correct, and worth knowing before you spend the generation on it.
 */
function advice(state: ReadinessState): string {
  if (state.blocks === 0) {
    return 'This outline has headings but no questions. A section with nothing under it '
      + 'has no results to be written from.'
  }
  if (state.infeasible > 0) return state.blocked ?? ''
  if (state.unchecked > 0) {
    return 'A question that has never been near the guard carries no query, so it would '
      + 'arrive in the finished document as an error message. Check it first.'
  }
  if (state.empty > 0) {
    return 'Some questions return no rows for now. A section whose results are all empty '
      + 'says so plainly instead of inventing numbers.'
  }
  return 'Every question has a validated query. Generating writes a new document and '
    + 'leaves earlier ones untouched.'
}

function Step({
  index, label, caption, state,
}: {
  index: number
  label: string
  caption: string
  state: StepState
}) {
  const accent = state !== 'todo'
  return (
    <span
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        // Allowed to shrink, so the caption's ellipsis has something to
        // ellipsis *against*. Fixed-width steps with `nowrap` captions push the
        // panel wider than the page at the awkward viewport sizes.
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'grid',
          placeItems: 'center',
          width: 22,
          height: 22,
          flexShrink: 0,
          borderRadius: 999,
          fontSize: 11,
          fontWeight: 700,
          background: state === 'done' ? 'var(--accent)' : 'transparent',
          border: `1.5px solid ${accent ? 'var(--accent)' : 'var(--border-strong)'}`,
          color:
            state === 'done'
              ? 'var(--on-accent)'
              : accent
                ? 'var(--accent)'
                : 'var(--text-faint)',
        }}
      >
        {state === 'done' ? <Icon.Check size={11} /> : index + 1}
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, lineHeight: 1.3 }}>
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 650,
            color: accent ? 'var(--text-strong)' : 'var(--text-faint)',
          }}
        >
          {label}
        </span>
        <span
          style={{
            fontSize: 10.5,
            color: state === 'current' ? 'var(--accent)' : 'var(--text-faint)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {caption}
        </span>
      </span>
    </span>
  )
}

// ── one section ───────────────────────────────────────────────────────────
function SectionCard({
  section, index, count, busy, checking, previews,
  onHeading, onIntent, onRemove, onMove,
  onAddBlock, onBlock, onRemoveBlock, onMoveBlock, onCheck, onSaveSql,
}: {
  section: ReportSection
  index: number
  count: number
  busy: boolean
  checking: string[]
  previews: Record<string, string>
  onHeading: (heading: string) => void
  onIntent: (intent: string) => void
  onRemove: () => void
  onMove: (to: number) => void
  onAddBlock: () => void
  onBlock: (blockId: string, payload: Record<string, unknown>) => void
  onRemoveBlock: (blockId: string) => void
  onMoveBlock: (from: number, to: number) => void
  onCheck: (blockId: string) => void
  onSaveSql: (blockId: string, sql: string) => Promise<ReportBlockCheck | null>
}) {
  const [confirmRemove, setConfirmRemove] = useState(false)
  const summary = section.kind === 'EXECUTIVE_SUMMARY'
  // The section's own readiness, so a long outline can be read at a glance
  // instead of by opening every row in it.
  const pending = section.blocks.filter((b) => b.feasibility_status === 'UNCHECKED').length
  const broken = section.blocks.filter((b) => b.feasibility_status === 'INFEASIBLE').length

  return (
    <section
      className="rm-outline-card"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        padding: '15px 16px',
        background: 'var(--panel)',
        border: `1px solid ${
          broken > 0
            ? 'var(--red-border)'
            : summary
              ? 'var(--accent-border)'
              : 'var(--border)'
        }`,
        borderRadius: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 11 }}>
        {/* The number a reader will see in the finished document, shown while
            the structure is still being arranged — so reordering has a visible
            consequence rather than only moving a card. */}
        <span
          aria-hidden
          className="mono"
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 26,
            height: 26,
            flexShrink: 0,
            marginTop: 1,
            borderRadius: 8,
            fontSize: 11.5,
            fontWeight: 700,
            fontVariantNumeric: 'tabular-nums',
            background: summary ? 'var(--accent-bg)' : 'var(--panel-alt)',
            border: `1px solid ${summary ? 'var(--accent-border)' : 'var(--border)'}`,
            color: summary ? 'var(--accent)' : 'var(--text-dim)',
          }}
        >
          {summary ? <Icon.Sparkle size={12} /> : index + 1}
        </span>

        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flexWrap: 'wrap' }}>
            <InlineEdit
              ariaLabel={`Heading of section ${index + 1}`}
              value={section.heading}
              required
              style={{ fontSize: 15.5, fontWeight: 650, color: 'var(--text-strong)' }}
              onCommit={onHeading}
            />
            {summary && <Chip tone="accent">Written last</Chip>}
            {!summary && section.blocks.length > 0 && (
              <span style={{ fontSize: 11, color: 'var(--text-faint)', flexShrink: 0 }}>
                {section.blocks.length}
                {section.blocks.length === 1 ? ' question' : ' questions'}
                {broken > 0
                  ? ` · ${broken} blocked`
                  : pending > 0
                    ? ` · ${pending} to check`
                    : ' · checked'}
              </span>
            )}
          </div>
          {/* Prompt input, not display text: the one line that tells the model
              what this section's paragraph is *for*. Labelled, because an
              unlabelled second line reads as a subtitle the reader will see. */}
          <InlineEdit
            ariaLabel={`What section ${index + 1} should cover`}
            value={section.intent}
            placeholder="Brief for the writer — what this section must establish, and against what"
            style={{ fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.55 }}
            multiline
            onCommit={onIntent}
          />
        </div>

        <span
          className="rm-outline-actions"
          style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 2 }}
        >
          <Reorder
            label={`section ${index + 1}`}
            index={index}
            count={count}
            disabled={busy}
            onMove={onMove}
          />
          <QuietDanger label={`Remove section ${index + 1}`} onClick={() => setConfirmRemove(true)}>
            <Icon.Trash />
          </QuietDanger>
        </span>
      </div>

      {summary && (
        <p
          style={{
            margin: 0,
            fontSize: 12,
            lineHeight: 1.6,
            color: 'var(--text-faint)',
            paddingInlineStart: 37,
          }}
        >
          Written last, from the sections it summarises — so it needs no questions of its own.
        </p>
      )}

      {section.blocks.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            paddingInlineStart: 37,
          }}
        >
          {section.blocks.map((block, blockIndex) => (
            <BlockRow
              key={block.id}
              block={block}
              index={blockIndex}
              count={section.blocks.length}
              busy={busy}
              checking={checking.includes(block.id)}
              preview={previews[block.id]}
              onChange={(payload) => onBlock(block.id, payload)}
              onRemove={() => onRemoveBlock(block.id)}
              onMove={(to) => onMoveBlock(blockIndex, to)}
              onCheck={() => onCheck(block.id)}
              onSaveSql={(sql) => onSaveSql(block.id, sql)}
            />
          ))}
        </div>
      )}

      <div style={{ paddingInlineStart: 37 }}>
        <GhostButton
          onClick={onAddBlock}
          disabled={busy}
          style={{ padding: '6px 11px', fontSize: 12.5 }}
        >
          <Icon.Plus size={13} /> Add a question
        </GhostButton>
      </div>

      {confirmRemove && (
        <Modal
          title="Remove this section?"
          onClose={() => setConfirmRemove(false)}
          footer={
            <>
              <GhostButton onClick={() => setConfirmRemove(false)}>Cancel</GhostButton>
              <DangerButton
                onClick={() => {
                  setConfirmRemove(false)
                  onRemove()
                }}
              >
                Remove
              </DangerButton>
            </>
          }
        >
          <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text2)' }} dir="auto">
            “{section.heading}” and its{' '}
            {section.blocks.length === 1 ? 'question' : `${section.blocks.length} questions`} leave
            the outline. Past runs keep their own copy and stay readable.
          </p>
        </Modal>
      )}
    </section>
  )
}

// ── one block ─────────────────────────────────────────────────────────────
/**
 * One question, and the guard's answer about it.
 *
 * The question is the whole control. Everything else on the row — the type, the
 * window, the verdict — is chrome around it, which is why the type and window
 * are quiet selects rather than labelled fields: they have defaults that are
 * right most of the time, and the sentence above them is what the user came to
 * write.
 */
function BlockRow({
  block, index, count, busy, checking, preview, onChange, onRemove, onMove, onCheck,
  onSaveSql,
}: {
  block: ReportBlock
  index: number
  count: number
  busy: boolean
  checking: boolean
  preview?: string
  onChange: (payload: Record<string, unknown>) => void
  onRemove: () => void
  onMove: (to: number) => void
  onCheck: () => void
  onSaveSql: (sql: string) => Promise<ReportBlockCheck | null>
}) {
  const [showSql, setShowSql] = useState(false)
  const verdict = FEASIBILITY[block.feasibility_status]
  const unchecked = block.feasibility_status === 'UNCHECKED'
  // A statement somebody typed. It survives a reworded question — the API
  // keeps it and only drops the verdict — so the check button here would
  // *replace* it rather than fill a gap, and has to say so.
  const ownSql = block.sql !== '' && block.sql_origin !== 'GENERATED'

  return (
    <div
      className="rm-outline-block"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
        padding: '11px 13px',
        background: 'var(--panel-alt)',
        border: `1px solid ${
          block.feasibility_status === 'INFEASIBLE' ? 'var(--red-border)' : 'var(--border)'
        }`,
        // The rail carries the verdict. It is the one cue that survives
        // skim-reading a twelve-question outline, and it costs no space.
        borderInlineStartWidth: 3,
        borderInlineStartColor: verdict.rail,
        borderRadius: 9,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span
          aria-hidden
          className="mono"
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: 'var(--text-faint)',
            fontVariantNumeric: 'tabular-nums',
            paddingTop: 3,
            flexShrink: 0,
          }}
        >
          {index + 1}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <InlineEdit
            ariaLabel={`Question ${index + 1}`}
            value={block.question}
            required
            multiline
            style={{ fontSize: 13.5, color: 'var(--text)', lineHeight: 1.55 }}
            onCommit={(question) => onChange({ question })}
          />
        </div>
        <span
          className="rm-outline-actions"
          style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 2 }}
        >
          <Reorder
            label={`question ${index + 1}`}
            index={index}
            count={count}
            disabled={busy}
            onMove={onMove}
            size={22}
          />
          <QuietDanger label={`Remove question ${index + 1}`} onClick={onRemove} size={24}>
            <Icon.Trash size={12} />
          </QuietDanger>
        </span>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 7,
          flexWrap: 'wrap',
          paddingInlineStart: 22,
        }}
      >
        <select
          aria-label={`How question ${index + 1} is shown`}
          className="rm-toolbar-select"
          value={block.block_type}
          disabled={busy}
          onChange={(event) => onChange({ block_type: event.target.value })}
          style={{ fontSize: 11.5, padding: '4px 8px' }}
        >
          {BLOCK_TYPES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {/* Changing the window rewrites the question the SQL answered, so the
            block goes back to unchecked — the API does that and returns the
            reset row, which is what lands in the chip beside this. */}
        <select
          aria-label={`Time window of question ${index + 1}`}
          className="rm-toolbar-select"
          value={block.time_window}
          disabled={busy}
          onChange={(event) => onChange({ time_window: event.target.value })}
          style={{ fontSize: 11.5, padding: '4px 8px' }}
          title="Resolved by the database on every run, so a re-run months from now describes then, not now."
        >
          {TIME_WINDOWS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {checking ? (
          <span
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-dim)' }}
          >
            <Spinner size={12} /> Checking against your schema…
          </span>
        ) : (
          <Chip tone={verdict.tone}>{verdict.label}</Chip>
        )}

        {preview && !checking && (
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            {preview}
          </span>
        )}

        <span style={{ marginInlineStart: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          {/* Always offered now, not only when there is a statement: with none,
              this is where one gets written by hand. */}
          <GhostButton
            onClick={() => setShowSql((open) => !open)}
            style={{ padding: '4px 9px', fontSize: 11.5 }}
          >
            {showSql ? 'Hide SQL' : 'SQL'}
          </GhostButton>
          {/* `is-wanted` is the tile editor's affordance for "this is the next
              thing to do", and an unchecked question is exactly that: it is the
              one state that stops the report being generated at all. It is not
              claimed for a block whose SQL somebody wrote, because there the
              next thing to do is in the box, not here. */}
          <button
            type="button"
            onClick={onCheck}
            disabled={busy || checking}
            className={`rm-check${unchecked && !checking && !ownSql ? ' is-wanted' : ''}`}
            style={{ marginInlineStart: 0, padding: '4px 10px', fontSize: 11.5 }}
            title={
              ownSql
                ? 'Asks the model for a new statement from the question, replacing the one you wrote.'
                : 'Turns the question into a query and checks it against your schema.'
            }
          >
            {checking ? <Spinner size={11} /> : <Icon.Check size={11} />}
            {ownSql ? 'Rewrite' : unchecked ? 'Check' : 'Check again'}
          </button>
        </span>
      </div>

      {/* The guard's own sentence, exactly as it wrote it. A re-worded rejection
          is one the user cannot act on. */}
      {block.feasibility_reason && !checking && (
        <div
          style={{
            display: 'flex',
            gap: 7,
            marginInlineStart: 22,
            fontSize: 11.5,
            lineHeight: 1.5,
            color: block.feasibility_status === 'INFEASIBLE' ? 'var(--red)' : 'var(--amber)',
          }}
        >
          <span aria-hidden style={{ display: 'flex', paddingTop: 1, flexShrink: 0 }}>
            <Icon.Alert size={12} />
          </span>
          <span>{block.feasibility_reason}</span>
        </div>
      )}

      {showSql && (
        <div
          style={{
            marginInlineStart: 22,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}
        >
          <BlockSql block={block} busy={busy || checking} onSave={onSaveSql} />
          {/* Said outside the box, because the box is where someone writes and
              this is a fact about what happens afterwards. Invariant #1, in the
              one place a user could mistake "it saved" for "it is trusted". */}
          <span style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--text-faint)' }}>
            Re-validated against the connection’s current schema on every run — being
            stored grants it nothing.
          </span>
        </div>
      )}
    </div>
  )
}

/** Who wrote the statement now on a block. Provenance, never a trust signal. */
const SQL_ORIGIN: Record<string, string> = {
  GENERATED: 'Written by the model from the question above',
  GENERATED_EDITED: 'Written by the model, then edited by you',
  HANDWRITTEN: 'Written by you',
}

/**
 * A block's statement, editable.
 *
 * The same shape as the tile editor's `SqlBox` and reusing its stylesheet,
 * because it is the same act: type a statement, hand it to the guard, read the
 * verdict. What is different is where the verdict lands — on the block, as its
 * feasibility, beside the one the model's own draft would have produced. The
 * two roads to that verdict are `POST .../check` and this, and they answer in
 * the same shape so the row above renders either without knowing which.
 *
 * Saving is not authorisation to run. `execute_saved_sql` guards this again on
 * every execution against the connection's *current* snapshot, and
 * `sql_origin` grants it nothing — which is the sentence under the box, said
 * once rather than implied.
 */
function BlockSql({
  block, busy, onSave,
}: {
  block: ReportBlock
  busy: boolean
  onSave: (sql: string) => Promise<ReportBlockCheck | null>
}) {
  const [draft, setDraft] = useState(block.sql)
  const [saving, setSaving] = useState(false)

  // The row is spliced in from the response, so the box follows the stored
  // statement whenever that changes underneath it — a check that rewrote it,
  // a save that came back normalised.
  useEffect(() => setDraft(block.sql), [block.sql])

  const dirty = draft.trim() !== block.sql.trim()
  const empty = draft.trim() === ''

  async function save() {
    if (empty || saving) return
    setSaving(true)
    try {
      await onSave(draft.trim())
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rm-sqlbox">
      <TextArea
        className="mono"
        aria-label={`SQL for “${block.question}”`}
        // Code, not prose: `TextArea` defaults to `dir="auto"`, which would
        // right-align a statement under a Persian question.
        dir="ltr"
        value={draft}
        rows={7}
        spellCheck={false}
        placeholder="No statement yet. Check the question to have one written, or write one here."
        disabled={busy}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
            event.preventDefault()
            void save()
          }
        }}
        style={{ fontSize: 12, minHeight: 120 }}
      />
      <div className="rm-sqlbox-bar">
        {/* `flex: 1` rather than leaning on `.rm-check`'s `margin-left: auto`:
            the auto margin is physical, and this bar carries three controls
            where the tile editor's carries one. */}
        <span
          className={`rm-sqlbox-hint${dirty && !saving ? ' is-stale' : ''}`}
          style={{ flex: 1 }}
        >
          {saving && <Spinner size={11} />}
          {saving
            ? 'Checking…'
            : dirty
              ? 'Edited — not checked yet'
              : SQL_ORIGIN[block.sql_origin] ?? ''}
        </span>
        {block.sql && !dirty && <CopyButton text={block.sql} label="Copy" />}
        {dirty && (
          <GhostButton
            onClick={() => setDraft(block.sql)}
            disabled={saving}
            style={{ padding: '4px 9px', fontSize: 11.5 }}
          >
            Revert
          </GhostButton>
        )}
        <button
          type="button"
          className={`rm-check${dirty && !saving ? ' is-wanted' : ''}`}
          onClick={() => void save()}
          disabled={busy || saving || empty || !dirty}
          title="Guard this statement and run it once for the preview. No model is asked."
        >
          <Icon.Play size={12} />
          Check
        </button>
      </div>
    </div>
  )
}

// ── small pieces ──────────────────────────────────────────────────────────
/** Up and down, which is the whole of reordering in v1. */
function Reorder({
  label, index, count, disabled, onMove, size = 24,
}: {
  label: string
  index: number
  count: number
  disabled: boolean
  onMove: (to: number) => void
  size?: number
}) {
  const style: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: size,
    height: size / 2 + 3,
    padding: 0,
    background: 'transparent',
    border: 'none',
    borderRadius: 5,
    color: 'var(--text-faint)',
    cursor: 'pointer',
    ['--rm-hover-bg' as string]: 'var(--panel-hover)',
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flexShrink: 0, paddingTop: 2 }}>
      <button
        className="rm-icon-btn"
        aria-label={`Move ${label} up`}
        disabled={disabled || index === 0}
        onClick={() => onMove(index - 1)}
        style={{ ...style, opacity: index === 0 ? 0.3 : 1 }}
      >
        <span style={{ display: 'flex', transform: 'rotate(180deg)' }}>
          <Icon.ArrowDown size={12} />
        </span>
      </button>
      <button
        className="rm-icon-btn"
        aria-label={`Move ${label} down`}
        disabled={disabled || index === count - 1}
        onClick={() => onMove(index + 1)}
        style={{ ...style, opacity: index === count - 1 ? 0.3 : 1 }}
      >
        <Icon.ArrowDown size={12} />
      </button>
    </div>
  )
}

/** Neutral until hovered, then unmistakably red — `semantic.tsx`'s rule. */
function QuietDanger({
  label, onClick, children, size = 28,
}: {
  label: string
  onClick: () => void
  children: React.ReactNode
  size?: number
}) {
  const [hover, setHover] = useState(false)
  return (
    <button
      aria-label={label}
      title={label}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        borderRadius: 7,
        border: `1px solid ${hover ? 'var(--red-border)' : 'var(--border-strong)'}`,
        background: hover ? 'var(--red-bg)' : 'transparent',
        color: hover ? 'var(--red)' : 'var(--text-faint)',
        cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  )
}

function Note({ tone, children }: { tone: 'green' | 'amber' | 'red'; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        padding: '10px 12px',
        fontSize: 12.5,
        lineHeight: 1.55,
        color: 'var(--text2)',
        background: `var(--${tone}-bg)`,
        border: `1px solid var(--${tone}-border)`,
        borderRadius: 10,
      }}
    >
      <span aria-hidden style={{ display: 'flex', paddingTop: 1, color: `var(--${tone})`, flexShrink: 0 }}>
        {tone === 'green' ? <Icon.Check size={13} /> : <Icon.Alert size={13} />}
      </span>
      <span>{children}</span>
    </div>
  )
}

/** The header's ghost buttons, one notch tighter than the default. */
const toolbarBtn: React.CSSProperties = { fontSize: 12.5, padding: '7px 12px' }

/** `.rm-dash-header` carries the padding and the tone; the layout is inline,
 *  the same way the dashboard's own header declares it. */
const headerStyle: React.CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  alignItems: 'center',
  gap: 10,
  borderBottom: '1px solid var(--border)',
  flexShrink: 0,
}

function reorder<T>(items: T[], from: number, to: number): T[] {
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

function EditorSkeleton({ onBack, error }: { onBack: () => void; error: string | null }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <header className="rm-dash-header" style={headerStyle}>
        <button
          onClick={onBack}
          aria-label="Back to reports"
          className="rm-icon-btn"
          style={backButton}
        >
          <Icon.ArrowLeft size={15} />
        </button>
        <div className="rm-bone" style={{ width: 190, height: 15, borderRadius: 6 }} />
      </header>
      <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: CONTENT_WIDTH, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error ? (
            <ErrorNote>{error}</ErrorNote>
          ) : (
            [0, 1, 2].map((index) => (
              <div
                key={index}
                aria-hidden
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 10,
                  padding: 15,
                  background: 'var(--panel)',
                  border: '1px solid var(--border)',
                  borderRadius: 12,
                }}
              >
                <div className="rm-bone" style={{ width: '42%', height: 13, borderRadius: 6 }} />
                <div className="rm-bone" style={{ width: '72%', height: 9, borderRadius: 6 }} />
                <div className="rm-bone" style={{ width: '90%', height: 34, borderRadius: 8, marginTop: 4 }} />
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

// ── the viewer ────────────────────────────────────────────────────────────
/**
 * Watching a report build itself, and refining it afterwards.
 *
 * The whole progressive render is the poll response: `GET /runs/{rid}` returns
 * the run and **everything written so far**, so a half-finished generation is a
 * half-finished document and needs no protocol of its own. A browser reloaded
 * mid-run resumes exactly where it was, because the server was never holding
 * the state — the rows were.
 *
 * What the reader watches, in the order it happens: the queries land (every
 * block at once, from one `execute_many`), then the paragraphs arrive one
 * section at a time, then the executive summary last, written from the
 * sections it summarises. The sections with numbers and no prose yet are not a
 * loading state to hide — they are the document being written.
 *
 * Two refinements are saved onto the **run**, never the template, for the
 * reason `edited_prose` is a second column: a regeneration must not destroy
 * writing, and a template must not carry one run's wording. Editing a
 * paragraph is one; picking a different chart type is the other.
 */
const POLL_MS = 1500

export function ReportRunViewer({
  reportId, runId, report, reportName, onBack, onHistory,
}: {
  reportId: string
  runId: string
  /**
   * The card the index already holds, for the cover block.
   *
   * The viewer fetches the *run*, and a run carries no connection name and no
   * description — it carries the model it used and the language it was written
   * in. Passing the card down is cheaper than a second request and, more to the
   * point, it is what lets the document say which database it describes, which
   * is the first thing a reader of a printed report needs to know.
   */
  report: ReportSummary | null
  /** Kept for the header while the index is still loading its cards. */
  reportName: string
  onBack: () => void
  /** Every other generation of this report — the reader's way between them. */
  onHistory: () => void
}) {
  const [run, setRun] = useState<ReportRunDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Bumped when a retry puts a finished run back to work, which is what
  // restarts a poll that had correctly stopped.
  const [pollKey, setPollKey] = useState(0)

  useEffect(() => {
    let stopped = false
    let timer: number | undefined

    async function tick(): Promise<void> {
      if (stopped) return
      // **Paused, not throttled.** A hidden tab asks the customer's database
      // for nothing; the timer stays so returning to the tab costs one interval
      // rather than a mount.
      if (document.hidden) {
        timer = window.setTimeout(() => void tick(), POLL_MS)
        return
      }
      try {
        const next = await api.run(reportId, runId)
        if (stopped) return
        setRun(next)
        setError(null)
        if (!isReportRunInFlight(next.status)) return
      } catch (err) {
        if (!stopped) setError(err instanceof Error ? err.message : 'Could not read this run.')
        return
      }
      timer = window.setTimeout(() => void tick(), POLL_MS)
    }

    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [reportId, runId, pollKey])

  const document_ = useMemo(() => (run ? assembleDocument(run) : []), [run])
  // Numbered once over the assembled document, so "Figure 4" means the same
  // thing in the body, in the appendix and on paper.
  const figures = useMemo(() => figureNumbers(document_), [document_])
  const headline = useMemo(() => keyFigures(document_), [document_])

  const language = run?.language ?? report?.language ?? 'en'
  const t = labelsFor(language)
  // The document lays out in its own direction rather than per element. A
  // Persian report whose figure numbers and captions run left-to-right around
  // right-to-left prose is the seam a reader sees before they read a word.
  const dir = language === 'fa' ? 'rtl' : 'ltr'

  // What gets printed: the article, which is exactly the saved run rendered as
  // a document. Nothing outside it reaches the page, and nothing the printer
  // does reaches the run.
  const article = useRef<HTMLElement>(null)
  const [printing, setPrinting] = useState(false)

  async function print() {
    setPrinting(true)
    try {
      await printReport(article.current)
    } finally {
      setPrinting(false)
    }
  }

  async function cancel() {
    setBusy(true)
    try {
      const stopped = await api.cancelRun(reportId, runId)
      // The row is marked cancelled by the route rather than by the worker, so
      // this lands even while an in-flight query is still finishing.
      setRun((current) => (current ? { ...current, ...stopped } : current))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That did not work.')
    } finally {
      setBusy(false)
    }
  }

  async function retry(sectionId: string) {
    setBusy(true)
    setError(null)
    try {
      const next = await api.retrySection(reportId, runId, sectionId)
      // Straight back onto the poll the page is already running: the rest of
      // the document stays on screen and this section rebuilds inside it.
      setRun((current) => (current ? { ...current, ...next } : current))
      setPollKey((key) => key + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That section could not be retried.')
    } finally {
      setBusy(false)
    }
  }

  async function saveProse(sectionId: string, text: string | null) {
    const row = await api.editProse(reportId, runId, sectionId, text)
    setRun((current) =>
      current
        ? { ...current, sections: current.sections.map((s) => (s.id === row.id ? row : s)) }
        : current,
    )
  }

  function replaceBlock(next: ReportBlockResult) {
    setRun((current) =>
      current
        ? { ...current, blocks: current.blocks.map((b) => (b.id === next.id ? next : b)) }
        : current,
    )
  }

  const status = run ? RUN_TONE[run.status] : null
  const running = run !== null && isReportRunInFlight(run.status)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <header className="rm-dash-header" style={headerStyle}>
        <button
          onClick={onBack}
          aria-label="Back to the outline"
          className="rm-icon-btn"
          style={backButton}
        >
          <Icon.ArrowLeft size={15} />
        </button>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
          <span
            dir="auto"
            style={{
              fontSize: 16.5,
              fontWeight: 700,
              letterSpacing: '-0.01em',
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {reportName}
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
            {run
              ? run.finished_at
                ? `generated ${relativeTime(run.finished_at)}`
                : run.started_at
                  ? `started ${relativeTime(run.started_at)}`
                  : 'queued'
              : 'loading…'}
            {run && run.model_snapshot.model && (
              <>
                <span aria-hidden style={{ opacity: 0.4 }}> · </span>
                {run.model_snapshot.model}
              </>
            )}
          </span>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          {running && <Spinner size={13} />}
          {status && <Chip tone={status.tone}>{status.label}</Chip>}
          {/* The way between generations, from inside one. A reader comparing
              this quarter with last quarter should not have to go back through
              the outline to do it. */}
          <GhostButton onClick={onHistory} style={toolbarBtn}>
            <Icon.List size={12} /> History
          </GhostButton>
          {/* Printing is the deliverable, not an extra: a report is a document
              and a document leaves the tool. The stylesheet hides the app
              chrome, forces the light tokens and keeps a figure whole across a
              page break; `report-print.ts` does the parts CSS cannot reach —
              the fonts, and redrawing the charts at page width. Offered only
              once there is a document to print. */}
          {!running && document_.length > 0 && (
            <GhostButton onClick={() => void print()} disabled={printing} style={toolbarBtn}>
              {printing ? <Spinner size={12} /> : <Icon.Doc size={12} />} {t.print}
            </GhostButton>
          )}
          {running && (
            <GhostButton onClick={() => void cancel()} disabled={busy} style={toolbarBtn}>
              Stop
            </GhostButton>
          )}
        </div>
      </header>

      {/* The progress bar belongs under the header rather than in it: the two
          counters and the phase are a sentence, and a sentence squeezed into a
          toolbar is a truncated sentence. */}
      {running && run && run.progress_total > 0 && (
        <div style={{ padding: '10px 20px 0' }}>
          <ProgressBar
            current={run.progress_current}
            total={run.progress_total}
            label={<span dir="auto">{run.phase || 'Working…'}</span>}
          />
        </div>
      )}

      <div
        className="rm-page-pad rm-report-scroll"
        style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
      >
        <article
          ref={article}
          className="rm-report"
          dir={dir}
          style={{
            maxWidth: 860,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 30,
          }}
        >
          {error && <ErrorNote>{error}</ErrorNote>}

          {/* A run that failed as a whole — a tightened disclosure policy, a
              removed connection — says so once, at the top, in the API's own
              words. §7's second gate is the one that is easy to forget, and
              this is where a user finds out it fired. */}
          {run?.status === 'FAILED' && run.error_message && (
            <Note tone="red">{run.error_message}</Note>
          )}
          {run?.status === 'CANCELLED' && (
            <Note tone="amber">
              This generation was stopped. What had already been computed is kept below —
              it was paid for.
            </Note>
          )}

          {run !== null && document_.length > 0 && (
            <DocumentCover
              title={report?.name ?? reportName}
              subtitle={report?.description ?? null}
              connection={report?.connection_name ?? null}
              run={run}
              t={t}
            />
          )}

          {/* The band normally rides under the executive summary, which is
              where a reader looks for it. A summary is an ordinary section and
              can be deleted from the outline — so when there is none, the
              figures go straight under the cover rather than disappearing. */}
          {!document_.some((section) => section.isSummary) && (
            <HeadlineFigures figures={headline} t={t} />
          )}

          {run === null ? (
            <ViewerSkeleton />
          ) : document_.length === 0 ? (
            <EmptyState
              icon={<Icon.Doc size={20} />}
              title={running ? 'Running the queries…' : 'This run wrote nothing'}
              body={
                running
                  ? 'Every question is executed first, then each section is written over its own results. The document appears section by section as it is written.'
                  : 'No result and no paragraph was written. The message above says why.'
              }
              action={running ? <Spinner /> : undefined}
            />
          ) : (
            document_.map((section) => (
              <SectionView
                key={section.key}
                reportId={reportId}
                runId={runId}
                section={section}
                figures={figures}
                /* The headline band rides under the executive summary: it is the
                   "at a glance" a reader looks for first, and every number in it
                   was computed by `plan_kpi` rather than written by a model. */
                headline={section.isSummary ? headline : []}
                t={t}
                busy={busy || running}
                onRetry={() => {
                  if (section.sectionId) void retry(section.sectionId)
                }}
                onProse={async (text) => {
                  if (section.sectionId) await saveProse(section.sectionId, text)
                }}
                onBlock={replaceBlock}
              />
            ))
          )}

          {run !== null && !running && document_.length > 0 && (
            <MethodNotes sections={document_} figures={figures} run={run} t={t} />
          )}
        </article>
      </div>
    </div>
  )
}

// ── the cover ─────────────────────────────────────────────────────────────
/**
 * What a document says about itself before it says anything else.
 *
 * A page of charts under a name is a screenshot; a report opens by stating what
 * it is, what it was built from and when — because a reader who finds it three
 * months later has no other way to know whether it is still true. Every field
 * here is a fact the run already carried and the page was throwing away: the
 * connection it read, the model that wrote the prose, the moment the numbers
 * were computed.
 *
 * Nothing here is model-written, which is the point: the one part of the
 * document that establishes its provenance cannot be the part that was
 * generated.
 */
function DocumentCover({
  title, subtitle, connection, run, t,
}: {
  title: string
  subtitle: string | null
  connection: string | null
  run: ReportRunDetail
  t: Record<string, string>
}) {
  const when = run.finished_at ?? run.started_at ?? run.created_at
  const meta: { label: string; value: string }[] = [
    ...(connection ? [{ label: t.dataSource, value: connection }] : []),
    { label: t.generated, value: new Date(when).toLocaleString() },
    ...(run.model_snapshot.model
      ? [{ label: t.model, value: run.model_snapshot.model }]
      : []),
  ]

  return (
    <header style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 600,
          letterSpacing: '0.14em',
          textTransform: 'uppercase',
          color: 'var(--accent)',
        }}
      >
        {t.eyebrow}
      </span>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h1
          dir="auto"
          style={{
            margin: 0,
            fontSize: 31,
            fontWeight: 700,
            lineHeight: 1.22,
            letterSpacing: '-0.02em',
            color: 'var(--text-strong)',
          }}
        >
          {title}
        </h1>
        {subtitle && (
          <p
            dir="auto"
            style={{
              margin: 0,
              fontSize: 15,
              lineHeight: 1.6,
              color: 'var(--text-dim)',
            }}
          >
            {subtitle}
          </p>
        )}
      </div>

      {/* A definition list rather than a sentence: these are fields, a reader
          scans them, and on paper they are what a cover page carries. */}
      <dl
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '10px 34px',
          margin: 0,
          paddingTop: 14,
          borderTop: '2px solid var(--text-strong)',
        }}
      >
        {meta.map((field) => (
          <div
            key={field.label}
            style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}
          >
            <dt
              style={{
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'var(--text-faint)',
              }}
            >
              {field.label}
            </dt>
            <dd
              dir="auto"
              style={{ margin: 0, fontSize: 12.5, color: 'var(--text2)' }}
            >
              {field.value}
            </dd>
          </div>
        ))}
      </dl>
    </header>
  )
}

// ── the headline band ─────────────────────────────────────────────────────
/**
 * The figures the document opens on.
 *
 * Every one is a `plan_kpi` result — computed deterministically from result
 * rows by the same planner chat and dashboard tiles use, never written by a
 * model. That is what makes it safe to draw them largest: the numbers a reader
 * takes away without reading the prose are the numbers that cannot be wrong.
 *
 * A row of stat tiles rather than one hero figure. A hero is for a dashboard
 * with a single subject; a report has several, and promoting one of them to
 * hero would be an editorial claim the data did not make.
 */
function HeadlineFigures({
  figures, t,
}: {
  figures: { block: ReportBlockResult; heading: string }[]
  t: Record<string, string>
}) {
  if (figures.length === 0) return null

  return (
    <section
      aria-label={t.keyFigures}
      style={{ display: 'flex', flexDirection: 'column', gap: 9 }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          color: 'var(--text-faint)',
        }}
      >
        {t.keyFigures}
      </span>
      <div
        className="rm-report-figures"
        style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${Math.min(figures.length, 4)}, minmax(0, 1fr))`,
          gap: 10,
        }}
      >
        {figures.map(({ block, heading }) => (
          <div
            key={block.id}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              padding: '15px 12px',
              background: 'var(--panel)',
              border: '1px solid var(--border)',
              borderRadius: 12,
            }}
          >
            {block.kpi && <Kpi spec={block.kpi} compact />}
            <span
              dir="auto"
              style={{
                fontSize: 10.5,
                color: 'var(--text-faint)',
                textAlign: 'center',
                lineHeight: 1.4,
              }}
            >
              {heading}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

// ── the appendix ──────────────────────────────────────────────────────────
/**
 * Where every figure in the document came from.
 *
 * The thing a generated report is most often *disbelieved* over is not a wrong
 * number — it is an unattributable one, and "which query produced this?" has no
 * answer anywhere else in a printed document. So the appendix lists every
 * figure with its question, its row count, the moment it was computed and the
 * statement itself.
 *
 * Assembled entirely from rows the run already holds. Nothing here is generated,
 * nothing here can drift from the body, and it costs no tokens — which is what
 * lets it be exhaustive rather than a sample.
 */
function MethodNotes({
  sections, figures, run, t,
}: {
  sections: DocumentSection[]
  figures: Map<string, number>
  run: ReportRunDetail
  t: Record<string, string>
}) {
  const [open, setOpen] = useState(false)
  const blocks = sections.flatMap((section) => section.blocks)
  if (blocks.length === 0) return null

  return (
    <section
      className="rm-report-method"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        paddingTop: 20,
        borderTop: '1px solid var(--border)',
      }}
    >
      <button
        onClick={() => setOpen((current) => !current)}
        className="rm-report-method-toggle"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: 0,
          border: 'none',
          background: 'transparent',
          color: 'var(--text-strong)',
          fontSize: 14,
          fontWeight: 650,
          cursor: 'pointer',
          textAlign: 'start',
        }}
      >
        <Icon.Chevron size={13} open={open} />
        {t.method}
      </button>

      {/* Open on paper whatever it is on screen: a printed appendix nobody can
          click open is a blank heading. The class does that in `@media print`. */}
      <div className="rm-report-method-body" hidden={!open}>
        <p
          dir="auto"
          style={{
            margin: '0 0 14px',
            fontSize: 12.5,
            lineHeight: 1.7,
            color: 'var(--text-dim)',
          }}
        >
          {t.methodBody}
        </p>
        <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 12 }}>
          {blocks.map((block) => (
            <li
              key={block.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
                paddingTop: 10,
                borderTop: '1px solid var(--border)',
              }}
            >
              <span style={{ fontSize: 11, fontWeight: 650, color: 'var(--text2)' }}>
                {t.figure} {figures.get(block.id) ?? '—'}
              </span>
              <span dir="auto" style={{ fontSize: 12.5, color: 'var(--text)' }}>
                {block.question_snapshot}
              </span>
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                {block.row_count.toLocaleString()}{' '}
                {block.row_count === 1 ? t.row : t.rows}
                {block.truncated && ` · ${t.capped}`}
                {' · '}
                {t.computed} {new Date(block.computed_at).toLocaleString()}
              </span>
              {/* Said again here, in the one part of the document a reader
                  comparing two generations actually opens. */}
              {block.sql_changed === true && (
                <span style={{ fontSize: 11, color: 'var(--amber)' }}>
                  {t.queryChangedNote}
                </span>
              )}
              <pre
                dir="ltr"
                style={{
                  margin: '3px 0 0',
                  padding: '8px 10px',
                  background: 'var(--input-bg)',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  fontSize: 11,
                  lineHeight: 1.55,
                  color: 'var(--text2)',
                  overflowX: 'auto',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {block.sql_text}
              </pre>
            </li>
          ))}
        </ol>
      </div>

      <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.7, color: 'var(--text-faint)' }}>
        {t.asOf} {new Date(run.finished_at ?? run.created_at).toLocaleString()}.{' '}
        {t.asOfTail}
      </p>
    </section>
  )
}

// ── one section of the document ───────────────────────────────────────────
/**
 * A heading, its argument, and the evidence under it.
 *
 * The executive summary is the same component in a different key, and the
 * difference is editorial rather than cosmetic: it is the page a reader gets
 * *instead of* the rest, so it is set apart, numbered outside the sequence, and
 * its "- " lines are rendered as the findings they are rather than run together
 * as prose. Everything else is a numbered section — and the number is what turns
 * a scroll of charts into something a reader can cite and a colleague can be
 * pointed at.
 */
function SectionView({
  reportId, runId, section, figures, headline, t, busy, onRetry, onProse, onBlock,
}: {
  reportId: string
  runId: string
  section: DocumentSection
  figures: Map<string, number>
  headline: { block: ReportBlockResult; heading: string }[]
  t: Record<string, string>
  busy: boolean
  onRetry: () => void
  onProse: (text: string | null) => Promise<void>
  onBlock: (block: ReportBlockResult) => void
}) {
  const failed = section.prose?.status === 'FAILED'
  const summary = section.isSummary

  return (
    <section
      className="rm-report-section"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: summary ? 14 : 13,
        ...(summary
          ? {
              padding: '20px 22px',
              background: 'var(--panel)',
              border: '1px solid var(--border)',
              borderRadius: 14,
            }
          : {}),
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        {/* The number sits beside the heading rather than inside it, so a long
            Persian heading wraps under itself instead of around the digit. */}
        {!summary && section.number > 0 && (
          <span
            aria-hidden
            className="mono"
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: 'var(--accent)',
              fontVariantNumeric: 'tabular-nums',
              flexShrink: 0,
            }}
          >
            {String(section.number).padStart(2, '0')}
          </span>
        )}
        <h2
          dir="auto"
          style={{
            margin: 0,
            flex: 1,
            minWidth: 0,
            fontSize: summary ? 16 : 20,
            fontWeight: 700,
            letterSpacing: summary ? '0.02em' : '-0.015em',
            textTransform: summary ? 'uppercase' : undefined,
            color: summary ? 'var(--text-dim)' : 'var(--text-strong)',
          }}
        >
          {section.heading}
        </h2>
        {/* Retry is per section, and the rest of the document stays on screen
            while it runs — the run's status is *derived* from its parts, so a
            successful retry turns PARTIAL into SUCCEEDED with no state
            machine anywhere. */}
        {section.sectionId && (failed || section.prose === null) && (
          <GhostButton
            onClick={onRetry}
            disabled={busy}
            className="rm-report-hide-in-print"
            style={{ padding: '5px 10px', fontSize: 12, flexShrink: 0 }}
            title="Run this section's queries again and rewrite its paragraph."
          >
            <Icon.Refresh size={12} /> {t.retry}
          </GhostButton>
        )}
      </div>

      {!summary && (
        <div
          aria-hidden
          style={{ height: 2, background: 'var(--text-strong)', opacity: 0.85 }}
        />
      )}

      {section.prose === null ? (
        <ProseSkeleton />
      ) : (
        <ProseView
          result={section.prose}
          summary={summary}
          t={t}
          editable={section.sectionId !== null}
          onSave={onProse}
        />
      )}

      <HeadlineFigures figures={headline} t={t} />

      {section.blocks.map((block) => (
        <BlockView
          key={block.id}
          reportId={reportId}
          runId={runId}
          block={block}
          figure={figures.get(block.id)}
          t={t}
          onBlock={onBlock}
        />
      ))}
    </section>
  )
}

// ── the paragraph ─────────────────────────────────────────────────────────
/**
 * Prose, edited where it is read.
 *
 * The edit goes to `edited_prose`, a second column beside what the model wrote
 * — so reverting is free, and a regeneration writes a new run rather than
 * destroying this one's writing.
 */
function ProseView({
  result, summary, t, editable, onSave,
}: {
  result: ReportSectionResult
  /**
   * Whether to read the "- " lines as findings.
   *
   * Only the summary prompt asks for them, and only the summary gets them
   * rendered — a section that happened to open a sentence with a dash is prose
   * and stays prose.
   */
  summary: boolean
  t: Record<string, string>
  editable: boolean
  onSave: (text: string | null) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const text = proseOf(result)
  const findings = result.numeric_check?.findings ?? []

  async function commit(next: string | null) {
    setSaving(true)
    try {
      await onSave(next)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  if (result.status === 'FAILED') {
    return (
      <Note tone="red">
        {result.error_message || 'This section could not be written.'}
      </Note>
    )
  }

  if (editing) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <TextArea
          autoFocus
          rows={5}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          style={{ fontSize: 14.5, lineHeight: 1.7 }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <PrimaryButton
            onClick={() => void commit(draft.trim())}
            disabled={saving}
            style={{ padding: '6px 12px', fontSize: 12.5 }}
          >
            {saving ? t.saving : t.save}
          </PrimaryButton>
          <GhostButton
            onClick={() => setEditing(false)}
            style={{ padding: '6px 12px', fontSize: 12.5 }}
          >
            {t.cancel}
          </GhostButton>
          {/* Reverting is a column going back to NULL, not a second edit. */}
          {isEdited(result) && (
            <GhostButton
              onClick={() => void commit(null)}
              disabled={saving}
              style={{ padding: '6px 12px', fontSize: 12.5, marginInlineStart: 'auto' }}
              title="Go back to what the model wrote."
            >
              {t.revert}
            </GhostButton>
          )}
        </div>
      </div>
    )
  }

  const quiet = result.status === 'SKIPPED_NO_DATA'
  const parts = summary ? summaryParts(text) : { lead: text, findings: [] }

  return (
    <div className="rm-turn" style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
      {parts.lead && (
        <p
          dir="auto"
          style={{
            margin: 0,
            // The summary is the paragraph a reader is most likely to read and
            // least likely to finish, so it gets the larger setting.
            fontSize: summary ? 15.5 : 14.5,
            lineHeight: 1.8,
            color: quiet ? 'var(--text-dim)' : 'var(--text)',
            fontStyle: quiet ? 'italic' : undefined,
            whiteSpace: 'pre-wrap',
          }}
        >
          {parts.lead}
        </p>
      )}

      {/* The one piece of structure the model is asked to produce, rendered as
          structure. Run together as a paragraph it reads as a list someone
          forgot to format; set out, it is the part of an executive summary
          people actually take away. */}
      {parts.findings.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 8 }}>
          {parts.findings.map((finding, index) => (
            <li
              key={index}
              dir="auto"
              style={{
                display: 'flex',
                gap: 10,
                fontSize: 14,
                lineHeight: 1.7,
                color: 'var(--text)',
              }}
            >
              <span
                aria-hidden
                style={{
                  flexShrink: 0,
                  width: 5,
                  height: 5,
                  marginTop: 9,
                  borderRadius: 999,
                  background: 'var(--accent)',
                }}
              />
              <span style={{ minWidth: 0 }}>{finding}</span>
            </li>
          ))}
        </ul>
      )}

      <div
        className="rm-report-hide-in-print"
        style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
      >
        {editable && (
          <button
            className="rm-turn-actions"
            onClick={() => {
              setDraft(text)
              setEditing(true)
            }}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '3px 8px',
              borderRadius: 6,
              border: '1px solid var(--border)',
              background: 'transparent',
              color: 'var(--text-dim)',
              fontSize: 11.5,
              cursor: 'pointer',
            }}
          >
            <Icon.Pencil size={11} /> {t.edit}
          </button>
        )}
        {isEdited(result) && (
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{t.edited}</span>
        )}
        {findings.length > 0 && !dismissed && (
          <NumericMarker findings={findings} onDismiss={() => setDismissed(true)} />
        )}
      </div>
    </div>
  )
}

/**
 * Figures in the paragraph that no result row supports.
 *
 * **A finding is a suspicion, never a verdict** — the posture `pipeline/checks.py`
 * argues for at length. A percentage the model derived correctly from two
 * result values is an expected false positive, so this flags, never blocks, and
 * it can be waved away. What it must not be is a red banner over a paragraph
 * that is probably fine.
 */
function NumericMarker({
  findings, onDismiss,
}: {
  findings: NumericFinding[]
  onDismiss: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <button
        onClick={() => setOpen((current) => !current)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          padding: '3px 8px',
          borderRadius: 6,
          border: '1px solid var(--amber-border)',
          background: 'var(--amber-bg)',
          color: 'var(--amber)',
          fontSize: 11.5,
          cursor: 'pointer',
        }}
      >
        <Icon.Alert size={11} />
        {findings.length === 1
          ? '1 figure to check'
          : `${findings.length} figures to check`}
      </button>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        title="Dismiss"
        style={{
          display: 'inline-flex',
          padding: 3,
          borderRadius: 5,
          border: 'none',
          background: 'transparent',
          color: 'var(--text-faint)',
          cursor: 'pointer',
        }}
      >
        <Icon.Close size={11} />
      </button>
      {open && (
        <span style={{ fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.5 }}>
          {findings.map((finding) => finding.text).join(' · ')} — not found in this section’s
          results. A percentage or a difference worked out from two of them is expected here.
        </span>
      )}
    </span>
  )
}

// ── one block of the document ─────────────────────────────────────────────
/**
 * One figure: its number, its caption, the picture, and where it came from.
 *
 * The number is what makes it a figure rather than a chart on a page. A reader
 * can cite it, the appendix can list it, and a paragraph can be checked against
 * it — none of which is possible for an unlabelled picture, and all of which is
 * ordinary in a document produced by people.
 *
 * The source line under it says how many rows the statement returned and when,
 * for the same reason the SQL is one click away: a number nobody can trace back
 * to a statement is a number nobody should act on.
 */
function BlockView({
  reportId, runId, block, figure, t, onBlock,
}: {
  reportId: string
  runId: string
  block: ReportBlockResult
  /** Its place in the document's numbering; absent only if it arrived mid-merge. */
  figure: number | undefined
  t: Record<string, string>
  onBlock: (block: ReportBlockResult) => void
}) {
  const [showSql, setShowSql] = useState(false)
  const kind = renderKindOf(block)

  return (
    <figure
      className="rm-report-figure"
      style={{
        margin: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
        padding: 14,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
      }}
    >
      <figcaption style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {figure !== undefined && (
          <span
            className="mono"
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: '0.09em',
              textTransform: 'uppercase',
              color: 'var(--accent)',
            }}
          >
            {t.figure} {figure}
          </span>
        )}
        <span
          dir="auto"
          style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', lineHeight: 1.5 }}
        >
          {block.question_snapshot}
        </span>
      </figcaption>

      {kind === 'error' ? (
        <Note tone="red">
          {block.error_message || 'This question produced no result.'}
        </Note>
      ) : kind === 'kpi' && block.kpi ? (
        <Kpi spec={block.kpi} />
      ) : kind === 'chart' && block.vega_spec ? (
        <BlockChart
          reportId={reportId}
          runId={runId}
          block={block}
          t={t}
          onBlock={onBlock}
        />
      ) : (
        <>
          {block.chart_note && (
            <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{block.chart_note}</span>
          )}
          <ResultTable spec={block} previewRows={12} maxHeight={420} />
        </>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          paddingTop: 3,
          borderTop: '1px solid var(--border)',
        }}
      >
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)', paddingTop: 5 }}>
          {block.row_count.toLocaleString()} {block.row_count === 1 ? t.row : t.rows}
          {block.truncated && ` · ${t.capped}`}
          {' · '}
          {t.computed} {new Date(block.computed_at).toLocaleString()}
        </span>
        {/* The statement moved since the last generation, so this figure and
            the one under the same heading last quarter are not the same
            measurement. Said on the figure rather than in a banner, because it
            is true of *this* figure and rarely of all of them — and it prints,
            because a printed document is where the comparison actually gets
            made. */}
        {block.sql_changed === true && (
          <span
            title={t.queryChangedNote}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              marginTop: 4,
              padding: '2px 7px',
              borderRadius: 5,
              border: '1px solid var(--amber-border)',
              background: 'var(--amber-bg)',
              color: 'var(--amber)',
              fontSize: 10.5,
            }}
          >
            <Icon.Alert size={10} />
            {t.queryChanged}
          </span>
        )}
        {/* The SQL is shown and auditable here for the same reason it is in
            chat: a number nobody can trace back to a statement is a number
            nobody should act on. Hidden on paper — the appendix carries every
            statement in full, so printing them twice is noise. */}
        <button
          onClick={() => setShowSql((open) => !open)}
          className="rm-report-hide-in-print"
          style={{
            marginTop: 4,
            padding: '2px 7px',
            borderRadius: 5,
            border: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-faint)',
            fontSize: 11,
            cursor: 'pointer',
          }}
        >
          {showSql ? t.hideQuery : t.query}
        </button>
        {showSql && <CopyButton text={block.sql_text} label="Copy" />}
      </div>

      {showSql && (
        <pre
          dir="ltr"
          className="rm-report-hide-in-print"
          style={{
            margin: 0,
            padding: '9px 11px',
            background: 'var(--input-bg)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            fontSize: 11.5,
            lineHeight: 1.6,
            color: 'var(--text2)',
            overflowX: 'auto',
            whiteSpace: 'pre',
          }}
        >
          {block.sql_text}
        </pre>
      )}
    </figure>
  )
}

/**
 * A block's chart, and changing it.
 *
 * Closed by default: the planner picked this type from the data and is usually
 * right, so nine tiles under every figure would be chrome. What it does not do
 * is offer a type this result cannot carry — the backend answers "can this be a
 * heatmap, and if not why" for every type at once and the grid greys the rest
 * with the reason on hover. Offer-then-demote becomes cannot-offer.
 *
 * Unlike chat's picker, **this one saves**: a report is printed from its saved
 * run, so a chart that lived only in the browser would be lost on the way to
 * the PDF. It saves onto the run and not the template, which is the same rule
 * `edited_prose` follows.
 */
function BlockChart({
  reportId, runId, block, t, onBlock,
}: {
  reportId: string
  runId: string
  block: ReportBlockResult
  t: Record<string, string>
  onBlock: (block: ReportBlockResult) => void
}) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<ChartOption[]>([])
  const [busy, setBusy] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)

  const type = chartTypeOf(block.vega_spec)

  async function choose(next: string) {
    setBusy(true)
    setRefused(null)
    try {
      const answer = await api.redrawBlockChart(reportId, runId, block.id, next)
      setOptions(answer.options)
      if (answer.spec) {
        onBlock({
          ...block,
          vega_spec: answer.spec,
          chart_source: answer.chart_source,
          chart_note: answer.chart_note,
        })
      } else {
        // Only reachable if the verdicts on screen are older than the data.
        setRefused(answer.reason)
      }
    } catch (err) {
      setRefused(err instanceof Error ? err.message : 'The chart could not be redrawn.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {block.vega_spec && <VegaChart spec={block.vega_spec} />}
      {block.chart_note && (
        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{block.chart_note}</span>
      )}
      <div
        className="rm-report-hide-in-print"
        style={{ display: 'flex', alignItems: 'center', gap: 8 }}
      >
        <button
          type="button"
          onClick={() => {
            const next = !open
            setOpen(next)
            // Opening asks for the verdicts once, redrawing what is already on
            // screen — so there is no separate "what fits this result" call.
            if (next && options.length === 0 && type) void choose(type)
          }}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            padding: '3px 8px',
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'transparent',
            color: 'var(--text-dim)',
            fontSize: 11.5,
            cursor: 'pointer',
          }}
        >
          <ChartGlyph type={type} size={13} />
          {open ? t.done : t.changeChart}
        </button>
        {busy && <Spinner size={12} />}
        {refused && (
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{refused}</span>
        )}
      </div>
      {open && (
        <div className="rm-report-hide-in-print" style={{ maxWidth: 560 }}>
          <ChartTypePicker
            value={type}
            options={options}
            columns={9}
            onChange={(next) => void choose(next)}
          />
        </div>
      )}
    </div>
  )
}

// ── the shapes of what is coming ──────────────────────────────────────────
function ProseSkeleton() {
  return (
    <div aria-hidden style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      <div className="rm-bone" style={{ width: '96%', height: 10, borderRadius: 6 }} />
      <div className="rm-bone" style={{ width: '88%', height: 10, borderRadius: 6 }} />
      <div className="rm-bone" style={{ width: '54%', height: 10, borderRadius: 6 }} />
    </div>
  )
}

function ViewerSkeleton() {
  return (
    <div aria-hidden style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
      {[0, 1].map((index) => (
        <div key={index} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="rm-bone" style={{ width: '38%', height: 17, borderRadius: 6 }} />
          <ProseSkeleton />
          <div className="rm-bone" style={{ width: '100%', height: 180, borderRadius: 12 }} />
        </div>
      ))}
    </div>
  )
}

/** The back arrow, identical in both halves of the feature. */
const backButton: React.CSSProperties = {
  display: 'flex',
  width: 30,
  height: 30,
  flexShrink: 0,
  alignItems: 'center',
  justifyContent: 'center',
  borderRadius: 8,
  border: 'none',
  background: 'transparent',
  color: 'var(--text-dim)',
  cursor: 'pointer',
  ['--rm-hover-bg' as string]: 'var(--panel-alt)',
}
