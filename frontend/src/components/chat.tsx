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
import { useState } from 'react'
import type {
  Artifact, ClarificationSpec, GeneratedQuery, KpiSpec, RunDetail, RunStep,
  TableArtifactSpec,
} from '../api/types'
import { Chip, CopyButton, Dot, dirOf, Icon, Kpi, ResultTable, Spinner } from './ui'
import { VegaChart } from './VegaChart'
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

// ── metadata chips ────────────────────────────────────────────────────────
/**
 * The referenced-table list is the one piece of run metadata with no natural
 * ceiling: an ordinary question touches two or three tables, a question about
 * the schema itself touches all forty-two. Printed in full it is a single
 * unbreakable line wider than the transcript, and since the message list only
 * scrolls vertically, that line scrolls the whole conversation sideways. So it
 * collapses to a character budget and opens on click.
 */
const TABLE_LABEL_BUDGET = 56

function splitTableLabel(names: string[]): { shown: string[]; hidden: number } {
  const shown: string[] = []
  let used = 0
  for (const name of names) {
    if (shown.length > 0 && used + name.length + 2 > TABLE_LABEL_BUDGET) break
    used += name.length + 2
    shown.push(name)
  }
  return { shown, hidden: names.length - shown.length }
}

function TablesChip({ names }: { names: string[] }) {
  const [open, setOpen] = useState(false)
  const { shown, hidden } = splitTableLabel(names)
  if (hidden === 0) return <Chip>tables: {names.join(', ')}</Chip>

  return (
    <button
      onClick={() => setOpen(!open)}
      aria-expanded={open}
      title={open ? 'Show fewer' : names.join(', ')}
      style={{
        minWidth: 0,
        maxWidth: '100%',
        padding: 0,
        border: 'none',
        background: 'transparent',
        font: 'inherit',
        textAlign: 'left',
        cursor: 'pointer',
      }}
    >
      <Chip wrap={open}>
        tables: {open ? names.join(', ') : `${shown.join(', ')} +${hidden} more`}
      </Chip>
    </button>
  )
}

export function RunMetadata({ run }: { run: RunDetail }) {
  const tables = run.queries.at(-1)?.referenced_tables ?? []
  const chips: React.ReactNode[] = []

  if (tables.length > 0) {
    chips.push(
      <TablesChip key="tables" names={tables.map((t) => t.split('.').pop() ?? t)} />,
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
    <div
      style={{
        display: 'flex',
        gap: 6,
        flexWrap: 'wrap',
        alignItems: 'center',
        minWidth: 0,
        maxWidth: '100%',
      }}
    >
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

/**
 * The readings offered when a run stopped to ask rather than guess.
 *
 * Chips, not a form: the question is already the assistant's message, so this
 * is only the shortcut. Picking one sends it as the next message, and typing
 * an answer instead works exactly as it always did — which is why nothing here
 * blocks the composer or marks the turn as unfinished.
 */
export function ClarificationOptions({
  spec, onPick, disabled,
}: {
  spec: ClarificationSpec
  onPick: (text: string) => void
  disabled?: boolean
}) {
  if (!spec.options?.length) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 2 }}>
      <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        Pick one, or just say it in your own words.
      </span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {spec.options.map((option) => (
          <OptionChip
            key={option}
            text={option}
            disabled={disabled}
            onClick={() => onPick(option)}
          />
        ))}
      </div>
    </div>
  )
}

function OptionChip({
  text, onClick, disabled,
}: {
  text: string
  onClick: () => void
  disabled?: boolean
}) {
  const [hover, setHover] = useState(false)
  const lit = hover && !disabled
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        fontSize: 12.5,
        fontWeight: 500,
        textAlign: 'left',
        color: lit ? 'var(--text-strong)' : 'var(--text-dim)',
        background: lit ? 'var(--panel-hover)' : 'var(--panel)',
        border: `1px solid ${lit ? 'var(--accent-border)' : 'var(--border)'}`,
        padding: '8px 13px',
        borderRadius: 20,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'background .12s ease, border-color .12s ease, color .12s ease',
      }}
    >
      {text}
    </button>
  )
}

// ── assistant turn ────────────────────────────────────────────────────────
export function AssistantTurn({
  text, run, streaming, onPickOption, optionsDisabled,
}: {
  text: string
  run: RunDetail | null
  streaming?: boolean
  onPickOption?: (text: string) => void
  optionsDisabled?: boolean
}) {
  const table = run?.artifacts.find((a) => a.kind === 'TABLE')
  const spec = table?.spec as TableArtifactSpec | undefined
  const chart = run?.artifacts.find((a) => a.kind === 'CHART')
  const chartSpec = chart?.spec as Record<string, unknown> | undefined
  const kpi = run?.artifacts.find((a) => a.kind === 'KPI')
  const kpiSpec = kpi?.spec as unknown as KpiSpec | undefined
  const clarification = run?.artifacts.find((a) => a.kind === 'CLARIFICATION')
  const clarifySpec = clarification?.spec as unknown as ClarificationSpec | undefined

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

      {clarifySpec && onPickOption && (
        <ClarificationOptions
          spec={clarifySpec}
          onPick={onPickOption}
          disabled={optionsDisabled}
        />
      )}

      {/*
        No client-side fallback chart. "No chart" is a decision the `chart`
        node makes about the data — a single row, a measure identical in every
        row, more categories than a reader can compare — and it carries a
        reason into the step trail. A second renderer here that drew bars
        anyway would silently overrule it, which is how a result the pipeline
        called unchartable ended up as a wall of equal-length bars sitting
        under an answer that said they were all tied.
      */}
      {chartSpec && <VegaChart spec={chartSpec} />}
      {/* The other half of that decision. A single-row result is refused as a
          chart for a good reason and is the shape a KPI is made of, so the
          `chart` node answers it with a number instead of nothing. Never both:
          the node reaches for one only where it declined the other. */}
      {kpiSpec && (
        <div
          style={{
            marginTop: 6,
            padding: '18px 10px',
            border: '1px solid var(--border)',
            borderRadius: 10,
            background: 'var(--panel)',
          }}
        >
          <Kpi spec={kpiSpec} />
        </div>
      )}
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
