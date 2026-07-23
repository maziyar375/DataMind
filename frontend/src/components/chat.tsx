/**
 * Chat turn rendering.
 *
 * The step chips, the "Generated SQL" disclosure, the result table, and the
 * metadata chips all read from persisted run data rather than from live
 * events, which is why reopening an old conversation shows the full history
 * of how an answer was reached rather than a bare paragraph.
 */
import { useMemo, useState } from 'react'
import type { Artifact, GeneratedQuery, RunDetail, RunStep, TableArtifactSpec } from '../api/types'
import { Chip, Dot, dirOf, Icon, Spinner } from './ui'
import { NODE_META } from '../theme/tokens'

// ── user turn ─────────────────────────────────────────────────────────────
export function UserBubble({ text }: { text: string }) {
  return (
    <div
      className="rm-enter"
      dir={dirOf(text)}
      style={{
        alignSelf: 'flex-end',
        maxWidth: 560,
        background: 'var(--accent-bg)',
        border: '1px solid var(--accent-border)',
        color: 'var(--text-strong)',
        padding: '10px 14px',
        borderRadius: 10,
        fontSize: 14,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
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
              borderRadius: 5,
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

export function ThinkingCard({ steps, detail }: { steps: RunStep[]; detail?: string }) {
  const active = steps.find((s) => s.status === 'RUNNING')
  const label = active
    ? (NODE_META[active.name]?.detail ?? 'Working…')
    : (detail ?? 'Starting…')

  return (
    <div
      className="rm-enter"
      style={{
        maxWidth: 720,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '16px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 9,
          fontSize: 13,
          color: 'var(--text-dim)',
        }}
      >
        <Spinner size={14} />
        <span className="rm-pulse">{label}</span>
      </div>
      <StepTrail steps={steps} />
    </div>
  )
}

// ── generated SQL disclosure ──────────────────────────────────────────────
export function SqlPanel({ queries }: { queries: GeneratedQuery[] }) {
  const [open, setOpen] = useState(false)
  if (queries.length === 0) return null

  const final = queries[queries.length - 1]
  const rejected = queries.filter((q) => q.validation_status !== 'VALID')

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 9,
        overflow: 'hidden',
        background: 'var(--code-bg)',
      }}
    >
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '9px 12px',
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
          <span style={{ marginLeft: 'auto' }}>
            <Chip tone="amber">
              {rejected.length} repair{rejected.length > 1 ? 's' : ''}
            </Chip>
          </span>
        )}
      </button>

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
            <div style={{ padding: '10px 14px' }}>
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
                {final.rewritten_sql ?? final.raw_sql}
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
          borderRadius: 9,
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
    <div
      className="rm-enter"
      style={{
        maxWidth: 720,
        background: 'var(--red-bg)',
        border: '1px solid var(--red-border)',
        borderRadius: 12,
        padding: '14px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 13.5,
          fontWeight: 600,
          color: 'var(--red)',
        }}
      >
        <Icon.Alert size={15} />
        {run.error_message ?? 'This run did not complete.'}
      </div>
      {run.error_code && (
        <span className="mono" style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {run.error_code}
        </span>
      )}
      {run.steps.length > 0 && <StepTrail steps={run.steps} />}
      {run.queries.length > 0 && <SqlPanel queries={run.queries} />}
    </div>
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
    <div
      className="rm-enter"
      style={{
        maxWidth: 720,
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '18px 20px',
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      {run && run.steps.length > 0 && <StepTrail steps={run.steps} />}

      {spec && spec.rows.length > 1 && <ResultBars spec={spec} />}

      <div
        dir={dirOf(text)}
        style={{
          fontSize: 14.5,
          lineHeight: 1.55,
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

      {spec && <ResultTable spec={spec} />}
      {run && run.queries.length > 0 && <SqlPanel queries={run.queries} />}
      {run && <RunMetadata run={run} />}
    </div>
  )
}
