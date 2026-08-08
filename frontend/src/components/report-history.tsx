/**
 * Every generation of one report, newest first.
 *
 * A report is a **template plus its runs**, and until now the product only ever
 * showed you one of them — the latest. That is the wrong shape for the thing
 * reports exist to do: a document generated in Farvardin and again in Mehr is
 * two readings of the same structure against different data, and the whole
 * point of keeping `report_runs` immutable is that both stay readable. Somebody
 * comparing this quarter with last quarter needs the list, not the newest row.
 *
 * Nothing here reads a run's *results*. `GET /reports/{id}/runs` returns the run
 * rows only — status, timings, the model that wrote it, the prompt version — so
 * opening this page costs one small request however many documents it lists, and
 * the results are read by the viewer when a run is actually opened.
 *
 * Its own file rather than a fourth act in `report.tsx`: that file is already
 * the outline editor *and* the document viewer, and a third full-page view with
 * no state in common with either is what tips a long file into an unreadable
 * one.
 */
import { useEffect, useState } from 'react'

import { isReportRunInFlight, reports as api } from '../api/client'
import type { ReportRun, ReportRunStatus } from '../api/types'
import {
  Chip, EmptyState, ErrorNote, GhostButton, Icon, Spinner, relativeTime,
} from './ui'
import type { ChipTone } from './ui'

/**
 * What each terminal state is called, and how loudly.
 *
 * `PARTIAL` is amber and its own word. It is the honest state for a run whose
 * status is *derived* from independently-failable sections: some were written
 * and some were not, and calling that either "complete" or "failed" is a lie the
 * reader has to open the document to catch.
 *
 * Exported because the viewer's header renders the same vocabulary, and two
 * copies are how the two screens quietly stop agreeing about what `PARTIAL`
 * means.
 */
export const RUN_TONE: Record<ReportRunStatus, { label: string; tone: ChipTone }> = {
  QUEUED: { label: 'Queued', tone: 'neutral' },
  RUNNING: { label: 'Generating', tone: 'accent' },
  SUCCEEDED: { label: 'Complete', tone: 'green' },
  PARTIAL: { label: 'Partly complete', tone: 'amber' },
  FAILED: { label: 'Failed', tone: 'red' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral' },
}

/** How long a run took, or nothing while it is still taking it. */
function durationOf(run: ReportRun): string | null {
  if (!run.started_at || !run.finished_at) return null
  const ms = new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return null
  if (ms < 1000) return `${ms} ms`
  const seconds = Math.round(ms / 1000)
  if (seconds < 90) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

export function ReportRunHistory({
  reportId, reportName, currentRunId, onOpenRun, onBack,
}: {
  reportId: string
  reportName: string
  /** The run being read, if the reader came here from one. Marked, not filtered. */
  currentRunId?: string | null
  onOpenRun: (runId: string) => void
  onBack: () => void
}) {
  const [runs, setRuns] = useState<ReportRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const history = await api.runs(reportId)
        if (!cancelled) setRuns(history)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Could not read this report’s history.')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [reportId])

  const total = runs?.length ?? 0

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
            {runs === null
              ? 'loading…'
              : `${total} generation${total === 1 ? '' : 's'}`}
          </span>
        </div>
      </header>

      <div className="rm-page-pad" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        <div
          style={{
            maxWidth: 880,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          {error && <ErrorNote>{error}</ErrorNote>}

          {runs === null ? (
            <HistorySkeleton />
          ) : runs.length === 0 ? (
            <EmptyState
              icon={<Icon.List size={20} />}
              title="No generations yet"
              body={
                'A report keeps every document it has produced. Generate this one from '
                + 'its outline and each run is kept here, readable months later exactly '
                + 'as it was written.'
              }
              action={<GhostButton onClick={onBack}>Back to the outline</GhostButton>}
            />
          ) : (
            <>
              <p
                style={{
                  margin: 0,
                  fontSize: 12.5,
                  lineHeight: 1.7,
                  color: 'var(--text-dim)',
                }}
              >
                Each generation is a snapshot of the data at the moment it ran. Opening
                one reads it exactly as it was written — generating again never touches
                a run that already exists.
              </p>

              <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'grid', gap: 9 }}>
                {runs.map((run, index) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    /* Newest first is the order the API returns, so the first row
                       is the latest — the one a reader means by "the report". */
                    latest={index === 0}
                    current={run.id === currentRunId}
                    onOpen={() => onOpenRun(run.id)}
                  />
                ))}
              </ol>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * One generation, as a row you can open.
 *
 * The whole row is the control rather than a trailing "Open" link: it is a list
 * of documents, and a list of documents opens on click everywhere else a person
 * has used one.
 */
function RunRow({
  run, latest, current, onOpen,
}: {
  run: ReportRun
  latest: boolean
  current: boolean
  onOpen: () => void
}) {
  const status = RUN_TONE[run.status]
  const running = isReportRunInFlight(run.status)
  const duration = durationOf(run)
  const when = run.finished_at ?? run.started_at ?? run.created_at

  const meta = [
    run.model_snapshot.model || null,
    duration,
    // The prompt version is not trivia: a document written under r1 and one
    // written under r2 are different artefacts, and six months later nothing
    // else on the page says which one is being read.
    run.prompt_version ? `prompt ${run.prompt_version}` : null,
  ].filter((part): part is string => Boolean(part))

  return (
    <li>
      <button
        onClick={onOpen}
        className="rm-history-row"
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '13px 15px',
          background: 'var(--panel)',
          border: `1px solid ${current ? 'var(--accent-border)' : 'var(--border)'}`,
          borderRadius: 12,
          font: 'inherit',
          textAlign: 'start',
          cursor: 'pointer',
        }}
      >
        <span
          aria-hidden
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 34,
            height: 34,
            flexShrink: 0,
            borderRadius: 9,
            background: 'var(--panel-alt)',
            border: '1px solid var(--border)',
            color: 'var(--text-dim)',
          }}
        >
          {running ? <Spinner size={13} /> : <Icon.Doc size={15} />}
        </span>

        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-strong)' }}>
              {new Date(when).toLocaleString()}
            </span>
            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
              {relativeTime(when)}
            </span>
            {latest && <Chip tone="neutral">Latest</Chip>}
            {current && <Chip tone="accent">Reading</Chip>}
          </span>

          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
            {meta.length > 0 ? meta.join(' · ') : 'no model recorded'}
          </span>

          {/* A run that failed as a whole says why here, so the list answers
              "what happened on the 3rd" without opening an empty document. */}
          {run.error_message && (
            <span
              style={{
                fontSize: 11.5,
                lineHeight: 1.5,
                color: run.status === 'FAILED' ? 'var(--red)' : 'var(--amber)',
              }}
            >
              {run.error_message}
            </span>
          )}

          {running && run.phase && (
            <span dir="auto" style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
              {run.phase}
              {run.progress_total > 0
                && ` — ${run.progress_current} of ${run.progress_total}`}
            </span>
          )}
        </span>

        <span style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
          <Chip tone={status.tone}>{status.label}</Chip>
          <span aria-hidden style={{ display: 'flex', color: 'var(--text-faint)' }}>
            <Icon.Chevron size={14} />
          </span>
        </span>
      </button>
    </li>
  )
}

function HistorySkeleton() {
  return (
    <div aria-hidden style={{ display: 'grid', gap: 9 }}>
      {[0, 1, 2].map((index) => (
        <div key={index} className="rm-bone" style={{ height: 66, borderRadius: 12 }} />
      ))}
    </div>
  )
}

/** The two shells every full-page report view wears, kept identical on purpose. */
const headerStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  padding: '13px 20px',
  borderBottom: '1px solid var(--border)',
  flexWrap: 'wrap',
}

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
