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
import { Fragment, memo, useEffect, useMemo, useRef, useState } from 'react'
import { runs } from '../api/client'
import { formatAnswer } from './chat-format'
import { thoughtTime } from './thinking'
import type { ThinkingState } from './thinking'
import type {
  Artifact, ChartOption, ClarificationSpec, GeneratedQuery, KpiSpec, RunDetail,
  RunKnowledge, RunStep, TableArtifactSpec,
} from '../api/types'
import { ChartGlyph, ChartTypePicker } from './chart-picker'
import {
  ActionDivider, Chip, CopyButton, Dot, Icon, Kpi, PrimaryButton, QuietAction,
  ResultTable, Spinner, TextArea, dirOf,
} from './ui'
import { VegaChart } from './VegaChart'
import { NODE_META } from '../theme/tokens'

// ── turn frame ────────────────────────────────────────────────────────────
function AssistantAvatar({
  busy, failed, stopped,
}: {
  busy?: boolean
  failed?: boolean
  /** The reader ended this one. Neutral, not red — see `RunStoppedCard`. */
  stopped?: boolean
}) {
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
        background: failed
          ? 'var(--red-bg)'
          : stopped ? 'var(--panel-alt)' : 'var(--accent-bg)',
        border: `1px solid ${
          failed ? 'var(--red-border)' : stopped ? 'var(--border)' : 'var(--accent-border)'
        }`,
        marginTop: 1,
      }}
    >
      {busy ? (
        <Spinner size={14} />
      ) : failed ? (
        <Icon.Alert size={15} stroke="var(--red)" />
      ) : stopped ? (
        <Icon.Stop size={12} stroke="var(--text-faint)" />
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
export const UserBubble = memo(function UserBubble({ text }: { text: string }) {
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
})

// ── step chips ────────────────────────────────────────────────────────────
/** A step's own time, in the unit a reader can hold: `840ms`, then `4.2s`. */
function stepTime(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

/**
 * How long the step that is running now has been running.
 *
 * The trail shows a duration on every step **except** the one you are waiting
 * for, which is the only one anybody wonders about. `route` is a model call
 * and this product has measured it at 0.9s and at 27s on the same thread with
 * the same model, so "a while with no number on it" is a real state a reader
 * lands in, and a chip that has been spinning silently for half a minute is
 * indistinguishable from a chip that is stuck.
 *
 * It stays quiet below `SLOW_STEP_MS`: an ordinary step finishes before the
 * counter appears, so the trail is unchanged for every run that is behaving,
 * and the number arrives exactly when it starts being the answer to a
 * question. Timed from when this client first saw the step, which is what the
 * reader is measuring anyway.
 */
const SLOW_STEP_MS = 2000

function useElapsed(key: number | undefined): number {
  const [now, setNow] = useState(0)
  const startedAt = useRef<number | null>(null)

  useEffect(() => {
    if (key === undefined) {
      startedAt.current = null
      setNow(0)
      return
    }
    startedAt.current = Date.now()
    setNow(0)
    // One second, and rendered as whole seconds: a counter that ticks tenths
    // is a stopwatch, and a stopwatch on a screen someone is waiting at makes
    // the wait the subject.
    const timer = setInterval(() => {
      if (startedAt.current !== null) setNow(Date.now() - startedAt.current)
    }, 1000)
    return () => clearInterval(timer)
  }, [key])

  return now
}

export function StepTrail({
  steps, interrupted,
}: {
  steps: RunStep[]
  /**
   * The run ended while a step was still RUNNING — a stop, and nothing else
   * gets here. Without this the node the reader interrupted keeps a spinner
   * and an accent border for as long as the thread exists, which says the
   * work is still going on a turn that says it is over.
   */
  interrupted?: boolean
}) {
  const waitingOn = steps.find((s) => s.status === 'RUNNING' && !interrupted)
  const elapsed = useElapsed(waitingOn?.seq)
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
        const running = step.status === 'RUNNING' && !interrupted
        const stopped = step.status === 'RUNNING' && interrupted
        const failed = step.status === 'FAILED'
        const skipped = step.status === 'SKIPPED' || stopped

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
            className="rm-chip-in"
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
            {stopped && (
              <span style={{ color: 'var(--text-faint)' }}>stopped</span>
            )}
            {/*
              Every finished step shows its own time, not only the ones that
              ended DONE. A step that ran for forty seconds and then reported
              SKIPPED — `clarify` failing open on a provider timeout is the
              real case — spent that time whatever it concluded, and hiding it
              made the slowest node in the run look like the one that never
              ran.
            */}
            {step.duration_ms != null && !running && (
              <span style={{ color: 'var(--text-faint)' }}>
                {stepTime(step.duration_ms)}
              </span>
            )}
            {running && elapsed >= SLOW_STEP_MS && (
              <span
                style={{ color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums' }}
              >
                {Math.floor(elapsed / 1000)}s
              </span>
            )}
          </span>
        )
      })}
    </div>
  )
}

/**
 * The step trail and the line above it — from the first step to the finished
 * answer, as one thing.
 *
 * Showing route → retrieve → generate → validate → execute → present, each
 * with its own timing, is how a reader can see that an answer went through a
 * validated pipeline rather than a single model call. That evidence is the
 * product's argument for itself, so it is not hidden behind a disclosure;
 * the toggle only exists for anyone who wants the transcript quieter.
 *
 * One component for both states, deliberately. The live trail and the finished
 * one used to be two — a thinking card while the run worked, a summary once it
 * had persisted — and the swap happened at the exact moment the first token
 * arrived: the chips for route, retrieve, clarify and the rest vanished as the
 * answer started writing itself, which is precisely when the reader is
 * watching them. Now only the header line changes, and the chips below it are
 * the same element throughout — fed by live events, then by the persisted run.
 */
function StepPanel({
  steps, streaming, totalMs,
}: {
  steps: RunStep[]
  /** The run is still in flight, so the header names what it is doing now. */
  streaming?: boolean
  /** The run's own measured latency, once it has one. */
  totalMs?: number | null
}) {
  const [open, setOpen] = useState(true)
  if (steps.length === 0) return null

  const failed = steps.some((s) => s.status === 'FAILED')
  const active = steps.find((s) => s.status === 'RUNNING')
  const total = totalMs ?? steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0)
  const seconds = (total / 1000).toFixed(total < 1000 ? 2 : 1)

  /** One line of truth for what the run is doing, so the visible label and the
   *  spoken announcement below cannot drift apart. */
  const status = streaming
    ? active
      ? (NODE_META[active.name]?.detail ?? 'Working…')
      : 'Starting…'
    : failed
      ? `Stopped after ${steps.length} steps · ${seconds}s`
      : `All ${steps.length} steps passed · ${seconds}s`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* The visible copy of this lives inside the toggle below, where a live
          region would make a screen reader re-announce the whole control every
          time the step changed. Announcing it here instead gives one polite
          update per pipeline step — the trail a sighted user is watching. */}
      <span className="rm-sr" role="status">{status}</span>
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
          color: streaming
            ? 'var(--text-dim)'
            : failed
              ? 'var(--red)'
              : 'var(--green)',
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={12} stroke="currentColor" />
        {streaming ? <span className="rm-pulse">{status}</span> : status}
      </button>
      {open && <StepTrail steps={steps} />}
    </div>
  )
}

// ── the model thinking out loud ───────────────────────────────────────────
/**
 * A line that says the model is working, and how long it has been at it.
 *
 * The problem it solves is not decoration. A reasoning model streams its
 * scratchpad on a channel separate from its answer, and the chat rendered
 * none of it: a step chip sat on `describe` with an empty paragraph under it
 * for as long as the model wanted to think, which is indistinguishable from
 * a crash. The elapsed count is the load-bearing part — it is the one thing
 * on screen that can only be true if something is still happening.
 *
 * Collapsed by default. The thought is genuinely interesting once and then
 * never again, and a transcript that unrolls hundreds of words of
 * deliberation above every answer is a worse transcript. Opening it is one
 * click, and what is inside is the raw channel, unedited.
 */
function ThinkingPanel({ thinking }: { thinking: ThinkingState }) {
  const [open, setOpen] = useState(false)
  const [, tick] = useState(0)
  const bodyRef = useRef<HTMLDivElement>(null)
  // When *this reader* started watching. The server's `ms` is authoritative
  // but only lands every few hundred milliseconds; this is what lets the
  // number move once a second in between.
  const startedAt = useRef(Date.now())

  useEffect(() => {
    if (thinking.done) return
    const id = window.setInterval(() => tick((n) => n + 1), 1000)
    return () => window.clearInterval(id)
  }, [thinking.done])

  // Pinned to the newest line, and faded at the top only when there is
  // something scrolled off above it.
  useEffect(() => {
    const el = bodyRef.current
    if (!el || !open) return
    el.scrollTop = el.scrollHeight
    el.dataset.faded = String(el.scrollHeight > el.clientHeight + 4)
  }, [open, thinking.text])

  // Never runs backwards: whichever of the two clocks is further along wins
  // while it is live, and the server's total is the one that is kept.
  const elapsed = thinking.done
    ? thinking.ms
    : Math.max(thinking.ms, Date.now() - startedAt.current)
  const label = thinking.done
    ? `Thought for ${thoughtTime(elapsed)}`
    : `Thinking… ${thoughtTime(elapsed)}`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {/* Announced once per state, not once per second: a live region on a
          ticking clock would read the whole label out every tick. */}
      <span className="rm-sr" role="status">
        {thinking.done ? label : 'The model is thinking.'}
      </span>
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
          fontVariantNumeric: 'tabular-nums',
          color: 'var(--text-dim)',
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <Icon.Chevron open={open} size={12} stroke="currentColor" />
        <span className={thinking.done ? undefined : 'rm-think'}>{label}</span>
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="rm-think-body rm-enter"
          style={{
            maxHeight: 132,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'var(--panel-alt)',
            fontSize: 12.5,
            lineHeight: 1.6,
            // 4.95:1 on this ground in dark, 5.38:1 in light. `--text-faint`
            // is the colour this wants to be and cannot be read at.
            color: 'var(--text-dim)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {thinking.text}
        </div>
      )}
    </div>
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
      className="rm-artifact"
      style={{
        border: '1px solid var(--border)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--code-bg)',
        animationDelay: '.09s',
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
  // Only where a query ran. A schema question is answered from the snapshot
  // and never reaches the database, so this reads 0 — and a lone "0ms" chip
  // under an answer claims a database round trip that did not happen.
  if (run.db_latency_ms != null && run.queries.length > 0) {
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
      className="rm-artifact"
      style={{
        display: 'flex',
        gap: 6,
        flexWrap: 'wrap',
        alignItems: 'center',
        minWidth: 0,
        maxWidth: '100%',
        animationDelay: '.12s',
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
export const RunErrorCard = memo(function RunErrorCard({ run }: { run: RunDetail }) {
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
})

/**
 * A run the reader stopped.
 *
 * Not `RunErrorCard`. A cancelled run used to take the same red panel as a
 * failure — *"This run did not complete"* — which told someone who had just
 * pressed Stop that something had gone wrong, in the colour this product
 * reserves for a database refusing a statement. Nothing went wrong: they
 * changed their mind, which is the one terminal state that owes no
 * explanation.
 *
 * So it keeps what the run did manage — the trail, and the SQL if it got that
 * far, both persisted and both worth reading — and offers the question back.
 * There is no partial answer to keep: the text streamed before the stop was
 * never written down, and showing it here would mean showing something that
 * vanishes on the next reload.
 */
export const RunStoppedCard = memo(function RunStoppedCard({
  run, onRetry,
}: {
  run: RunDetail
  /** Run the same question again, against this same message. */
  onRetry?: (run: RunDetail) => void
}) {
  return (
    <Turn avatar={<AssistantAvatar stopped />}>
      {/* Sentence and action on one line, the action **next to** the words it
          answers. It was pinned to the far right of a full-width panel, which
          put a hairline of a button as far from its own sentence as the layout
          allowed and made a recovery look like an afterthought. */}
      <div
        style={{
          display: 'inline-flex',
          alignSelf: 'flex-start',
          alignItems: 'center',
          gap: 4,
          flexWrap: 'wrap',
          maxWidth: '100%',
          padding: '7px 8px 7px 12px',
          borderRadius: 999,
          border: '1px solid var(--border)',
          background: 'var(--panel)',
          fontSize: 12.5,
          color: 'var(--text-dim)',
        }}
      >
        <Icon.Stop size={10} stroke="var(--text-faint)" />
        <span style={{ padding: '0 8px 0 4px' }}>You stopped this answer.</span>
        {onRetry && (
          <QuietAction
            tone="accent"
            onClick={() => onRetry(run)}
            title="Run this question again"
          >
            <Icon.Refresh size={13} />
            Retry
          </QuietAction>
        )}
      </div>
      {run.steps.length > 0 && <StepTrail steps={run.steps} interrupted />}
      {run.queries.length > 0 && <SqlPanel queries={run.queries} />}
    </Turn>
  )
})

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
    <div
      className="rm-artifact"
      style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 2 }}
    >
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

// ── the chart, and changing it ────────────────────────────────────────────
/**
 * A turn's chart with a "change chart" control under it.
 *
 * The pipeline picked this type from the question and the data, and it is
 * usually right — so the control is closed by default and opens on request,
 * rather than putting nine tiles under every answer. What it does not do is
 * offer a type this result cannot carry: the backend returns a verdict per
 * type alongside the redrawn spec, and the grid greys the rest with the reason.
 *
 * **Nothing here is saved.** A transcript records what a run produced;
 * rewriting yesterday's chart artifact because someone flipped a picker today
 * would leave the step trail beside it ("bar chart (model)") describing a
 * chart that is no longer there. The redraw lives as long as the reader is
 * looking at it, and a reload brings back what the run actually produced.
 */
function ChatChart({ runId, spec }: { runId: string; spec: Record<string, unknown> }) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<ChartOption[]>([])
  const [redrawn, setRedrawn] = useState<Record<string, unknown> | null>(null)
  const [type, setType] = useState<string>(
    () => (spec.usermeta as { datamind?: { chart_type?: string } } | undefined)
      ?.datamind?.chart_type ?? '',
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function choose(next: string) {
    if (next === type) return
    setBusy(true)
    setError(null)
    try {
      const result = await runs.redrawChart(runId, next)
      setOptions(result.options)
      if (result.spec) {
        setRedrawn(result.spec)
        setType(result.chart_type)
      } else {
        // Only reachable if the verdicts on screen are older than the data —
        // the grid disables what cannot work. Say so rather than doing nothing.
        setError(result.reason)
      }
    } catch {
      setError('The chart could not be redrawn.')
    } finally {
      setBusy(false)
    }
  }

  // Opening asks for the verdicts once, using the type already on screen: the
  // response carries the options, so there is no separate "what fits" call.
  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && options.length === 0 && type) {
      setBusy(true)
      try {
        setOptions((await runs.redrawChart(runId, type)).options)
      } catch {
        /* the picker simply stays unfiltered */
      } finally {
        setBusy(false)
      }
    }
  }

  return (
    <div
      className="rm-artifact"
      style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
    >
      <VegaChart spec={redrawn ?? spec} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button
          type="button"
          onClick={() => void toggle()}
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
          {open ? 'Done' : 'Change chart'}
        </button>
        {busy && <Spinner size={12} />}
        {error && (
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{error}</span>
        )}
      </div>
      {open && (
        <div style={{ maxWidth: 560 }}>
          <ChartTypePicker
            value={type}
            options={options}
            includeAuto={false}
            columns={8}
            onChange={(next) => void choose(next)}
          />
        </div>
      )}
    </div>
  )
}


// ── the answer's prose ────────────────────────────────────────────────────
/**
 * The answer, with the markdown a model writes anyway drawn rather than
 * printed. `chat-format.ts` decides what the text means; this decides only
 * what each piece looks like.
 *
 * The container above keeps `pre-wrap`, so the newlines rejoined here and
 * every run of spaces land exactly where they used to — this changes how
 * three constructs are painted and nothing about the layout. Spans become
 * elements with text children; **no string here is ever interpreted as
 * markup**, which is what makes rendering a model's output safe by
 * construction rather than by sanitising.
 */
function AnswerText({ text }: { text: string }) {
  // An answer streams, so this runs on every flush of new tokens over the
  // whole text so far — cheap, but not free, and `memo` on the turn means the
  // other turns are not paying it too.
  const lines = useMemo(() => formatAnswer(text), [text])
  return (
    <>
      {lines.map((spans, line) => (
        <Fragment key={line}>
          {line > 0 && '\n'}
          {spans.map((span, i) => {
            if (span.kind === 'strong') {
              return <strong key={i} style={{ fontWeight: 650 }}>{span.text}</strong>
            }
            if (span.kind === 'code') {
              return (
                <code
                  key={i}
                  style={{
                    fontSize: '0.92em',
                    padding: '1px 5px',
                    borderRadius: 5,
                    background: 'var(--code-bg)',
                    color: 'var(--code-text)',
                  }}
                >
                  {span.text}
                </code>
              )
            }
            return <Fragment key={i}>{span.text}</Fragment>
          })}
        </Fragment>
      ))}
    </>
  )
}

// ── assistant turn ────────────────────────────────────────────────────────
/**
 * One turn, live or persisted.
 *
 * `memo` is not an optimisation detail here, it is what keeps a long
 * transcript still while an answer streams: the page re-renders on every
 * flush of new tokens, and without this every earlier turn — its table, its
 * chart, its SQL — re-rendered with it. That needs `onPickOption` to keep its
 * identity between renders, which is why `ChatPage` holds it in a `useCallback`
 * rather than writing an arrow in the JSX.
 */
/**
 * The most consequential twenty pixels in the feature.
 *
 * Three tiers, and **the most important decision here is that "Generated" is
 * not a warning.** It is the default path, it is most answers, and dressing it
 * in amber would train every reader to ignore amber within a week. Verified
 * *earns* a chip; Generated gets an honest sentence. Presence versus absence
 * carries the hierarchy — not a traffic light.
 *
 * Two things ride on a Verified badge and neither is optional:
 *
 *  - **the matched question**, which is the reader's only defence against a
 *    confident wrong match, and costs one line;
 *  - **the bound parameters**, which answer the next question a suspicious
 *    reader has — *did it think July or June?* Power BI shows the matched
 *    trigger phrase; showing the bindings as well is a small addition nobody
 *    else makes.
 *
 * And the way out. A reader who does not believe the match gets *Generate a
 * fresh answer instead*, one click, which is what makes showing the badge safe
 * at all — and what makes the override rate a measured number rather than an
 * anecdote.
 *
 * The badge never animates. A badge that draws attention to itself is a badge
 * nobody trusts.
 */
function AnswerBadge({
  knowledge, onRegenerate,
}: {
  knowledge: RunKnowledge
  /** Absent while the run is still streaming, or once already overridden. */
  onRegenerate?: () => void
}) {
  if (knowledge.tier === 'GENERATED') {
    return (
      <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
        Generated against the bare schema.
      </div>
    )
  }

  if (knowledge.tier === 'GROUNDED') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Chip tone="accent">◆ Grounded</Chip>
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          every table it used is described in your semantic layer
        </span>
      </div>
    )
  }

  const bindings = Object.entries(knowledge.bound_params)
  return (
    <div
      style={{
        display: 'flex', flexDirection: 'column', gap: 5,
        padding: '9px 12px', borderRadius: 9,
        border: '1px solid var(--green-border)', background: 'var(--green-bg)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--green)', fontSize: 12, fontWeight: 700 }}>
          ✓ Verified
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
          answered from a saved question
        </span>
      </div>
      {knowledge.question && (
        <div
          dir={dirOf(knowledge.question)}
          style={{ fontSize: 12, color: 'var(--text)' }}
        >
          &ldquo;{knowledge.question}&rdquo;
          {bindings.length > 0 && (
            <span style={{ color: 'var(--text-faint)' }}>
              {'  ·  '}
              {bindings.map(([name, value]) => `${name}=${value}`).join(', ')}
            </span>
          )}
        </div>
      )}
      {onRegenerate && !knowledge.overridden && (
        <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          Not what you asked?{' '}
          <button
            type="button"
            onClick={onRegenerate}
            style={{
              background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              font: 'inherit', color: 'var(--accent)', textDecoration: 'underline',
            }}
          >
            Generate a fresh answer instead
          </button>
        </div>
      )}
      {knowledge.overridden && (
        <div style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          You asked for a fresh answer to this one.
        </div>
      )}
    </div>
  )
}

/**
 * Everything you can do *to* a finished answer, on one line.
 *
 * It used to be two rows of bordered buttons — copy and teach on one, then
 * *Was this right?* with three more underneath — drawn in the brightest ink
 * the theme has. Five boxes under every answer read louder than the sentence
 * they were judging, and the transcript became a stack of forms again.
 *
 * So: one row, `QuietAction` throughout, faint until reached for. The two
 * groups either side of the hairline are genuinely different asks — *use this
 * answer* on the left, *judge this answer* on the right — and the divider is
 * what lets them share a line without reading as one menu of five things.
 *
 * The row is always present rather than revealed on hover, unlike the controls
 * above it. Feedback nobody can see is feedback nobody gives, and hiding the
 * one control that asks whether an answer was right is how a learning loop
 * quietly stops learning. At this weight it costs the page nothing.
 */
function AnswerActions({
  text, run, onFeedback, onSaveAsTemplate,
}: {
  text: string
  run: RunDetail | null
  onFeedback?: (run: RunDetail, verdict: string, comment: string) => Promise<void>
  onSaveAsTemplate?: (run: RunDetail) => void
}) {
  const [pending, setPending] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const given = run?.knowledge.feedback

  async function submit(verdict: string, note = '') {
    if (!run || !onFeedback) return
    setBusy(true)
    try {
      await onFeedback(run, verdict, note)
      setPending(null)
      setComment('')
    } finally {
      setBusy(false)
    }
  }

  const canTeach = onSaveAsTemplate && run && run.queries.length > 0
  const canJudge = Boolean(onFeedback && run)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 1,
          rowGap: 2,
          // The first control's own padding, pulled back so its *label* lines
          // up with the answer above rather than its invisible hit area.
          marginLeft: -7,
        }}
      >
        <CopyButton text={text} />
        {/* One click from an answer that worked to the knowledge that keeps it
            working. The editor opens prefilled with the question and the
            statement the reader just watched succeed, which is the whole
            reason this belongs here rather than on the Knowledge tab. */}
        {canTeach && (
          <QuietAction
            tone="accent"
            onClick={() => onSaveAsTemplate!(run!)}
            title="Teach this question, prefilled with the SQL that answered it"
          >
            <Icon.Sparkle size={13} />
            Save as template
          </QuietAction>
        )}
        {canJudge && <ActionDivider />}
        {/* The verdict travels as one group, so a column too narrow for the
            whole row breaks *between* the two asks rather than through the
            middle of one — "Yes" stranded on the line above "No" is a choice
            that no longer looks like a choice. */}
        {canJudge && (
          <span
            // The verdict is replaced in place by its receipt, so a reader who
            // is not watching this corner of the page is told that it landed.
            aria-live="polite"
            style={{
              display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 1,
              rowGap: 2, minWidth: 0,
            }}
          >
          {given ? (
            <FeedbackReceipt given={given} />
          ) : (
            <>
              <span
                style={{
                  fontSize: 11.5,
                  color: 'var(--text-dim)',
                  padding: '0 6px 0 1px',
                }}
              >
                Was this right?
              </span>
              {/* The only verdict that submits on the click itself: there is
                  nothing to ask someone who says the answer was right. */}
              <QuietAction
                tone="green"
                disabled={busy}
                onClick={() => void submit('CORRECT')}
              >
                {busy ? <Spinner size={13} /> : <Icon.Check size={13} />}
                Yes
              </QuietAction>
              <QuietAction
                tone="red"
                disabled={busy}
                active={pending === 'WRONG'}
                onClick={() => setPending(pending === 'WRONG' ? null : 'WRONG')}
              >
                <Icon.Close size={13} />
                No
              </QuietAction>
              {/* Three verdicts, not two. Genie's *Yes / Fix it / Request
                  review* split exists because "this is wrong" and "please look
                  at this" are different asks: one is a correction the reader
                  could make themselves, the other is a question they cannot
                  answer. Collapsing them loses the second. */}
              <QuietAction
                tone="accent"
                disabled={busy}
                active={pending === 'NEEDS_REVIEW'}
                onClick={() =>
                  setPending(pending === 'NEEDS_REVIEW' ? null : 'NEEDS_REVIEW')
                }
              >
                <Icon.Flag size={13} />
                Ask for review
              </QuietAction>
            </>
          )}
          </span>
        )}
      </div>

      {/* `✗ No` and `Ask for review` expand one note **in place**: no toast, no
          dialog, no confetti — and no second modal over a transcript people are
          reading. */}
      {pending && (
        <FeedbackNote
          verdict={pending}
          value={comment}
          busy={busy}
          onChange={setComment}
          onSend={() => void submit(pending, comment)}
          onCancel={() => {
            setPending(null)
            setComment('')
          }}
        />
      )}
    </div>
  )
}

/**
 * The note that opens under `No` and `Ask for review`.
 *
 * A titled card rather than a bare textarea with two buttons loose under it.
 * The two verdicts ask for different things — one is a correction, the other a
 * question for a person — and the heading is where that difference is said,
 * so the placeholder does not have to carry it alone.
 */
function FeedbackNote({
  verdict, value, busy, onChange, onSend, onCancel,
}: {
  verdict: string
  value: string
  busy: boolean
  onChange: (value: string) => void
  onSend: () => void
  onCancel: () => void
}) {
  const wrong = verdict === 'WRONG'
  const tone = wrong ? 'red' : 'accent'
  return (
    <div
      className="rm-enter"
      style={{
        maxWidth: 520,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        padding: 12,
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: 'var(--panel)',
      }}
    >
      <div style={{ display: 'flex', gap: 9, alignItems: 'flex-start' }}>
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 22,
            height: 22,
            flexShrink: 0,
            borderRadius: 6,
            background: `var(--${tone}-bg)`,
            color: `var(--${tone})`,
          }}
        >
          {wrong ? <Icon.Close size={13} /> : <Icon.Flag size={13} />}
        </span>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
            {wrong ? 'What was wrong?' : 'Ask someone to look at this'}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>
            {wrong
              ? 'Optional — but it is what the next person reads.'
              : 'This goes to whoever owns the connection.'}
          </div>
        </div>
      </div>
      <TextArea
        autoFocus
        value={value}
        // An example rather than the heading again: the heading asks the
        // question, so the box is free to show what an answer looks like.
        placeholder={
          wrong
            ? 'It counted refunded orders…'
            : 'What should someone look at?'
        }
        onChange={(e) => onChange(e.target.value)}
        // Enter sends, Shift+Enter breaks the line: the composer above already
        // taught that, and a note this short is not worth a trip to the mouse.
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            onSend()
          }
        }}
        style={{ minHeight: 62, fontSize: 12.5 }}
      />
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
        <QuietAction onClick={onCancel} disabled={busy}>
          Cancel
        </QuietAction>
        <PrimaryButton
          onClick={onSend}
          disabled={busy}
          style={{ fontSize: 12, padding: '7px 15px', borderRadius: 7 }}
        >
          {busy && <Spinner size={12} />}
          Send
        </PrimaryButton>
      </div>
    </div>
  )
}

/**
 * What happened to a verdict already given.
 *
 * And once a flag becomes a template, this is where the person who raised it
 * finds out — the one thing that keeps a feedback control from becoming a
 * suggestion box people learn to ignore.
 */
function FeedbackReceipt({ given }: { given: NonNullable<RunKnowledge['feedback']> }) {
  const view = given.became_template
    ? {
        icon: <Icon.Sparkle size={13} />,
        tone: 'var(--accent)',
        text: 'Your flag became a saved question — this will be answered from it next time.',
      }
    : given.state === 'DISMISSED'
      ? {
          icon: <Icon.Check size={13} />,
          tone: 'var(--text-faint)',
          text: `Reviewed — ${given.resolution_note}`,
        }
      : given.verdict === 'CORRECT'
        ? {
            icon: <Icon.Check size={13} />,
            tone: 'var(--green)',
            text: 'Thanks — noted as correct.',
          }
        : {
            icon: <Icon.Flag size={13} />,
            tone: 'var(--accent)',
            // Whose queue, when the server named one. §4.6 asks the control to
            // say a flag goes to whoever owns the connection; the name comes
            // from the server so it stays true when ownership moves, where
            // prose baked in here would quietly start lying.
            text: given.routed_to
              ? `Thanks — this is in ${given.routed_to}’s review queue.`
              : 'Thanks — this is in the review queue.',
          }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        gap: 6,
        padding: '4px 2px',
        fontSize: 11.5,
        color: 'var(--text-dim)',
        minWidth: 0,
      }}
    >
      <span style={{ color: view.tone, alignSelf: 'center', display: 'flex' }}>
        {view.icon}
      </span>
      {view.text}
    </span>
  )
}

export const AssistantTurn = memo(function AssistantTurn({
  text, run, streaming, steps, thinking, preview, onPickOption, optionsDisabled,
  onRegenerate, onFeedback, onSaveAsTemplate,
}: {
  text: string
  run: RunDetail | null
  streaming?: boolean
  /**
   * The trail for a run still in flight, assembled from live events. A
   * finished turn has no use for it and reads `run.steps` instead — same
   * chips, from the persisted record.
   */
  steps?: RunStep[]
  /**
   * The model's reasoning channel, while it has one. Null for the models that
   * do not think out loud — which is most of them — and null on a finished
   * turn, since the events it is built from are never stored.
   */
  thinking?: ThinkingState | null
  /**
   * The result of the query this run just ran, live — before the run has
   * finished and before anything has been persisted. `execute` publishes it
   * the moment it has the rows, which on a normal run is some twenty seconds
   * before `present` has finished writing the sentence about them. Only ever
   * set on a streaming turn; a finished one reads its TABLE artifact.
   */
  preview?: TableArtifactSpec | null
  onPickOption?: (text: string) => void
  optionsDisabled?: boolean
  /** Records the override, then re-asks the same question without the store. */
  onRegenerate?: (run: RunDetail) => void
  /** *Was this right?* Absent while streaming and on a failed run. */
  onFeedback?: (run: RunDetail, verdict: string, comment: string) => Promise<void>
  /** Opens the template editor prefilled with this answer's question and SQL. */
  onSaveAsTemplate?: (run: RunDetail) => void
}) {
  const table = run?.artifacts.find((a) => a.kind === 'TABLE')
  const spec = table?.spec as TableArtifactSpec | undefined
  const chart = run?.artifacts.find((a) => a.kind === 'CHART')
  const chartSpec = chart?.spec as Record<string, unknown> | undefined
  const kpi = run?.artifacts.find((a) => a.kind === 'KPI')
  const kpiSpec = kpi?.spec as unknown as KpiSpec | undefined
  const clarification = run?.artifacts.find((a) => a.kind === 'CLARIFICATION')
  const clarifySpec = clarification?.spec as unknown as ClarificationSpec | undefined
  const trail = steps ?? run?.steps ?? []

  return (
    <Turn avatar={<AssistantAvatar busy={streaming} />}>
      <StepPanel
        steps={trail}
        streaming={streaming}
        totalMs={run?.total_latency_ms}
      />

      {/* Under the trail and above the answer, which is where it happens: the
          step panel names the node that is working, this says the model
          inside it is still going, and then the answer arrives. */}
      {thinking && <ThinkingPanel thinking={thinking} />}

      {/* Above the answer, not below it: what a reader most needs to know
          about a sentence is whether to believe it, and that has to arrive
          before the sentence does. Never while streaming — a badge that
          appeared and then changed tier mid-answer would be worse than none. */}
      {!streaming && run && (
        <AnswerBadge
          knowledge={run.knowledge}
          onRegenerate={onRegenerate ? () => onRegenerate(run) : undefined}
        />
      )}

      {/* Nothing written yet: the panel's header is already saying which node
          is working, and an empty paragraph with a caret in it under that just
          moves the layout twice. */}
      {(text.length > 0 || !streaming) && (
      <div
        dir={dirOf(text)}
        style={{
          fontSize: 14.5,
          lineHeight: 1.65,
          color: 'var(--text)',
          whiteSpace: 'pre-wrap',
        }}
      >
        <AnswerText text={text} />
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
      )}

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
      {chartSpec && run && <ChatChart runId={run.id} spec={chartSpec} />}
      {/* The other half of that decision. A single-row result is refused as a
          chart for a good reason and is the shape a KPI is made of, so the
          `chart` node answers it with a number instead of nothing. Never both:
          the node reaches for one only where it declined the other. */}
      {kpiSpec && (
        <div
          className="rm-artifact"
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
      {/* The artifacts under an answer arrive together — the run persisted them
          in one go — so they are staggered by a few frames each rather than
          landing as one block. Small on purpose: this is the difference between
          a result appearing and a result being dumped, not an effect. */}
      {spec && (
        <div className="rm-artifact" style={{ animationDelay: '.06s' }}>
          <ResultTable spec={spec} />
        </div>
      )}
      {/* The same table, twenty seconds earlier. It sits exactly where the
          persisted one will, so the swap at the end of the run moves nothing
          on the page — the rows are simply already there. */}
      {!spec && preview && (
        <div className="rm-artifact">
          <ResultTable spec={preview} />
        </div>
      )}
      {run && run.queries.length > 0 && <SqlPanel queries={run.queries} />}
      {run && <RunMetadata run={run} />}

      {/* Copy, teach and judge on one quiet line — see `AnswerActions`. */}
      {!streaming && text && (
        <AnswerActions
          text={text}
          run={run}
          onFeedback={onFeedback}
          onSaveAsTemplate={onSaveAsTemplate}
        />
      )}
    </Turn>
  )
})
