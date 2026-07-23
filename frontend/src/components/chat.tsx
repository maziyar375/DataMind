/**
 * Chat turn rendering.
 *
 * The step chips, the "Generated SQL" disclosure, the result table, and the
 * metadata chips all read from persisted run data rather than from live
 * events, which is why reopening an old conversation shows the full history
 * of how an answer was reached rather than a bare paragraph.
 *
 * An assistant turn is laid out as an avatar gutter plus an open content
 * column rather than a bordered card. Wrapping every answer in a panel made
 * the transcript read as a stack of forms; only the things that genuinely are
 * objects — a result table, the SQL — keep a border of their own.
 */
import { useMemo, useState } from 'react'
import type { Artifact, GeneratedQuery, RunDetail, RunStep, TableArtifactSpec } from '../api/types'
import { Chip, CopyButton, Dot, dirOf, Icon, Spinner } from './ui'
import { NODE_META } from '../theme/tokens'

// ── turn frame ────────────────────────────────────────────────────────────
function AssistantAvatar({ busy, failed }: { busy?: boolean; failed?: boolean }) {
  return (
    <span
      style={{
        width: 30,
        height: 30,
        borderRadius: 9,
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: failed ? 'var(--red-bg)' : 'var(--accent-bg)',
        border: `1px solid ${failed ? 'var(--red-border)' : 'var(--accent-border)'}`,
        marginTop: 1,
      }}
    >
      {busy ? (
        <Spinner size={14} />
      ) : failed ? (
        <Icon.Alert size={15} stroke="var(--red)" />
      ) : (
        <Icon.Sparkle size={15} stroke="var(--accent)" />
      )}
    </span>
  )
}

/** Avatar gutter plus content column, so every answer lines up down the page. */
function Turn({
  avatar, children,
}: {
  avatar: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div
      className="rm-enter rm-turn"
      style={{ display: 'flex', gap: 13, alignItems: 'flex-start', maxWidth: 780 }}
    >
      {avatar}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          paddingTop: 3,
        }}
      >
        {children}
      </div>
    </div>
  )
}

// ── user turn ─────────────────────────────────────────────────────────────
export function UserBubble({ text }: { text: string }) {
  return (
    <div
      className="rm-enter"
      dir={dirOf(text)}
      style={{
        alignSelf: 'flex-end',
        maxWidth: 560,
        background: 'var(--accent)',
        color: 'var(--on-accent)',
        padding: '10px 15px',
        borderRadius: '14px 14px 4px 14px',
        fontSize: 14,
        lineHeight: 1.55,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        boxShadow: '0 1px 3px rgba(0,0,0,0.10)',
      }}
    >
      {text}
    </div>
  )
}

// ── step chips ────────────────────────────────────────────────────────────
export function StepTrail({ steps }: { steps: RunStep[] }) {
  if (steps.length === 0) return null
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 6,
        alignItems: 'center',
      }}
    >
      {steps.map((step) => {
        const meta = NODE_META[step.name] ?? { label: step.name, detail: '' }
        const running = step.status === 'RUNNING'
        const failed = step.status === 'FAILED'
        const skipped = step.status === 'SKIPPED'

        const color = failed
          ? 'var(--red)'
          : running
            ? 'var(--accent)'
            : skipped
              ? 'var(--text-faint)'
              : 'var(--green)'

        return (
          <span
            key={step.seq}
            title={step.detail ?? meta.detail}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              fontWeight: 500,
              padding: '4px 9px',
              borderRadius: 6,
              color: running ? 'var(--text-strong)' : 'var(--text-dim)',
              background: running ? 'var(--accent-bg)' : 'var(--panel-alt)',
              border: running ? '1px solid var(--accent-border)' : '1px solid transparent',
            }}
          >
            {running ? <Spinner size={11} /> : <Dot color={color} />}
            {meta.label}
            {step.duration_ms != null && step.status === 'DONE' && (
              <span style={{ color: 'var(--text-faint)' }}>{step.duration_ms}ms</span>
            )}
          </span>
        )
      })}
    </div>
  )
}

/**
 * The step trail stays open on a finished run.
 *
 * Showing route → retrieve → generate → validate → execute → present, each
 * with its own timing, is how a reader can see that an answer went through a
 * validated pipeline rather than a single model call. That evidence is the
 * product's argument for itself, so it is not hidden behind a disclosure;
 * the toggle only exists for anyone who wants the transcript quieter.
 */
function StepSummary({ run }: { run: RunDetail }) {
  const [open, setOpen] = useState(true)
  if (run.steps.length === 0) return null

  const total =
    run.total_latency_ms ??
    run.steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0)
  const seconds = (total / 1000).toFixed(total < 1000 ? 2 : 1)
  const failed = run.steps.some((s) => s.status === 'FAILED')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          alignSelf: 'flex-start',
          fontSize: 11.5,
          fontWeight: 600,
          color: failed ? 'var(--red)' : 'var(--green)',
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
        }}
      >
        <Icon.Chevron open={open} size={12} stroke="currentColor" />
        {failed ? (
          <>Stopped after {run.steps.length} steps · {seconds}s</>
        ) : (
          <>All {run.steps.length} steps passed · {seconds}s</>
        )}
      </button>
      {open && <StepTrail steps={run.steps} />}
    </div>
  )
}

export function ThinkingCard({ steps, detail }: { steps: RunStep[]; detail?: string }) {
  const active = steps.find((s) => s.status === 'RUNNING')
  const label = active
    ? (NODE_META[active.name]?.detail ?? 'Working…')
    : (detail ?? 'Starting…')

  return (
    <Turn avatar={<AssistantAvatar busy />}>
      <div
        style={{
          fontSize: 13.5,
          color: 'var(--text-dim)',
        }}
      >
        <span className="rm-pulse">{label}</span>
      </div>
      <StepTrail steps={steps} />
    </Turn>
  )
}

// ── generated SQL disclosure ──────────────────────────────────────────────
export function SqlPanel({ queries }: { queries: GeneratedQuery[] }) {
  const [open, setOpen] = useState(false)
  if (queries.length === 0) return null

  const final = queries[queries.length - 1]
  const rejected = queries.filter((q) => q.validation_status !== 'VALID')
  const finalSql = final.rewritten_sql ?? final.raw_sql

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--code-bg)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center' }}>
        <button
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flex: 1,
            minWidth: 0,
            padding: '10px 12px',
            background: 'transparent',
            border: 'none',
            color: 'var(--text-dim)',
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <Icon.Chevron open={open} size={13} />
          Generated SQL
          {rejected.length > 0 && (
            <Chip tone="amber">
              {rejected.length} repair{rejected.length > 1 ? 's' : ''}
            </Chip>
          )}
        </button>
        {final.validation_status === 'VALID' && (
          <span style={{ paddingRight: 8, flexShrink: 0 }}>
            <CopyButton text={finalSql} label="Copy SQL" />
          </span>
        )}
      </div>

      {open && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {rejected.map((query) => (
            <div key={query.attempt_no} style={{ padding: '10px 14px' }}>
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--amber)',
                  marginBottom: 6,
                  fontWeight: 600,
                }}
              >
                Attempt {query.attempt_no} — rejected
              </div>
              <pre
                className="mono"
                style={{
                  margin: 0,
                  fontSize: 12,
                  color: 'var(--text-faint)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {query.raw_sql}
              </pre>
              {query.validation_report.issues?.map((issue, i) => (
                <div
                  key={i}
                  style={{ fontSize: 11.5, color: 'var(--amber)', marginTop: 6 }}
                >
                  <span className="mono">[{issue.rule_id}]</span> {issue.message}
                </div>
              ))}
            </div>
          ))}

          {final.validation_status === 'VALID' && (
            <div style={{ padding: '12px 14px' }}>
              <pre
                className="mono"
                style={{
                  margin: 0,
                  fontSize: 12.5,
                  color: 'var(--code-text)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  lineHeight: 1.6,
                }}
              >
                {finalSql}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── result table ──────────────────────────────────────────────────────────

export function ResultTable({ spec }: { spec: TableArtifactSpec }) {
  const [expanded, setExpanded] = useState(false)
  const rows = expanded ? spec.rows : spec.rows.slice(0, 5)

  if (spec.columns.length === 0) {
    return (
      <div style={{ fontSize: 13, color: 'var(--text-dim)' }}>
        The query ran successfully but returned no rows.
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 10,
          overflow: 'auto',
          maxHeight: expanded ? 420 : 'none',
        }}
      >
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12.5,
          }}
        >
          <thead>
            <tr>
              {spec.columns.map((column) => (
                <th
                  key={column.name}
                  style={{
                    position: 'sticky',
                    top: 0,
                    textAlign: column.semantic_type === 'quantitative' ? 'right' : 'left',
                    padding: '9px 12px',
                    background: 'var(--panel-alt)',
                    color: 'var(--text-dim)',
                    fontWeight: 600,
                    fontSize: 11,
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {column.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} style={{ borderTop: '1px solid var(--border)' }}>
                {row.map((cell, cellIndex) => {
                  const column = spec.columns[cellIndex]
                  const numeric = column?.semantic_type === 'quantitative'
                  return (
                    <td
                      key={cellIndex}
                      className={numeric ? 'mono' : undefined}
                      style={{
                        padding: '8px 12px',
                        textAlign: numeric ? 'right' : 'left',
                        color: 'var(--text2)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {formatCell(cell)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {spec.rows.length > 5 && (
        <button
          onClick={() => setExpanded(!expanded)}
          style={{
            alignSelf: 'flex-start',
            fontSize: 12,
            color: 'var(--accent)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {expanded
            ? 'Show fewer rows'
            : `Show all ${spec.rows.length.toLocaleString()} rows`}
        </button>
      )}
    </div>
  )
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    return Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  return String(value)
}

// ── simple bar chart, drawn from the result itself ────────────────────────
export function ResultBars({ spec }: { spec: TableArtifactSpec }) {
  const chart = useMemo(() => {
    const labelIndex = spec.columns.findIndex((c) => c.semantic_type !== 'quantitative')
    const valueIndex = spec.columns.findIndex((c) => c.semantic_type === 'quantitative')
    if (labelIndex === -1 || valueIndex === -1) return null

    const points = spec.rows.slice(0, 12).map((row) => ({
      label: String(row[labelIndex] ?? ''),
      value: Number(row[valueIndex] ?? 0),
    }))
    if (points.length < 2 || points.some((p) => Number.isNaN(p.value))) return null

    const max = Math.max(...points.map((p) => p.value))
    return max > 0 ? { points, max } : null
  }, [spec])

  if (!chart) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
      {chart.points.map((point, index) => (
        <div key={index} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span
            style={{
              width: 140,
              fontSize: 12,
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flexShrink: 0,
            }}
            title={point.label}
          >
            {point.label}
          </span>
          <span
            style={{
              flex: 1,
              height: 8,
              background: 'var(--panel-alt)',
              borderRadius: 4,
              overflow: 'hidden',
            }}
          >
            <span
              style={{
                display: 'block',
                height: '100%',
                width: `${(point.value / chart.max) * 100}%`,
                background: 'var(--accent)',
                borderRadius: 4,
                transition: 'width .3s ease',
              }}
            />
          </span>
          <span
            className="mono"
            style={{
              width: 92,
              textAlign: 'right',
              fontSize: 12,
              color: 'var(--text2)',
              flexShrink: 0,
            }}
          >
            {point.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── metadata chips ────────────────────────────────────────────────────────
export function RunMetadata({ run }: { run: RunDetail }) {
  const tables = run.queries.at(-1)?.referenced_tables ?? []
  const chips: React.ReactNode[] = []

  if (tables.length > 0) {
    chips.push(
      <Chip key="tables">
        tables: {tables.map((t) => t.split('.').pop()).join(', ')}
      </Chip>,
    )
  }
  if (run.db_latency_ms != null) {
    chips.push(<Chip key="ms">{run.db_latency_ms}ms</Chip>)
  }
  const scanned = findScanned(run.artifacts)
  if (scanned != null) {
    chips.push(<Chip key="scanned">{scanned.toLocaleString()} rows scanned</Chip>)
  }
  if (run.repair_count > 0) {
    chips.push(
      <Chip key="repair" tone="amber">
        {run.repair_count} repair{run.repair_count > 1 ? 's' : ''}
      </Chip>,
    )
  }

  if (chips.length === 0) return null
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {chips}
    </div>
  )
}

function findScanned(artifacts: Artifact[]): number | null {
  const table = artifacts.find((a) => a.kind === 'TABLE')
  const value = table?.spec?.rows_scanned_estimate
  return typeof value === 'number' ? value : null
}

// ── error card ────────────────────────────────────────────────────────────
export function RunErrorCard({ run }: { run: RunDetail }) {
  return (
    <Turn avatar={<AssistantAvatar failed />}>
      <div
        style={{
          background: 'var(--red-bg)',
          border: '1px solid var(--red-border)',
          borderRadius: 12,
          padding: '13px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div
          style={{
            fontSize: 13.5,
            fontWeight: 600,
            color: 'var(--red)',
            lineHeight: 1.5,
          }}
        >
          {run.error_message ?? 'This run did not complete.'}
        </div>
        {run.error_code && (
          <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            {run.error_code}
          </span>
        )}
      </div>
      {run.steps.length > 0 && <StepTrail steps={run.steps} />}
      {run.queries.length > 0 && <SqlPanel queries={run.queries} />}
    </Turn>
  )
}

// ── assistant turn ────────────────────────────────────────────────────────
export function AssistantTurn({
  text, run, streaming,
}: {
  text: string
  run: RunDetail | null
  streaming?: boolean
}) {
  const table = run?.artifacts.find((a) => a.kind === 'TABLE')
  const spec = table?.spec as TableArtifactSpec | undefined

  return (
    <Turn avatar={<AssistantAvatar busy={streaming} />}>
      {run && !streaming && <StepSummary run={run} />}

      <div
        dir={dirOf(text)}
        style={{
          fontSize: 14.5,
          lineHeight: 1.65,
          color: 'var(--text)',
          whiteSpace: 'pre-wrap',
        }}
      >
        {text}
        {streaming && (
          <span
            className="rm-pulse"
            style={{
              display: 'inline-block',
              width: 7,
              height: 15,
              marginLeft: 3,
              verticalAlign: 'text-bottom',
              background: 'var(--accent)',
              borderRadius: 1,
            }}
          />
        )}
      </div>

      {spec && spec.rows.length > 1 && <ResultBars spec={spec} />}
      {spec && <ResultTable spec={spec} />}
      {run && run.queries.length > 0 && <SqlPanel queries={run.queries} />}
      {run && <RunMetadata run={run} />}

      {/* Revealed on hover of the turn, so a finished answer stays quiet. */}
      {!streaming && text && (
        <div
          className="rm-turn-actions"
          style={{ display: 'flex', alignItems: 'center', gap: 2, marginLeft: -6 }}
        >
          <CopyButton text={text} />
        </div>
      )}
    </Turn>
  )
}
