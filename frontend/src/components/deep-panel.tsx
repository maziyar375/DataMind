/**
 * The deep turn: the plan the reader watches, and the answer's footnotes.
 *
 * docs/plans/deep-analysis-mode.md §4. For the minutes a deep run takes, this
 * panel *is* the product — the restatement first (the cheapest place to catch a
 * misread question), then the steps with the running one marked, finished ones
 * opening onto their statement and what was computed from it. A replaced step
 * is shown **as a replacement**, struck through above what took its place:
 * hiding revisions would make the plan look prescient.
 *
 * *Answer now* is the panel's primary control, not a twin of the composer's
 * stop button, and the copy says why: stop throws the run away, this keeps what
 * it has found. Every state is a glyph and a word, never colour alone.
 *
 * All the arithmetic is `deep-plan.ts`; this file only draws it.
 */
import { useState } from 'react'
import {
  answerSpans,
  budgetLine,
  canAnswerNow,
  INTENT_LABEL,
  stepRows,
  stopSentence,
  TOOL_LABEL,
  type DeepClaim,
  type DeepView,
  type RowState,
  type StepRow,
} from './deep-plan'
import { dirOf, Icon, PrimaryButton, Spinner } from './ui'

const STATE: Record<RowState, { glyph: string; word: string; color: string }> = {
  done: { glyph: '✓', word: 'Done', color: 'var(--green)' },
  failed: { glyph: '✕', word: 'Not answered', color: 'var(--red)' },
  skipped: { glyph: '–', word: 'Skipped', color: 'var(--text-faint)' },
  running: { glyph: '●', word: 'Running', color: 'var(--accent)' },
  pending: { glyph: '○', word: 'Waiting', color: 'var(--text-faint)' },
  'not-run': { glyph: '○', word: 'Not run', color: 'var(--text-faint)' },
}

export function DeepPlanPanel({
  view, inFlight, onAnswerNow, focus, onFocus, idPrefix,
}: {
  view: DeepView
  inFlight: boolean
  onAnswerNow?: () => void
  /** The step a footnote asked to open, 0-based. */
  focus: number | null
  onFocus: (index: number | null) => void
  /** Keeps two turns' step anchors apart on one page. */
  idPrefix: string
}) {
  const rows = stepRows(view, inFlight)
  const stopped = inFlight ? '' : stopSentence(view)
  const budget = budgetLine(view.budget)
  const answerable = canAnswerNow(view, inFlight)

  return (
    <section
      aria-label="Analysis plan"
      className="rm-artifact"
      style={{
        border: '1px solid var(--border)',
        borderRadius: 10,
        background: 'var(--panel)',
        padding: '12px 14px',
        margin: '4px 0 10px',
      }}
    >
      <header style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: '.04em',
                       textTransform: 'uppercase', color: 'var(--text-dim)' }}>
          Deep analysis
        </span>
        {budget && (
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>{budget}</span>
        )}
        <span style={{ flex: 1 }} />
        {inFlight && onAnswerNow && (
          view.answerNowRequested ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
                           fontSize: 12.5, color: 'var(--text-dim)' }}>
              <Spinner size={12} /> Finishing this step, then writing the answer…
            </span>
          ) : (
            <PrimaryButton
              onClick={onAnswerNow}
              disabled={!answerable}
              title="Stop planning and write the answer from the steps already done. Stop, below, throws the run away; this keeps what it has found."
              style={{ padding: '6px 12px', fontSize: 12.5 }}
            >
              Answer now
              <span style={{ fontWeight: 400, opacity: 0.85 }}>· keep what’s found</span>
            </PrimaryButton>
          )
        )}
      </header>

      {!view.plan && inFlight && (
        <p style={{ margin: '10px 0 2px', fontSize: 13.5, color: 'var(--text-dim)',
                    display: 'flex', alignItems: 'center', gap: 8 }}>
          <Spinner size={12} /> Planning the analysis…
        </p>
      )}

      {view.plan && (
        <>
          <p
            dir={dirOf(view.plan.restatement)}
            style={{ margin: '10px 0 8px', fontSize: 13.5, color: 'var(--text)' }}
          >
            <span style={{ color: 'var(--text-dim)' }}>Understood as: </span>
            {view.plan.restatement}
          </p>
          <ol style={{ listStyle: 'none', margin: 0, padding: 0,
                       display: 'flex', flexDirection: 'column', gap: 2 }}>
            {rows.map((row) => (
              <PlanRow
                key={row.index}
                row={row}
                open={focus === row.index}
                onToggle={() => onFocus(focus === row.index ? null : row.index)}
                id={`${idPrefix}-step-${row.index}`}
              />
            ))}
          </ol>
        </>
      )}

      {stopped && (
        <p style={{ margin: '10px 0 0', fontSize: 12.5, color: 'var(--amber)',
                    display: 'flex', alignItems: 'center', gap: 6 }}>
          <Icon.Alert size={13} /> {stopped}
        </p>
      )}
    </section>
  )
}

function PlanRow({
  row, open, onToggle, id,
}: {
  row: StepRow
  open: boolean
  onToggle: () => void
  id: string
}) {
  const state = STATE[row.state]
  const expandable = row.found !== null
  const muted = row.state === 'pending' || row.state === 'not-run'
  return (
    <li id={id} style={{ borderRadius: 8, background: open ? 'var(--panel-alt)' : undefined }}>
      <button
        type="button"
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        style={{
          all: 'unset',
          boxSizing: 'border-box',
          width: '100%',
          display: 'grid',
          gridTemplateColumns: '20px 1fr auto',
          gap: 8,
          alignItems: 'baseline',
          padding: '6px 8px',
          cursor: expandable ? 'pointer' : 'default',
        }}
      >
        <span aria-hidden className={row.state === 'running' ? 'rm-pulse' : undefined}
              style={{ color: state.color, fontSize: 12, textAlign: 'center' }}>
          {state.glyph}
        </span>
        <span style={{ minWidth: 0 }}>
          {row.replaced.map((old, i) => (
            <span key={i} dir={dirOf(old.question)}
                  style={{ display: 'block', fontSize: 12.5, color: 'var(--text-faint)',
                           textDecoration: 'line-through' }}>
              {old.question}
            </span>
          ))}
          <span dir={dirOf(row.step.question)}
                style={{ fontSize: 13.5, color: muted ? 'var(--text-dim)' : 'var(--text-strong)' }}>
            <span style={{ color: 'var(--text-faint)', marginRight: 6 }}>{row.index + 1}.</span>
            {row.step.question}
            {row.replaced.length > 0 && (
              <span style={{ marginLeft: 6, fontSize: 11.5, color: 'var(--accent)' }}>revised</span>
            )}
          </span>
          {row.step.why && (
            <span dir={dirOf(row.step.why)}
                  style={{ display: 'block', fontSize: 12.5, color: 'var(--text-dim)' }}>
              {row.step.why}
            </span>
          )}
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--text-dim)', whiteSpace: 'nowrap',
                       display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <span>{INTENT_LABEL[row.step.intent] ?? row.step.intent}</span>
          <span aria-hidden>·</span>
          <span>{TOOL_LABEL[row.step.tool] ?? row.step.tool}</span>
          <span aria-hidden>·</span>
          <span style={{ color: state.color }}>{state.word}</span>
          {expandable && <Icon.Chevron size={12} open={open} />}
        </span>
      </button>
      {open && row.found && <StepFound row={row} />}
    </li>
  )
}

function StepFound({ row }: { row: StepRow }) {
  const found = row.found!
  return (
    <div style={{ padding: '0 8px 10px 36px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {found.status !== 'DONE' ? (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)' }}>
          {found.note || 'This step produced no result.'}
        </p>
      ) : (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)' }}>
          {found.row_count ?? 0} rows
          {found.truncated ? ' (capped by the row limit)' : ''}
          {(found.attempts ?? 1) > 1 ? ` · repaired ${(found.attempts ?? 1) - 1}×` : ''}
        </p>
      )}
      {found.computed && (
        <p dir="auto" style={{ margin: 0, fontSize: 13, color: 'var(--text)' }}>
          <span style={{ color: 'var(--text-dim)' }}>
            {found.computed.ok ? 'Computed: ' : 'Not computed: '}
          </span>
          {found.computed.summary}
        </p>
      )}
      {found.sql && (
        <pre dir="ltr" style={{
          margin: 0, padding: '8px 10px', borderRadius: 6, fontSize: 12,
          background: 'var(--code-bg)', color: 'var(--code-text)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {found.sql}
        </pre>
      )}
    </div>
  )
}

/**
 * The answer, with a footnote after each sentence that cites a step. A
 * footnote after the **sentence**, not a link on the digit — the report's rule
 * (Phase 3): a claim is drawn from a result, and underlining the number would
 * say the number is sourced while leaving the claim around it unsourced.
 */
export function DeepAnswerText({
  text, claims, onCite,
}: {
  text: string
  claims: readonly DeepClaim[]
  onCite: (index: number) => void
}) {
  const spans = answerSpans(text, claims)
  return (
    <>
      {spans.map((span, i) => (
        <span key={i}>
          {i > 0 && (spans[i - 1].lead ? '\n\n' : ' ')}
          {span.text}
          {span.cites !== null && (
            <button
              type="button"
              onClick={() => onCite(span.cites! - 1)}
              title={
                span.unsupported
                  ? `A figure in this sentence is not in step ${span.cites}'s result`
                  : `Drawn from step ${span.cites} — open its query`
              }
              style={{
                all: 'unset',
                cursor: 'pointer',
                fontSize: 11,
                verticalAlign: 'super',
                marginLeft: 2,
                color: span.unsupported ? 'var(--amber)' : 'var(--accent)',
              }}
            >
              [{span.cites}{span.unsupported ? ' ?' : ''}]
            </button>
          )}
        </span>
      ))}
    </>
  )
}

/** Open a step and bring it into view — what a footnote does. */
export function useStepFocus(idPrefix: string): [number | null, (index: number | null) => void] {
  const [focus, setFocus] = useState<number | null>(null)
  function focusStep(index: number | null) {
    setFocus(index)
    if (index === null) return
    requestAnimationFrame(() => {
      document.getElementById(`${idPrefix}-step-${index}`)
        ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    })
  }
  return [focus, focusStep]
}
