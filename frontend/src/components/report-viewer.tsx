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
import { useEffect, useMemo, useRef, useState } from 'react'
import { isReportRunInFlight, reports as api } from '../api/client'
import type {
  ChartOption, KpiSpec, NumericFinding, ReportBlockResult, ReportRunDetail,
  ReportSectionResult, ReportSummary,
} from '../api/types'
import { Restricted } from './access'
import { ChartGlyph, ChartTypePicker } from './chart-picker'
import {
  assembleDocument, captionOf, chartTypeOf, claimSpans, figureNumbers, isCallout,
  isEdited, keyFigures, proseOf, renderKindOf, summaryParts,
  type DocumentSection, type KeyFigure,
} from './report-document'
import { RUN_TONE } from './report-history'
import { printReport } from './report-print'
import { VegaChart } from './VegaChart'
import {
  Chip, CopyButton, EmptyState, ErrorNote, GhostButton, Icon, Kpi, Logo,
  PrimaryButton, ProgressBar, ResultTable, Spinner, TextArea, relativeTime,
} from './ui'
import { Note, backButton, headerStyle, labelsFor, toolbarBtn } from './report-parts'

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
    <div
      className="rm-report-view"
      style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}
    >
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
              once there is a document to print.

              The browser's own date, title and URL are gone by then: the
              stylesheet claims all six `@page` margin boxes, so there is
              nowhere left for the browser to print them (docs/reference/reports.md
              §12). */}
          {!running && document_.length > 0 && (
            <GhostButton
              onClick={() => void print()}
              disabled={printing}
              style={toolbarBtn}
              title="Print the saved run, at page width, with the page numbered."
            >
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
        {/* `lang` is declared for the hyphenator, not for the layout. The
            printed body is justified, and `hyphens: auto` picks its break
            patterns from the language in force — inherited from
            `<html lang="en">` it would try English patterns on Persian words.
            Persian is not hyphenated at all, so naming it is how that
            paragraph gets justified without them. */}
        <article
          ref={article}
          className="rm-report"
          dir={dir}
          lang={language}
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
                connectionId={report?.connection_id ?? null}
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
    <header
      className="rm-report-cover"
      style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
    >
      {/* The masthead: what this document is and what it is called on one
          side, whose press it came off on the other.

          The mark sits beside the whole block rather than on the eyebrow's
          line, which is what lets it be a real mark instead of a bullet — set
          against the title it has something its own size to be measured
          against, and it anchors the top corner of the page without pushing a
          single line down. Logical properties, so a Persian report mirrors the
          masthead rather than stranding the mark on the wrong edge. */}
      <div
        className="rm-report-masthead"
        style={{ display: 'flex', alignItems: 'flex-start', gap: 24 }}
      >
        <div
          className="rm-report-coverhead"
          style={{ display: 'flex', flexDirection: 'column', gap: 14, flex: 1, minWidth: 0 }}
        >
          <span
            className="rm-report-kicker"
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
              className="rm-report-title"
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
                className="rm-report-subtitle"
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
        </div>

        <Brandmark />
      </div>

      {/* A definition list rather than a sentence: these are fields, a reader
          scans them, and on paper they are what a cover page carries. */}
      <dl
        className="rm-report-meta"
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

/**
 * The mark on the cover.
 *
 * A printed report leaves the tool: it is mailed, filed and read months later
 * by someone who never saw the application, and the one thing the page cannot
 * otherwise say is where it was made. The app chrome that says so on screen is
 * the first thing `@media print` removes, so the document has to carry it
 * itself — which is why this sits inside the article rather than in the
 * viewer's header.
 *
 * The mark alone, without the name set beside it. The name is already on the
 * page — it is in the alt text for a reader who cannot see the mark, and a
 * cover that spells out the tool beside the report's own title is a cover
 * arguing about whose document it is. A mark needs no caption.
 *
 * Which is also what lets it be *big*: at the size of a word it would read as
 * a bullet in front of the eyebrow, while set against the title it is a mark,
 * and it anchors the corner of the page the way the rule under the metadata
 * anchors the foot of the block.
 */
function Brandmark() {
  return (
    <div
      className="rm-report-brand"
      style={{ marginInlineStart: 'auto', display: 'flex', flexShrink: 0 }}
    >
      <Logo size={54} />
    </div>
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
  figures: KeyFigure[]
  t: Record<string, string>
}) {
  if (figures.length === 0) return null

  return (
    <section
      aria-label={t.keyFigures}
      className="rm-report-band"
      style={{ display: 'flex', flexDirection: 'column', gap: 9 }}
    >
      <span
        className="rm-report-bandlabel"
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
        {figures.map(({ block, caption }) => (
          <div
            key={block.id}
            className="rm-report-tile"
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
            {/* Labelled with the document's caption rather than with the
                column the number was computed from: `total_rev` names a
                result, and this band is the first thing a reader sees. */}
            {block.kpi && <Kpi spec={{ ...block.kpi, label: caption }} compact />}
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
          className="rm-report-methodintro"
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
              className="rm-report-methoditem"
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 5,
                paddingTop: 10,
                borderTop: '1px solid var(--border)',
              }}
            >
              {/* A callout carries no figure number, so it is headed by what
                  it is instead. The entry is never headed by a dash: the
                  appendix is where a number gets attributed, and an entry
                  nobody can match to something in the body attributes
                  nothing. */}
              <span
                className="rm-report-figlabel"
                style={{ fontSize: 11, fontWeight: 650, color: 'var(--text2)' }}
              >
                {isCallout(block) ? t.headline : `${t.figure} ${figures.get(block.id) ?? '—'}`}
              </span>
              <span
                dir="auto"
                className="rm-report-caption"
                style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}
              >
                {captionOf(block)}
              </span>
              {/* The question, in the one place it belongs: beside the
                  statement it produced. It is how this number was obtained,
                  which is the question this whole section exists to answer. */}
              <span
                dir="auto"
                className="rm-report-methodq"
                style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.5 }}
              >
                <span style={{ color: 'var(--text-faint)' }}>{t.question}: </span>
                {block.question_snapshot}
              </span>
              <span
                className="rm-report-source"
                style={{ fontSize: 11, color: 'var(--text-faint)' }}
              >
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
                className="rm-report-sql"
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

      <p
        className="rm-report-asof"
        style={{ margin: 0, fontSize: 11.5, lineHeight: 1.7, color: 'var(--text-faint)' }}
      >
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
  connectionId,
}: {
  reportId: string
  runId: string
  section: DocumentSection
  /** Passed through to a withheld figure's explainer, and nothing else. */
  connectionId: string | null
  figures: Map<string, number>
  headline: KeyFigure[]
  t: Record<string, string>
  busy: boolean
  onRetry: () => void
  onProse: (text: string | null) => Promise<void>
  onBlock: (block: ReportBlockResult) => void
}) {
  const failed = section.prose?.status === 'FAILED'
  const summary = section.isSummary
  // Which figure a footnote in this section's paragraph asked to see. Held
  // here rather than in the block, because the paragraph and the figure are
  // siblings and the footnote is the only thing that connects them.
  const [cited, setCited] = useState<string | null>(null)

  return (
    <section
      className={summary ? 'rm-report-section rm-report-summary' : 'rm-report-section'}
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
            className="mono rm-report-num"
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
          className="rm-report-heading"
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
          className="rm-report-rule"
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
          onCite={setCited}
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
          connectionId={connectionId}
          // A footnote asked for this one. Passed as the id rather than a
          // boolean so pressing the same footnote twice re-opens it: the block
          // clears the request once it has honoured it, and a second press is
          // a new value rather than the same `true` React will not re-render.
          cited={cited === block.id}
          onCited={() => setCited(null)}
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
  result, summary, t, editable, onSave, onCite,
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
  /** A footnote was pressed: show the statement behind that result. */
  onCite?: (blockResultId: string) => void
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
  // The sentences this paragraph was written as, each knowing its source.
  // `claimSpans` returns the whole paragraph as one uncited span when the
  // claims do not reassemble into it — which is every edited paragraph, and
  // is the right answer: a footnote on a sentence somebody has rephrased
  // points at a source that sentence no longer draws on.
  const spans = summary ? [] : claimSpans(parts.lead, result.claims)

  return (
    <div
      className="rm-turn rm-report-prose"
      style={{ display: 'flex', flexDirection: 'column', gap: 11 }}
    >
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
          {summary || spans.length <= 1
            ? parts.lead
            : spans.map((span, index) => (
                <span key={index}>
                  {index > 0 && ' '}
                  {span.text}
                  {span.blockResultId && onCite && (
                    <Footnote
                      cites={span.cites}
                      unsupported={span.unsupported}
                      onOpen={() => onCite(span.blockResultId!)}
                    />
                  )}
                </span>
              ))}
        </p>
      )}

      {/* The one piece of structure the model is asked to produce, rendered as
          structure. Run together as a paragraph it reads as a list someone
          forgot to format; set out, it is the part of an executive summary
          people actually take away. */}
      {parts.findings.length > 0 && (
        <ul
          className="rm-report-findings"
          style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 8 }}
        >
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
 * The mark after a sentence that says which figure it came from.
 *
 * Set as a footnote rather than as a link on the number itself, and the reason
 * is the sentence: a claim is an assertion made from one result, not a digit
 * lifted out of one, and underlining "1,200,000" would say the number is
 * sourced while leaving the claim around it unsourced. The whole sentence is
 * what was drawn from the figure, so the whole sentence carries the mark.
 *
 * Amber where the cited result does not support a figure in the sentence —
 * the same tone `NumericMarker` uses, and the same posture: **a finding is a
 * suspicion, never a verdict**. It does not stop anyone reading the sentence.
 *
 * Hidden on paper. The appendix carries every statement in full, and a printed
 * document cannot open anything.
 */
function Footnote({
  cites, unsupported, onOpen,
}: {
  cites: number | null
  unsupported: boolean
  onOpen: () => void
}) {
  const label = cites === null ? '?' : String(cites)
  return (
    <button
      type="button"
      onClick={onOpen}
      className="rm-report-hide-in-print"
      title={
        unsupported
          ? `Figure ${label} is cited here, and does not carry every number in this sentence. Open its statement.`
          : `Drawn from figure ${label}. Open the statement behind it.`
      }
      aria-label={`Open the statement behind figure ${label}`}
      style={{
        verticalAlign: 'super',
        marginInlineStart: 2,
        padding: '0 3px',
        minWidth: 14,
        borderRadius: 4,
        border: '1px solid transparent',
        background: 'transparent',
        color: unsupported ? 'var(--amber-text)' : 'var(--text-faint)',
        fontSize: 9.5,
        fontVariantNumeric: 'tabular-nums',
        lineHeight: 1.4,
        cursor: 'pointer',
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.borderColor = 'var(--border)'
        event.currentTarget.style.color = 'var(--accent)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.borderColor = 'transparent'
        event.currentTarget.style.color = unsupported
          ? 'var(--amber-text)'
          : 'var(--text-faint)'
      }}
    >
      {label}
    </button>
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
 * **The caption is a statement, not the question that produced it.** A figure
 * headed "How did revenue move month by month?" reads as a transcript of the
 * session the document came out of; a report captions its exhibits with what
 * they show. The question is not dropped — it moves to the query panel below
 * and to the appendix, which is where a reader goes to ask how a number was
 * obtained. `captionOf` falls back to it when no title was ever written.
 *
 * A block that produced one number is not drawn here at all: see `BlockCallout`.
 *
 * The source line under it says how many rows the statement returned and when,
 * for the same reason the SQL is one click away: a number nobody can trace back
 * to a statement is a number nobody should act on.
 */
function BlockView({
  reportId, runId, block, figure, t, onBlock, connectionId, cited, onCited,
}: {
  reportId: string
  runId: string
  block: ReportBlockResult
  /** A footnote in the paragraph above asked to see this figure's statement. */
  cited?: boolean
  /** Honoured — clear the request, so pressing the same footnote again works. */
  onCited?: () => void
  /** The database this document was built over, for a withheld figure's
   *  "Why?" link. Null once that connection has been deleted. */
  connectionId: string | null
  /** Its place in the document's numbering; absent for a callout, and while mid-merge. */
  figure: number | undefined
  t: Record<string, string>
  onBlock: (block: ReportBlockResult) => void
}) {
  const kind = renderKindOf(block)
  const caption = captionOf(block)

  // The intersection rule, on a figure. Checked **before** the callout branch
  // and before `kind`: a withheld block has no kpi, no rows and no chart, so
  // every branch below it would render an empty exhibit rather than say why
  // it is empty. The caption, the figure number and the position survive, so
  // the document still reads as a document and the reader can name the
  // exhibit they need access to.
  if (block.restricted) {
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
        <figcaption
          className="rm-report-figcap"
          style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
        >
          {figure !== undefined && (
            <span
              className="mono rm-report-figlabel"
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
            className="rm-report-caption"
            style={{
              fontSize: 13.5,
              fontWeight: 650,
              color: 'var(--text-strong)',
              lineHeight: 1.45,
            }}
          >
            {caption}
          </span>
        </figcaption>
        <Restricted
          reason={block.error_message}
          connectionId={connectionId}
          compact
        />
      </figure>
    )
  }

  if (isCallout(block) && block.kpi) {
    // The caption *is* the label. `plan_kpi` labels the number with its column
    // (`total_rev`), which beside a written caption is the same thing said
    // twice and worse the second time.
    return (
      <BlockCallout
        block={block}
        spec={{ ...block.kpi, label: caption }}
        t={t}
        cited={cited}
        onCited={onCited}
      />
    )
  }

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
      <figcaption
        className="rm-report-figcap"
        style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
      >
        {figure !== undefined && (
          <span
            className="mono rm-report-figlabel"
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
          className="rm-report-caption"
          style={{ fontSize: 13.5, fontWeight: 650, color: 'var(--text-strong)', lineHeight: 1.45 }}
        >
          {caption}
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
          <ResultTable
            spec={block}
            previewRows={12}
            maxHeight={420}
            download={caption || 'figure'}
          />
        </>
      )}

      <BlockFoot block={block} t={t} cited={cited} onCited={onCited} />
    </figure>
  )
}

/**
 * A single number, in the flow of the section that discusses it.
 *
 * The same result a numbered exhibit would have been given a quarter of a page
 * for. One value has no structure to study — no series, no ranking, no shares —
 * so it is set as a callout beside the prose instead: the convention every
 * reporting tool and every consulting deck follows, and the reason a figure
 * number in this document still means "something worth turning to".
 *
 * It keeps its provenance in full. The number is smaller; the audit trail is
 * not, because that is the promise the product makes about every figure it
 * prints, and a lone number is the one most likely to be quoted out of the
 * document.
 */
function BlockCallout({
  block, spec, t, cited, onCited,
}: {
  block: ReportBlockResult
  /** The block's own KPI, labelled with the document's caption for it. */
  spec: KpiSpec
  t: Record<string, string>
  /** A footnote asked for this one — a callout is cited like any exhibit. */
  cited?: boolean
  onCited?: () => void
}) {
  return (
    <aside
      className="rm-report-callout"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: '12px 16px',
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        // The rail is what says "this is an aside, not an exhibit" at a glance,
        // and it is logical rather than left so a Persian document gets it on
        // the side the reader starts from.
        borderInlineStart: '3px solid var(--accent)',
        borderRadius: 10,
      }}
    >
      <Kpi spec={spec} compact />
      <BlockFoot block={block} t={t} cited={cited} onCited={onCited} />
    </aside>
  )
}

/**
 * Where a figure came from: how many rows, computed when, and the statement.
 *
 * Shared by the exhibit and the callout, because the audit trail is a property
 * of a result and not of how large it was drawn.
 *
 * The question lives here rather than over the picture. It is how the number
 * was obtained — provenance, like the SQL it sits above and the row count
 * beside it — and a reader who wants it is already looking in this line.
 */
function BlockFoot({
  block, t, cited, onCited,
}: {
  block: ReportBlockResult
  t: Record<string, string>
  cited?: boolean
  onCited?: () => void
}) {
  const [showSql, setShowSql] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  // A footnote was pressed: open the statement and bring it into view. The
  // request is cleared as it is honoured, so pressing the same footnote twice
  // works — a boolean that stayed true would be the same value on the second
  // press and React would do nothing.
  useEffect(() => {
    if (!cited) return
    setShowSql(true)
    box.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    onCited?.()
  }, [cited, onCited])

  return (
    <>
      <div
        ref={box}
        className="rm-report-figfoot"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          flexWrap: 'wrap',
          paddingTop: 3,
          borderTop: '1px solid var(--border)',
        }}
      >
        <span
          className="rm-report-source"
          style={{ fontSize: 10.5, color: 'var(--text-faint)', paddingTop: 5 }}
        >
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
        <div
          className="rm-report-hide-in-print"
          style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
        >
          <span dir="auto" style={{ fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--text-faint)' }}>{t.question}: </span>
            {block.question_snapshot}
          </span>
          <pre
            dir="ltr"
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
        </div>
      )}
    </>
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
