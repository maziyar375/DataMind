/**
 * The outline editor — the structure a report is generated from.
 *
 * This is the screen the whole feature turns on. A report is not a question
 * answered once; it is a **document whose structure a human approved**, and
 * this is where that approval happens (docs/history/reports-plan.md §2). The model
 * proposes headings and questions, the user rewrites them, and only then is a
 * single model call spent per section on prose.
 *
 * Three things shape the layout:
 *
 *  - **The question is the unit, not the SQL.** A block is one plain-language
 *    question that becomes one query and one chart, and in v1 that question is
 *    what the user edits. Editing it always drops the *verdict* — the stored
 *    statement answered the previous question — so the chip returns to "Not
 *    checked" on its own rather than the page pretending otherwise. What
 *    happens to the statement itself depends on who wrote it: a model draft is
 *    dropped with the verdict, a hand-written one is kept, and the row says
 *    which, because a kept statement is one the run will still execute.
 *  - **Feasibility is mechanical, and it is the guard's answer, not ours.**
 *    `POST .../check` gives back `FEASIBLE | EMPTY | INFEASIBLE` and, when it
 *    refuses, the guard's own sentence. That sentence is rendered verbatim: a
 *    re-worded rejection is one the user cannot act on, which is the rule
 *    `semantic.tsx` already follows for metric expressions.
 *  - **"Check all" is a pool, not a job.** One check is five to ten seconds of
 *    guard work on one heading, and the headings are independent — nothing one
 *    check learns is of use to the next. So the page runs `CHECK_CONCURRENCY`
 *    of them at a time, in the page with per-block progress, which reads better
 *    than a job with a progress bar and needs no extra table — the reason
 *    `api/v1/reports.py` makes the route synchronous and per block. It can be
 *    stopped between waves, because a user who sees the first four answers is
 *    often done reading. It walks only the blocks a check would *fill*:
 *    running it over a block whose SQL somebody wrote by hand would replace
 *    that SQL, and a bulk button must not be the thing that does that.
 *  - **Generate says what it is about to produce.** The last click is the
 *    expensive one — minutes of worker time and a model call per section — and
 *    it used to be a button that either ran or sat greyed out with its reason
 *    in a `title`. It is now never disabled: a clean outline generates on the
 *    first click, and an outline with holes in it opens `GeneratePreflight`,
 *    which names each hole as the thing it becomes in the finished document and
 *    offers the one action that fixes most of them. `report-readiness.ts` is
 *    where that judgement lives, tested, because guessing it wrong costs a run.
 *
 * Every free-text field is `dir="auto"`: a report is written in Persian as
 * often as English, and the two mix inside one heading.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { access, isReportRunInFlight, llmConfigs as modelsApi, reports as api } from '../api/client'
import {
  DEFAULT_SECTIONS, MAX_SECTIONS, MIN_SECTIONS, type LlmConfig, type Report,
  type ReportBlock, type ReportBlockCheck, type ReportBlockType,
  type ReportFeasibility, type ReportRun, type ReportSection,
  type ReportTimeWindow,
} from '../api/types'
import { AccessPopover, ReachBadge, TransferControl, type WarnFn } from './access'
import { accessOf } from '../permissions'
import {
  generationBlockedBy, preflightOf, readinessOf, type Preflight,
  type PreflightProblem, type ReadinessState,
} from './report-readiness'
import {
  Chip, CopyButton, DangerButton, EmptyState, ErrorNote, GhostButton, Icon,
  InlineEdit, Modal, NumberStepper, PrimaryButton, ProgressBar, Spinner,
  TextArea, relativeTime, type ChipTone,
} from './ui'
import { Note, backButton, headerStyle, toolbarBtn } from './report-parts'

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
 * How many questions the "Check all" sweep has in flight at once.
 *
 * Each one is an independent `POST .../check` — a model writing a statement
 * for one question, five to ten seconds, learning nothing the next one needs —
 * so the only reason to run them one after another is the cost of running them
 * together. Four is that cost: it matches `MAX_CONCURRENT_TILES` on the
 * backend, and it is well inside a pool of `db_pool_size + db_max_overflow`
 * sessions with room for the rest of the app.
 */
const CHECK_CONCURRENCY = 4

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
  // How many sections the *next* proposal asks for. Held here rather than
  // written on every nudge: a number the user tried and then thought better of
  // should leave nothing behind, and the report is only told when they commit
  // to spending the call. Seeded from the row, so it is the number that was
  // asked for last time rather than a default that forgets.
  const [sectionTarget, setSectionTarget] = useState(DEFAULT_SECTIONS)
  const [checking, setChecking] = useState<string[]>([])
  const [sweep, setSweep] = useState<
    { done: number; total: number; label: string; alongside: number } | null
  >(null)
  const [previews, setPreviews] = useState<Record<string, string>>({})
  const [latestRun, setLatestRun] = useState<ReportRun | null>(null)
  // How many documents this report has produced. The header shows it because
  // "History 7" is an invitation and "History" is a menu item.
  const [runCount, setRunCount] = useState(0)
  const [starting, setStarting] = useState(false)
  // What Generate found wrong, while the dialog about it is open. Held rather
  // than recomputed on render: the dialog has to keep describing the outline
  // the user pressed the button on, even as the sweep it started changes it.
  const [preflight, setPreflight] = useState<Preflight | null>(null)
  /**
   * Questions that *were* checked and are not any more, because an edit
   * invalidated them.
   *
   * The API cannot tell these apart from a question nobody has ever checked —
   * both are `UNCHECKED` — but the user can, and the difference is the whole
   * point: one is work not started, the other is work undone by something they
   * just did. Ids only, and they live and die with the page.
   */
  const [invalidated, setInvalidated] = useState<Set<string>>(() => new Set())
  const stopSweep = useRef(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        // The run list is rows only — no result of any run is read here. It is
        // what lets a report that has already been generated offer its document
        // instead of stranding the reader in the editor.
        const [loaded, configs, history] = await Promise.all([
          api.get(reportId), modelsApi.list('chat'), api.runs(reportId),
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

  // Follows the row, so a proposal made at four leaves the panel showing four
  // the next time it is opened rather than the default it started at.
  const stored = report?.section_target
  useEffect(() => {
    if (stored !== undefined) setSectionTarget(stored)
  }, [stored])

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

  /**
   * The outline as it is *now*, readable from a function that has been awaiting.
   *
   * The sweep runs for a minute and lands a row at a time; the code that
   * decides what to do when it finishes closed over `report` before any of them
   * arrived. Reading state through a ref is the honest fix — the alternative is
   * threading every answer back up through the loop's return value, which is
   * the same information travelling twice.
   */
  const latest = useRef(report)
  useEffect(() => {
    latest.current = report
  }, [report])

  /**
   * Every block write this page has made, in order, so a reader can wait for
   * them.
   *
   * There is one sequence where that matters and it is the one everybody
   * performs: reword a question, then reach straight for Generate. `InlineEdit`
   * commits on blur and blur lands *before* the click, so at the moment the
   * button's handler runs, the PATCH that is about to reset that question's
   * verdict is in flight and nothing on the page knows it yet. Without this the
   * preflight would describe the outline of a second ago, call it clean, and
   * spend the run — producing exactly the hole it exists to prevent.
   *
   * Both callbacks are `run`, so one failed write does not stall the chain
   * behind it.
   */
  const writes = useRef<Promise<unknown>>(Promise.resolve())
  const sequence = useCallback(<T,>(run: () => Promise<T>): Promise<T> => {
    const next = writes.current.then(run, run)
    writes.current = next.then(
      () => undefined,
      () => undefined,
    )
    return next
  }, [])

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
    setPreflight(null)
    setStarting(true)
    const run = await guard(() => api.startRun(reportId))
    setStarting(false)
    if (run) onOpenRun(run.id)
  }

  /**
   * The last click, and the only one that costs minutes.
   *
   * A clean outline generates on the first press: a confirmation over work
   * there is nothing to say about is the kind of ceremony that teaches people
   * to click through dialogs without reading them. An outline with holes in it
   * opens one, because the alternative — the button greying itself out with the
   * reason in a `title` — leaves a user who is on a touchscreen, or reading
   * with a keyboard, looking at a control that will not say what is wrong.
   */
  const arming = useRef(false)
  async function attemptGenerate() {
    if (arming.current) return
    arming.current = true
    try {
      // The edit that was committed on the way to this button — see `sequence`.
      await writes.current
      // And one turn more, because `latest` follows `report` from an effect and
      // React runs effects after the commit that write just scheduled.
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0))
      const found = preflightOf(latest.current?.sections ?? [])
      if (found.clean) return void generate()
      setPreflight(found)
    } finally {
      arming.current = false
    }
  }

  /**
   * Check what a sweep can check, and then generate if that was all of it.
   *
   * The dialog's own button, and the reason it is the primary one: "eleven
   * questions are ready and one was never checked" is not a decision anybody
   * wants to make, it is a chore they want done. If the sweep uncovers
   * something new — a question the guard refuses — the dialog comes back
   * describing that instead of generating over it.
   */
  async function checkThenGenerate(pending: ReportBlock[]) {
    setPreflight(null)
    await checkAll(pending)
    // Read after the sweep, not before: every verdict landed while this was
    // awaiting, and `latest` is how they are seen.
    const after = preflightOf(latest.current?.sections ?? [])
    if (after.clean) return void generate()
    setPreflight(after)
  }

  /**
   * Propose a structure, then check it where the user can watch.
   *
   * Two model calls of very different shapes: one for the whole outline, then
   * one per question. Running them as two separate button presses meant the
   * second one started against a page of `UNCHECKED` rows the user had already
   * read and had no reason to expect anything more from — so the sweep looked
   * like a thing the tool made you do rather than a thing it was doing.
   *
   * Chained, the order carries the meaning: the questions land first and stay
   * on screen, and the verdicts arrive under them a wave at a time. Nothing
   * here is hidden behind a spinner that could be showing the questions
   * instead.
   *
   * The sweep is interruptible (`Stop`), which is what makes it acceptable to
   * start it without asking: it is one model call per question, and a user who
   * wants to rewrite the outline before spending that can say so mid-way.
   */
  /**
   * @param count how many sections to ask for, as the panel beside the button
   * has it. Written through first when it differs, because `POST /outline`
   * reads the stored number — the alternative is a route that takes the shape
   * of the document as a query parameter, and then two places decide it.
   */
  async function propose(count: number) {
    setConfirmPropose(false)
    setProposing(true)
    const next = await guard(async () => {
      if (count !== report?.section_target) {
        await api.update(reportId, { section_target: count })
      }
      return api.proposeOutline(reportId)
    })
    // Cleared *before* the sweep starts, so the outline is on screen for the
    // render that shows the first question being checked rather than one
    // render later.
    setProposing(false)
    if (!next) return
    setReport(next)
    setPreviews({})
    // Nothing on the page came from the outline that just went away.
    setInvalidated(new Set())

    // Read off the response rather than the `now` memo: that memo is derived
    // from state this function has only just set, and would still be describing
    // the outline this one replaced.
    const pending = preflightOf(next.sections).sweepable
    if (pending.length === 0) return

    // A beat before the sweep, for two reasons that happen to want the same
    // pause. React has not committed these rows yet, so the sweep's first
    // `scrollIntoView` would search a DOM that has no questions in it. And an
    // outline that appears already mid-check reads as one event; a moment of
    // just-the-questions is what makes the checking legible as the second
    // thing that happens to them.
    await new Promise<void>((resolve) => window.setTimeout(resolve, 400))
    await checkAll(pending)
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

  // Sequenced, because Generate waits on this queue. See `sequence`.
  function patchBlock(blockId: string, payload: Record<string, unknown>) {
    return sequence(async () => {
      const before = latest.current?.sections
        .flatMap((section) => section.blocks)
        .find((block) => block.id === blockId)
      const next = await guard(() => api.updateBlock(reportId, blockId, payload))
      if (!next) return
      putBlock(next)
      // The API resets the verdict when a change makes the stored statement
      // answer the previous question, and it does that quietly — the row simply
      // says "Not checked" again. Remembering that *this* row was checked a
      // second ago is what lets it say something more useful than that.
      if (
        before
        && before.feasibility_status !== 'UNCHECKED'
        && next.feasibility_status === 'UNCHECKED'
      ) {
        setInvalidated((current) => new Set(current).add(blockId))
      }
    })
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
          // Whatever it says, this row has now been looked at since its last
          // edit, so it stops being one of the ones an edit left behind.
          setInvalidated((current) => {
            if (!current.has(blockId)) return current
            const next = new Set(current)
            next.delete(blockId)
            return next
          })
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

  /**
   * Every block a check would *fill*, `CHECK_CONCURRENCY` at a time.
   *
   * The caller passes `preflight.sweepable`, never "everything unchecked": a
   * block whose SQL somebody wrote by hand is unchecked again the moment its
   * question is reworded, and checking it asks the model for a new statement
   * over the top of theirs. One button that quietly does that to four blocks is
   * the kind of thing a person never forgives a tool for.
   *
   * **The blocks are independent, so the sweep is a pool and not a queue.**
   * One check is five to ten seconds of one model writing one statement about
   * one question; nothing it learns is of any use to the next one. Twelve of
   * them end to end is two minutes of watching a spinner move down a page —
   * the same wall clock the report's own narration used to spend, and for the
   * same reason. In waves of four it is half a minute, and every row in flight
   * carries its own spinner, so the page shows more of itself working rather
   * than less.
   *
   * What the pool must not lose is the two things the walk was for:
   *
   *  - **It can be stopped**, because a user who has seen the first four
   *    answers is often done reading. `Stop` lands between waves — the four in
   *    flight are model calls already paid for, and abandoning them buys
   *    nothing back.
   *  - **You can watch it.** The page follows the wave rather than each block,
   *    and the progress line names the question it scrolled to and counts the
   *    rest.
   */
  async function checkAll(pending: ReportBlock[]) {
    stopSweep.current = false
    let done = 0
    for (let at = 0; at < pending.length; at += CHECK_CONCURRENCY) {
      if (stopSweep.current) break
      const wave = pending.slice(at, at + CHECK_CONCURRENCY)
      setSweep({
        done,
        total: pending.length,
        label: wave[0].question,
        alongside: wave.length - 1,
      })
      // Follow the sweep down the page, to the head of the wave. `nearest`
      // scrolls the least it can and does nothing at all when the row is
      // already visible, so a user reading question three is not dragged to
      // question four — a walk you cannot watch is the thing this whole chain
      // exists to fix, but so is a page that moves under you.
      window.document
        .querySelector(`[data-block-id="${wave[0].id}"]`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })

      const answers = await Promise.all(
        wave.map(async (block) => {
          const answer = await check(block.id)
          // Counted as it lands rather than at the end of the wave: the bar is
          // the only thing on screen that says the pool is four wide.
          done += 1
          setSweep((current) => (current ? { ...current, done } : current))
          return answer
        }),
      )
      // A failed *request* — not a refusal, which arrives as a verdict — means
      // the next nine will fail the same way. Stop and say so once.
      if (answers.some((answer) => !answer)) break
    }
    setSweep(null)
  }

  const sections = report?.sections ?? []
  const state = useMemo(() => readinessOf(sections), [sections])
  // The live judgement, for the panel and the header. The dialog reads the
  // frozen copy in `preflight` instead — see the note on that state.
  const now = useMemo(() => preflightOf(sections), [sections])

  if (!report) {
    return <EditorSkeleton onBack={onBack} error={error} />
  }

  // Somebody this report was shared with **for viewing** reads it; they do not
  // get an editor whose every field, button and check answers 403.
  if (!accessOf(report.privileges).edit) {
    return (
      <ReportReadOnly
        report={report}
        latestRun={latestRun}
        runCount={runCount}
        onBack={onBack}
        onOpenRun={onOpenRun}
        onHistory={onHistory}
      />
    )
  }

  const busy = proposing || sweep !== null
  // Not "busy" — unfixable. Everything that writes to this report needs the
  // database it was built against, and that database is gone; only reading is
  // still possible. See `generationBlockedBy`.
  const blocked = generationBlockedBy(report)

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
          {/* Who else can reach this document, and the way to change it. It
              draws nothing unless the viewer holds `manage` — the same answer
              the API will give the next request — so somebody who was shared
              this report sees a header without a share control rather than
              one whose button 403s. */}
          <ReachBadge privileges={report.privileges} owner={report.owner_name} />
          <AccessPopover
            base={`reports/${report.id}`}
            warn={reportWarn(report.id)}
            resourceLabel={report.name}
            // Sized to the ghost buttons it stands beside, which all carry
            // `toolbarBtn`. It was the one control in this row at 13px.
            buttonStyle={toolbarBtn}
            extraActions={
              <TransferControl
                base={`reports/${report.id}`}
                title={report.name}
                // See the dashboard header: the previous owner keeps nothing,
                // so the only honest next screen is the index.
                onTransferred={onBack}
              />
            }
          />
          {sections.length > 0 && (
            <GhostButton
              onClick={() => setConfirmPropose(true)}
              disabled={busy || blocked !== null}
              style={toolbarBtn}
              title={
                blocked ??
                'Ask the model for a fresh structure. This replaces the outline below.'
              }
            >
              <Icon.Sparkle size={13} /> Propose again
            </GhostButton>
          )}
          {now.sweepable.length > 0 && (
            <GhostButton
              onClick={() => void checkAll(now.sweepable)}
              disabled={busy || blocked !== null}
              style={toolbarBtn}
              title={blocked ?? undefined}
            >
              <Icon.Check size={13} />
              {`Check ${now.sweepable.length} question${now.sweepable.length === 1 ? '' : 's'}`}
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
          {/* Never disabled for the state of the *outline* — only for work
              already in flight. A greyed-out control with its reason in a
              `title` is unreadable on a touchscreen, unreachable from a
              keyboard, and is how a user concludes the product is broken; the
              dialog this opens says the same thing where it can be read, and
              offers to fix it.

              A deleted database is the one exception, and it is the exception
              because that rationale inverts: there is no fix to offer and no
              dialog worth opening, so a live button could only produce a 422
              the user did not ask for. The reason is already on the page as an
              amber note below — the readable half of the rule is kept, and only
              the "never disabled" half gives way. */}
          <PrimaryButton
            onClick={() => void attemptGenerate()}
            disabled={busy || starting || blocked !== null}
            title={
              blocked ??
              (now.clean
                ? 'Generate this report from the outline below.'
                : 'Check what this will produce before it runs.')
            }
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

          {/* The readable half of the disabled-control rule: every greyed
              button in this page has its reason here, in one sentence, in the
              flow of the document rather than behind a hover. */}
          {blocked && <Note tone="amber">{blocked}</Note>}

          {/* The status panel leads, because it is the answer to the question a
              user opens this page with — how far along is this, and what is the
              next thing to do. It stays up through the sweep, where its counts
              are the *most* useful they ever are: they move, one verdict at a
              time, and the minute the sweep takes is the minute a user most
              wants to know how much of it is left. Hidden only while a proposal
              is in flight, because then it describes an outline that is about
              to be replaced. */}
          {!proposing && (
            <OutlineStatus
              report={report}
              state={state}
              preflight={now}
              sections={sections}
            />
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
                      {sweep.alongside > 0 && ` and ${sweep.alongside} more`}
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
                  <ProposePanel
                    sections={sectionTarget}
                    onSections={setSectionTarget}
                    model={models.find((m) => m.id === report.llm_config_id) ?? null}
                    disabled={!report.prompt.trim() || blocked !== null}
                    onPropose={() => void propose(sectionTarget)}
                    onAddSection={() => void addSection()}
                  />
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
                blocked={blocked}
                checking={checking}
                invalidated={invalidated}
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
          subtitle="One model call for the structure, then one per question to check it — stoppable at any point."
          onClose={() => setConfirmPropose(false)}
          footer={
            <>
              <GhostButton onClick={() => setConfirmPropose(false)}>Cancel</GhostButton>
              <PrimaryButton onClick={() => void propose(sectionTarget)}>
                Replace the outline
              </PrimaryButton>
            </>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text2)' }}>
              This <strong>replaces</strong> every section and question below, including anything
              you have written or checked. Past runs are untouched — they keep their own copy of
              the structure they were generated from.
            </p>

            {/* The one thing worth changing at this exact moment. A structure
                being replaced is precisely when its length is up for
                reconsideration — the last one is on screen to judge it by. */}
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '10px 12px',
                background: 'var(--panel-alt)',
                borderRadius: 10,
                fontSize: 12.5,
                color: 'var(--text2)',
              }}
            >
              <span>Sections to ask for</span>
              <NumberStepper
                ariaLabel="Sections to ask for"
                value={sectionTarget}
                onChange={setSectionTarget}
                min={MIN_SECTIONS}
                max={MAX_SECTIONS}
              />
              <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--text-faint)' }}>
                plus the executive summary
              </span>
            </label>
          </div>
        </Modal>
      )}

      {preflight && (
        <GeneratePreflight
          preflight={preflight}
          sections={sections.length}
          model={models.find((m) => m.id === report.llm_config_id)?.name ?? null}
          onClose={() => setPreflight(null)}
          onCheck={() => void checkThenGenerate(preflight.sweepable)}
          onGenerate={() => void generate()}
        />
      )}
    </div>
  )
}

// ── the last click ────────────────────────────────────────────────────────
/**
 * What generating right now would produce, said before it is spent.
 *
 * A generation is minutes of worker time and a model call per section, and
 * everything wrong with an outline is *legible before it starts* — which is
 * what made the old arrangement hard to defend: the button either ran, or sat
 * greyed out with its objection in a `title` attribute, and the states in
 * between produced a finished document with an error message where a chart
 * should be.
 *
 * Three rules hold this together:
 *
 *  - **A clean outline never sees this.** Confirming work there is nothing to
 *    say about is how people learn to click past dialogs without reading them.
 *  - **Every line names a consequence, not a state.** "3 questions have never
 *    been checked" is a fact about the editor; "they arrive in the document as
 *    an error message where their figure should be" is the reason to care.
 *  - **The primary action is the fix, not the acceptance.** Where a sweep can
 *    resolve what is wrong, that is the default button and it generates when it
 *    is done. Generating anyway stays available — a document with one known
 *    hole is often exactly what somebody wants at five o'clock — but it is
 *    never what the dialog nudges towards.
 */
function GeneratePreflight({
  preflight, sections, model, onClose, onCheck, onGenerate,
}: {
  preflight: Preflight
  sections: number
  model: string | null
  onClose: () => void
  onCheck: () => void
  onGenerate: () => void
}) {
  const { problems, sweepable, canGenerate } = preflight
  const fixable = sweepable.length

  return (
    <Modal
      title="Generate this report?"
      subtitle={
        canGenerate
          ? `${sections} section${sections === 1 ? '' : 's'} written`
            + `${model ? ` by ${model}` : ''}, one model call each — a few minutes.`
          : 'Nothing here can be run yet.'
      }
      width={560}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          {/* Offered whenever the API would accept the run, and worded as the
              choice it is. Secondary when there is a fix to press instead. */}
          {canGenerate
            && (fixable > 0 ? (
              <GhostButton onClick={onGenerate}>Generate anyway</GhostButton>
            ) : (
              <PrimaryButton onClick={onGenerate}>Generate anyway</PrimaryButton>
            ))}
          {fixable > 0 && (
            <PrimaryButton onClick={onCheck}>
              <Icon.Check size={12} />
              {`Check ${fixable} and generate`}
            </PrimaryButton>
          )}
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {problems.map((problem) => (
          <PreflightRow key={problem.kind} problem={problem} />
        ))}

        {fixable > 0 && (
          <p style={{ margin: '2px 0 0', fontSize: 11.5, lineHeight: 1.55, color: 'var(--text-faint)' }}>
            Checking is one model call per question and can be stopped. Questions whose SQL you
            wrote yourself are never checked in bulk — that would replace it.
          </p>
        )}
      </div>
    </Modal>
  )
}

/** One thing that will be wrong with the document, and what it will look like. */
function PreflightRow({ problem }: { problem: PreflightProblem }) {
  const red = problem.tone === 'red'
  return (
    <div
      style={{
        display: 'flex',
        gap: 9,
        padding: '10px 12px',
        borderRadius: 10,
        background: red ? 'var(--red-bg)' : 'var(--amber-bg)',
        border: `1px solid ${red ? 'var(--red-border)' : 'var(--amber-border)'}`,
      }}
    >
      <span
        aria-hidden
        style={{ display: 'flex', paddingTop: 1, flexShrink: 0, color: red ? 'var(--red)' : 'var(--amber)' }}
      >
        <Icon.Alert size={13} />
      </span>
      <span style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
        <strong style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--text-strong)' }}>
          {problem.title}
        </strong>
        <span style={{ fontSize: 12, lineHeight: 1.55, color: 'var(--text2)' }}>
          {problem.detail}
        </span>
        {/* The guard's own sentence, exactly as it wrote it — the same rule the
            block row follows, and for the same reason. */}
        {problem.hint && (
          <span
            dir="auto"
            style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--text-dim)', fontStyle: 'italic' }}
          >
            “{problem.hint}”
          </span>
        )}
      </span>
    </div>
  )
}

// ── asking for a structure ────────────────────────────────────────────────
/**
 * The two knobs that govern the model call, beside the button that spends it.
 *
 * The count used to be in the create dialog, which asked a user to choose the
 * shape of a document before they had seen a single heading of it, and then
 * stranded the number a screen away from the button it governs. Here the question is
 * asked at the moment it is answerable — the schema has been read, the request
 * is on screen above, and the next thing that happens is the call.
 *
 * The model is *named*, not chosen, because it is a property of the report
 * rather than of this call: it also writes the prose and checks the questions,
 * so it lives one card up with the request. Named here anyway, because this is
 * where it starts costing money.
 */
function ProposePanel({
  sections, onSections, model, disabled, onPropose, onAddSection,
}: {
  sections: number
  onSections: (count: number) => void
  model: LlmConfig | null
  disabled: boolean
  onPropose: () => void
  onAddSection: () => void
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 11 }}>
      <label
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 9,
          padding: '8px 11px 8px 13px',
          background: 'var(--panel-alt)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          fontSize: 12.5,
          color: 'var(--text2)',
        }}
      >
        <span>Sections to ask for</span>
        <NumberStepper
          ariaLabel="Sections to ask for"
          value={sections}
          onChange={onSections}
          min={MIN_SECTIONS}
          max={MAX_SECTIONS}
        />
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          plus the executive summary
        </span>
      </label>

      <div style={{ display: 'flex', gap: 8 }}>
        <PrimaryButton onClick={onPropose} disabled={disabled}>
          <Icon.Sparkle size={14} /> Propose an outline
        </PrimaryButton>
        <GhostButton onClick={onAddSection}>Add a section</GhostButton>
      </div>

      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        {model
          ? `Written by ${model.name} — changeable above.`
          : 'No model chosen yet — pick one above before proposing.'}
      </span>
    </div>
  )
}

// ── the request ───────────────────────────────────────────────────────────
/**
 * What the report is for, and who writes it.
 *
 * The request is kept verbatim and is what the outline is proposed *from*, so
 * it is editable here rather than frozen at creation: rewriting it and
 * proposing again is the loop a user actually runs. The model sits beside it
 * because it is a property of the report — it writes the prose and checks the
 * questions too, not only the one call that proposes a structure. **How many**
 * sections to ask for is not here for exactly that reason: it governs one call
 * and nothing else, so it lives with the button that makes it.
 *
 * The connection is not editable and says so: a report keyed to two
 * connections would cross disclosure policies. The language is not editable
 * either, but it is not *pinned* — it follows the request, so the way to
 * change it is to write the request in the other language.
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
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          title="A report is pinned to the connection it was created against."
        >
          <Icon.Lock size={11} /> fixed
        </span>
        <span aria-hidden style={{ opacity: 0.4 }}>·</span>
        {/* Derived, not chosen: written wherever the request above is written.
            Shown because it decides the document's direction, and a user whose
            Persian request came back marked English needs to see that here
            rather than at the bottom of a generated document. */}
        <span title="Taken from the request above — write it in the other language to change it.">
          {report.language === 'fa' ? 'فارسی' : 'English'}
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
            {/* The report's own model, when it was not shared with this
                editor: named, so the picker does not silently show another,
                and marked, because generating with it will be refused. */}
            {report.llm_config_id !== null
              && !models.some((model) => model.id === report.llm_config_id) && (
                <option value={report.llm_config_id} disabled>
                  {report.llm_config_name ?? 'Its model'} — not shared with you
                </option>
              )}
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
// `readinessOf` and `preflightOf` moved to `report-readiness.ts`: they are the
// two pure judgements on this page, one of them decides whether a generation is
// worth starting, and both are worth a test suite that no bundler has to run.

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
  report, state, preflight, sections,
}: {
  report: Report
  state: ReadinessState
  /** What generating now would produce — the same judgement the button makes. */
  preflight: Preflight
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
      // The button's own answer, not a second one derived differently: "ready"
      // here has to mean the click that follows will not open a dialog.
      caption: preflight.clean
        ? 'ready to run'
        : preflight.canGenerate
          ? `${preflight.problems.length} thing${preflight.problems.length === 1 ? '' : 's'} to know`
          : 'nothing to run yet',
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
            {advice(preflight)}
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * The one sentence that says what to do next, and why it matters.
 *
 * The most severe thing the preflight found, in the words the preflight dialog
 * will use for it if the user presses Generate anyway. Deliberately the same
 * sentence in both places: a panel that describes the outline one way and a
 * dialog that describes it another reads as two opinions rather than one.
 */
function advice(preflight: Preflight): string {
  const first = preflight.problems[0]
  if (!first) {
    return 'Every question has a validated query. Generating writes a new document and '
      + 'leaves earlier ones untouched.'
  }
  return `${first.title} ${first.detail}`
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
  section, index, count, busy, blocked, checking, invalidated, previews,
  onHeading, onIntent, onRemove, onMove,
  onAddBlock, onBlock, onRemoveBlock, onMoveBlock, onCheck, onSaveSql,
}: {
  section: ReportSection
  index: number
  count: number
  busy: boolean
  /** Why checking is impossible, or null. Separate from `busy` on purpose: it
   *  stops the model calls without freezing the text a user can still edit. */
  blocked: string | null
  checking: string[]
  /** Blocks an edit un-checked since the page opened. See the editor's state. */
  invalidated: Set<string>
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
              blocked={blocked}
              checking={checking.includes(block.id)}
              invalidated={invalidated.has(block.id)}
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
  block, index, count, busy, blocked, checking, invalidated, preview, onChange, onRemove,
  onMove, onCheck, onSaveSql,
}: {
  block: ReportBlock
  index: number
  count: number
  busy: boolean
  /** Why this row cannot be checked, or null. See `SectionCard`. */
  blocked: string | null
  checking: boolean
  /** Whether an edit on this page un-checked this row. */
  invalidated: boolean
  preview?: string
  onChange: (payload: Record<string, unknown>) => void
  onRemove: () => void
  onMove: (to: number) => void
  onCheck: () => void
  onSaveSql: (sql: string) => Promise<ReportBlockCheck | null>
}) {
  const [showSql, setShowSql] = useState(false)
  const unchecked = block.feasibility_status === 'UNCHECKED'
  // A statement somebody typed. It survives a reworded question — the API
  // keeps it and only drops the verdict — so the check button here would
  // *replace* it rather than fill a gap, and has to say so.
  const ownSql = block.sql !== '' && block.sql_origin !== 'GENERATED'
  /**
   * The chip, and the two things "Not checked" was being asked to mean at once.
   *
   * An edit resets the verdict, so a row the user checked a minute ago goes
   * back to looking exactly like one nobody has ever touched — and the worse
   * half of that is silent: a hand-written statement is *kept* through a
   * reword, so unless the row says so, the run answers the previous question
   * under the new heading and nothing on this page ever mentioned it.
   */
  const verdict = !unchecked || !invalidated
    ? FEASIBILITY[block.feasibility_status]
    : ownSql
      ? { label: 'Answers the previous wording', tone: 'amber' as ChipTone, rail: 'var(--amber)' }
      : { label: 'Edited — needs checking', tone: 'amber' as ChipTone, rail: 'var(--amber)' }

  return (
    <div
      className="rm-outline-block"
      /* How the sweep finds the row it is about to check, to bring it into
         view. An id rather than a ref because the walk is driven from the
         editor, which holds no handle on any individual row. */
      data-block-id={block.id}
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
          {/* What the document will caption this figure with — a statement,
              where the question above is a question. Not required and not
              pre-filled with the question: **empty means "use the question"**,
              and a copy of it sitting in this box would be one more thing to
              keep in step by hand. Editing it changes a label and nothing
              else, so unlike the question it never resets the verdict. */}
          <InlineEdit
            ariaLabel={`Caption for question ${index + 1}`}
            value={block.title}
            placeholder="Caption in the report — defaults to the question"
            multiline
            style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.5 }}
            onCommit={(title) => onChange({ title })}
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
            disabled={busy || checking || blocked !== null}
            className={
              `rm-check${unchecked && !checking && !ownSql && !blocked ? ' is-wanted' : ''}`
            }
            style={{ marginInlineStart: 0, padding: '4px 10px', fontSize: 11.5 }}
            title={
              blocked ??
              (ownSql
                ? 'Asks the model for a new statement from the question, replacing the one you wrote.'
                : 'Turns the question into a query and checks it against your schema.')
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

function reorder<T>(items: T[], from: number, to: number): T[] {
  const next = [...items]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/** "Would they see the figures?" for the share dialog — see `WarnFn`. */
const REPORT_WARN = new Map<string, WarnFn>()
export function reportWarn(reportId: string): WarnFn {
  let fn = REPORT_WARN.get(reportId)
  if (!fn) {
    fn = async (principal) => (await access.reportShareCheck(reportId, principal)).unreadable
    REPORT_WARN.set(reportId, fn)
  }
  return fn
}

/**
 * A report as somebody who may **view** it sees its outline: what it covers,
 * its sections and their questions — and the door to the document, which is
 * the thing they were shared it for. No fields, no checks, no Generate.
 */
function ReportReadOnly({
  report, latestRun, runCount, onBack, onOpenRun, onHistory,
}: {
  report: Report
  latestRun: ReportRun | null
  runCount: number
  onBack: () => void
  onOpenRun: (runId: string) => void
  onHistory: () => void
}) {
  const sections = report.sections
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <header className="rm-dash-header" style={headerStyle}>
        <button onClick={onBack} aria-label="Back to reports" className="rm-icon-btn" style={backButton}>
          <Icon.ArrowLeft size={15} />
        </button>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0, flex: 1, maxWidth: 460 }}>
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
            {report.name}
          </span>
          <span dir="auto" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            {report.description
              || `${sections.length} ${sections.length === 1 ? 'section' : 'sections'}`}
          </span>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <ReachBadge privileges={report.privileges} owner={report.owner_name} />
          {runCount > 0 && (
            <GhostButton onClick={onHistory} style={toolbarBtn}>
              <Icon.List size={13} /> History
              <span style={{ opacity: 0.6 }}>{runCount}</span>
            </GhostButton>
          )}
          {latestRun && (
            <PrimaryButton onClick={() => onOpenRun(latestRun.id)} style={{ padding: '8px 14px' }}>
              <Icon.Doc size={13} /> Open the latest document
            </PrimaryButton>
          )}
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
          {report.data_access === false ? (
            <Note tone="amber">
              This report reads{report.connection_name ? ` “${report.connection_name}”` : ' a data source'},
              which hasn’t been shared with you — its documents show the writing, and a lock
              where each figure would be. Ask the data source’s owner for access to see the numbers.
            </Note>
          ) : null}
          {!latestRun && (
            <Note tone="amber">
              Nothing has been generated from this report yet
              {report.owner_name ? ` — ${report.owner_name} can generate it` : ''}.
            </Note>
          )}

          {report.prompt.trim() && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                padding: 15,
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 12,
              }}
            >
              <span style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--text-strong)' }}>
                What this report covers
              </span>
              <p dir="auto" style={{ margin: 0, fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>
                {report.prompt}
              </p>
            </div>
          )}

          {sections.map((section, index) => (
            <section
              key={section.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
                padding: '15px 16px',
                background: 'var(--panel)',
                border: '1px solid var(--border)',
                borderRadius: 12,
              }}
            >
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                <span style={{ fontSize: 12, color: 'var(--text-faint)', fontVariantNumeric: 'tabular-nums' }}>
                  {section.kind === 'EXECUTIVE_SUMMARY' ? '★' : index + 1}
                </span>
                <span dir="auto" style={{ fontSize: 15, fontWeight: 650, color: 'var(--text-strong)' }}>
                  {section.heading}
                </span>
              </div>
              {section.intent && (
                <p dir="auto" style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.55, paddingInlineStart: 22 }}>
                  {section.intent}
                </p>
              )}
              {section.blocks.length > 0 && (
                <ol style={{ margin: 0, paddingInlineStart: 40, display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {section.blocks.map((block) => (
                    <li key={block.id} dir="auto" style={{ fontSize: 13, color: 'var(--text)', lineHeight: 1.55 }}>
                      {block.title || block.question}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          ))}
        </div>
      </div>
    </div>
  )
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
