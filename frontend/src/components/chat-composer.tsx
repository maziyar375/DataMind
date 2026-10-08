/**
 * The chat composer: the question box, the answer-depth toggle and the section
 * picker that ride inside it. Rendered by `ChatPage`.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Icon, Spinner, dirOf } from './ui'

export function Composer({
  value, onChange, onSubmit, onStop, busy, stopping, ready,
  sections, scope, onScope, depth, onDepth,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  /** Cancel the run in flight. The same button, while there is one. */
  onStop: () => void
  busy: boolean
  /** A stop already asked for and not yet landed. */
  stopping: boolean
  /** Both a database and a model are chosen — required before a first send. */
  ready: boolean
  /** This database's sections. Empty means no picker, which is every
   * connection that has not been divided. */
  sections: string[]
  /** The chosen section, `WHOLE_DATABASE`, or null for "let it choose". */
  scope: string | null
  onScope: (scope: string | null) => void
  /** This conversation's depth, or null where deep analysis is not enabled —
   * which is no toggle at all rather than a disabled one. */
  depth: 'QUICK' | 'DEEP' | null
  onDepth: (depth: 'QUICK' | 'DEEP') => void
}) {
  const [focus, setFocus] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)

  // Grow with the text, a line at a time, up to two-fifths of the window —
  // then scroll inside, which is how every chat box people arrive knowing
  // behaves. The cap follows the window rather than a fixed 160px, so a
  // laptop gets room for a pasted paragraph and a phone keeps its transcript.
  //
  // `overflow-y` is decided here too: hidden while the box can still grow,
  // so no scrollbar flashes on the line that makes it taller, and `auto`
  // only once the text is actually taller than the cap. A layout effect, so
  // the height lands before paint and the box never draws one line short.
  const grow = useCallback(() => {
    const el = ref.current
    if (!el) return
    const cap = Math.max(120, Math.round(window.innerHeight * 0.4))
    el.style.height = 'auto'
    const full = el.scrollHeight
    el.style.height = `${Math.min(full, cap)}px`
    el.style.overflowY = full > cap ? 'auto' : 'hidden'
    el.classList.toggle('is-scrolled', full > cap && el.scrollTop > 0)
  }, [])
  useLayoutEffect(grow, [value, grow])
  useEffect(() => {
    window.addEventListener('resize', grow)
    return () => window.removeEventListener('resize', grow)
  }, [grow])

  const canSend = value.trim().length > 0 && !busy && ready
  const active = focus || value.trim().length > 0
  // One control, two jobs — send while there is nothing running, stop while
  // there is. It is the same button because it is the same question ("what do
  // I do about this answer?"), and because a second button that is disabled
  // half the time is a second thing to look at every time it is not.
  const canStop = busy && !stopping

  const tools = sections.length > 0 || depth !== null
  const deep = depth === 'DEEP'

  const sendButton = (
    <>
          {/*
            One slot, two controls, one disc — the arrow becomes a square while
            a run is in flight, which is the convention every chat product has
            settled on and therefore the one people arrive already knowing.
            It was a labelled pill for a while, on the worry that a 13px glyph
            swap in the same circle is too quiet to notice. Three things carry
            that signal without a word: the square is now a third of the disc
            rather than a detail in the middle of it, the accent ring around
            the button breathes for as long as the run lasts, and the hint
            line directly underneath says `Esc` to stop. The word inside the
            button was the fourth telling of the same thing — and the only one
            that cost a control its shape.
          */}
          <button
            className={`rm-send-btn${canStop ? ' is-running' : ''}`}
            onClick={() => (busy ? canStop && onStop() : canSend && onSubmit())}
            disabled={busy ? !canStop : !canSend}
            aria-label={busy ? 'Stop generating' : 'Send'}
            title={busy ? 'Stop generating  ·  Esc' : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: 38,
              width: 38,
              padding: 0,
              borderRadius: 999,
              border: busy ? '1px solid var(--border-strong)' : 'none',
              flexShrink: 0,
              // Stopping is not the accent action — it undoes one — so the
              // disc goes neutral and lets the answer above it keep the only
              // accent on the screen. The ring around it stays accent: that
              // is the run, not the button.
              background: busy
                ? 'var(--panel-alt)'
                : canSend
                  ? 'linear-gradient(150deg, color-mix(in oklch, var(--accent) 88%, white), var(--accent))'
                  : 'var(--panel-alt)',
              color: busy
                ? stopping ? 'var(--text-dim)' : 'var(--text-strong)'
                : canSend ? 'var(--on-accent)' : 'var(--text-faint)',
              cursor: busy
                ? canStop ? 'pointer' : 'default'
                : canSend ? 'pointer' : 'not-allowed',
              transition: 'background .15s ease, color .15s ease',
            }}
          >
            {busy ? (
              // 24 draws a 12px square inside a 38px disc — the same third of
              // the button the products this borrows from use, and enough to
              // read as a shape rather than as a dot.
              stopping ? <Spinner size={15} /> : <Icon.Stop size={24} />
            ) : (
              // Up, not a paper plane: the arrow is what both of the products
              // people arrive from draw here, and it says "send this up into
              // the thread" rather than "email".
              <Icon.ArrowUp size={18} strokeWidth={2.4} />
            )}
          </button>
    </>
  )

  return (
    <div style={{ padding: '10px 28px 20px', flexShrink: 0 }}>
      <div
        className={`rm-composer${active ? ' is-active' : ''}${busy ? ' is-busy' : ''}`}
        style={{ maxWidth: 780, margin: '0 auto' }}
      >
        {/*
          The per-question controls live *inside* the box, on a row under the
          text — beside the message they configure and the button that sends
          it, which is where every chat product people arrive knowing puts
          them. They used to float above the box as two pills of different
          sizes and type, reading as page furniture rather than as settings
          for this question, and on a phone the pair ran off the screen.

          Not behind an "Advanced" fold, deliberately: both change the answer.
          Depth turns seconds into minutes, and a chosen section narrows what
          the model may read — a setting that changes the answer has to be
          visible while it is in force.
        */}
        <div
          className="rm-composer-box"
          // The whole box is the field: a click on its padding or on the
          // empty stretch of the tool row puts the caret in the text, as it
          // does in the products this follows. Controls keep their clicks.
          onMouseDown={(e) => {
            const target = e.target as HTMLElement
            if (target === e.currentTarget || target.classList.contains('rm-composer-controls')) {
              e.preventDefault()
              ref.current?.focus()
            }
          }}
          style={{
            display: 'flex',
            flexDirection: tools ? 'column' : 'row',
            alignItems: tools ? 'stretch' : 'flex-end',
            gap: 10,
            background: 'var(--panel)',
            // Deep keeps an accent edge at rest, so a thread left in the
            // minutes-long mode says so before anything is typed into it.
            border: `1px solid ${
              focus ? 'var(--accent-border)' : deep ? 'var(--accent-border)' : 'var(--border-strong)'
            }`,
            borderRadius: 24,
            padding: tools ? '14px 10px 10px 12px' : '10px 10px 10px 18px',
            cursor: 'text',
            // Calm on focus: a soft ring and a deeper shadow, and no lift. The
            // box used to rise a pixel when focused, which on a box that now
            // grows as you type reads as the field jumping.
            boxShadow: focus
              ? '0 0 0 3px var(--accent-bg), var(--elev-2)'
              : 'var(--elev-1)',
            transition: 'border-color .18s ease, box-shadow .2s ease',
          }}
        >
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onFocus={() => setFocus(true)}
            onBlur={() => setFocus(false)}
            // Past the cap, lines scrolled above the top soften into the box
            // rather than being sliced at its edge.
            onScroll={(e) =>
              e.currentTarget.classList.toggle('is-scrolled', e.currentTarget.scrollTop > 0)
            }
            onKeyDown={(e) => {
              // Not while an input method is composing: there Enter commits
              // the word being built, and sending half of it is data loss.
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                if (canSend) onSubmit()
              }
            }}
            rows={1}
            dir={dirOf(value)}
            // In Deep the prompt says what the mode is *for*: a why-question
            // is where a plan of several queries earns its minutes.
            placeholder={deep ? 'Ask why something changed…' : 'Ask anything about your data…'}
            aria-label="Ask about your data"
            style={{
              // In the stacked layout this must not be `flex: 1`: a flex-basis
              // on the column's main axis overrides the height `grow` sets, and
              // the box stayed one line tall however much was typed into it.
              flex: tools ? 'none' : 1,
              width: tools ? '100%' : undefined,
              resize: 'none',
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: 'var(--text)',
              fontSize: 15,
              lineHeight: 1.55,
              // Inset so the text starts where the controls' labels do.
              padding: tools ? '0 6px' : '5px 0',
              cursor: 'text',
            }}
          />
          {tools ? (
            <div className="rm-composer-tools">
              {/* The controls wrap as a group and the disc never moves: on a
                  phone the section chip drops under the mode rather than
                  being squeezed to an icon beside the send button. */}
              <div className="rm-composer-controls">
                {depth !== null && (
                  <DepthToggle value={depth} onChange={onDepth} disabled={busy} />
                )}
                {sections.length > 0 && (
                  <ScopePicker value={scope} onChange={onScope} options={sections} />
                )}
                {deep && <span className="rm-composer-eta">Takes a few minutes</span>}
              </div>
              {sendButton}
            </div>
          ) : (
            sendButton
          )}
        </div>

        <div
          className="rm-composer-hint"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            marginTop: 8,
            fontSize: 11,
            color: 'var(--text-faint)',
          }}
        >
          {busy ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              {stopping ? (
                'Stopping…'
              ) : (
                <>
                  <span className="rm-kbd">Esc</span> to stop
                </>
              )}
            </span>
          ) : ready ? (
            <>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <span className="rm-kbd">Enter</span> to send
              </span>
              <span style={{ opacity: 0.5 }}>·</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                <span className="rm-kbd">Shift</span>
                <span className="rm-kbd">Enter</span> for a new line
              </span>
            </>
          ) : (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Icon.Sparkle size={12} stroke="var(--text-faint)" />
              Choose a database and model above to start
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * *Ask within…* — the reader's own answer to the question the `scope` node
 * asks a model.
 *
 * The research matrix marks *"the user can correct the retrieval decision"*
 * as the one thing this product had no answer to, and once a database is
 * divided it is a dropdown. Three states, and the difference between the
 * first two is the whole point:
 *
 * - **Any section** — nobody has chosen, and the router picks as it does now;
 * - **a section** — answered from that one, with no routing call at all;
 * - **Whole database** — narrowed by nothing, also with no call.
 *
 * It sits under the composer rather than beside the header's two pickers
 * because it is a property of *this question*, not of the thread: the
 * database and the model are fixed once a thread starts, and this changes
 * between one question and the next.
 */
const WHOLE_DATABASE = 'NONE'

/**
 * *Quick* / *Deep* — the conversation's answer mode, first in the composer's
 * tool row.
 *
 * A segmented pair rather than a switch: both states are choices with names,
 * and "off" is not one of them. The latency the plan wants named in the
 * control (§4.1) is said three times without making the control long — the
 * Deep segment's tooltip, a "Takes a few minutes" beside the row while it is
 * chosen, and the box's accent edge — which is what stops a multi-minute
 * analysis being felt as a hang.
 *
 * A real radio group for the keyboard: one tab stop, arrows move the choice.
 */
function DepthToggle({
  value, onChange, disabled,
}: {
  value: 'QUICK' | 'DEEP'
  onChange: (value: 'QUICK' | 'DEEP') => void
  disabled?: boolean
}) {
  const options: Array<{
    value: 'QUICK' | 'DEEP'; label: string; title: string; icon: React.ReactNode
  }> = [
    {
      value: 'QUICK',
      label: 'Quick',
      title: 'Quick — one query, an answer in seconds',
      icon: <Icon.Zap size={13} />,
    },
    {
      value: 'DEEP',
      label: 'Deep',
      title: 'Deep analysis — plans several queries and takes a few minutes. You can watch it work and ask for the answer early.',
      icon: <Icon.Steps size={13} />,
    },
  ]
  const buttons = useRef<Array<HTMLButtonElement | null>>([])

  function step(from: number, delta: number) {
    const next = (from + delta + options.length) % options.length
    onChange(options[next].value)
    buttons.current[next]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label="Answer mode"
      aria-disabled={disabled || undefined}
      className="rm-seg"
    >
      {options.map((option, i) => {
        const on = option.value === value
        return (
          <button
            key={option.value}
            ref={(el) => {
              buttons.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            title={option.title}
            disabled={disabled}
            className={`rm-seg-opt${on ? ' is-on' : ''}${option.value === 'DEEP' ? ' is-deep' : ''}`}
            onClick={() => onChange(option.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                e.preventDefault()
                step(i, 1)
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                e.preventDefault()
                step(i, -1)
              }
            }}
          >
            {option.icon}
            <span>{option.label}</span>
          </button>
        )
      })}
    </div>
  )
}

function ScopePicker({
  value, onChange, options,
}: {
  value: string | null
  onChange: (value: string | null) => void
  options: string[]
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    // Capture, so closing this menu does not also reach the page-level Escape
    // that stops a run.
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const rows: { value: string | null; label: string; hint?: string }[] = [
    { value: null, label: 'Any section', hint: 'Chosen for each question' },
    ...options.map((name) => ({ value: name as string | null, label: name })),
    { value: WHOLE_DATABASE, label: 'Whole database', hint: 'Nothing narrowed' },
  ]
  const current = rows.find((r) => r.value === value) ?? rows[0]

  // What the chip says is the choice itself, not the control's name: "Any
  // section" when nothing narrows the question, "Within Orders" when a
  // section does, "Whole database" when the reader widened it on purpose.
  // Anything but the default is an override in force, so it is drawn tinted.
  const isSet = value !== null
  const label =
    value === null ? 'Any section'
    : value === WHOLE_DATABASE ? 'Whole database'
    : current.label

  return (
    <span ref={ref} style={{ position: 'relative', display: 'inline-flex', minWidth: 0 }}>
      <button
        type="button"
        className={`rm-tool-btn${isSet ? ' is-set' : ''}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Ask within: ${current.label}`}
        title={`Ask within: ${current.label}`}
      >
        <Icon.Grid size={13} />
        <span className="rm-tool-label">
          {value !== null && value !== WHOLE_DATABASE && (
            <span style={{ color: 'var(--text-dim)', fontWeight: 500 }}>Within </span>
          )}
          <span dir={dirOf(label)}>{label}</span>
        </span>
        {/* The menu opens upward, so the chevron points up until it does. */}
        <span
          aria-hidden
          style={{
            display: 'inline-flex',
            transform: open ? 'rotate(90deg)' : 'rotate(-90deg)',
            transition: 'transform .16s cubic-bezier(.2,.8,.2,1)',
          }}
        >
          <Icon.Chevron size={12} stroke="currentColor" />
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Ask within"
          style={{
            position: 'absolute',
            // Up from the chip and flush with its left edge: the row sits at
            // the bottom of the box, and a menu centred on a chip near the
            // box's edge would hang off it on a phone.
            bottom: 'calc(100% + 10px)',
            left: 0,
            minWidth: 230,
            maxWidth: 300,
            maxHeight: 300,
            overflowY: 'auto',
            background: 'var(--panel)',
            border: '1px solid var(--border-strong)',
            borderRadius: 10,
            padding: 5,
            boxShadow: 'inset 0 1px 0 0 var(--sheen), var(--elev-3)',
            zIndex: 50,
            textAlign: 'left',
          }}
        >
          <div
            aria-hidden
            style={{
              padding: '6px 9px 5px',
              fontSize: 11.5,
              fontWeight: 600,
              color: 'var(--text-dim)',
            }}
          >
            Ask within
          </div>
          {rows.map((row) => {
            const chosen = row.value === current.value
            return (
              <button
                key={row.value ?? '__auto__'}
                type="button"
                role="option"
                aria-selected={chosen}
                className="rm-menu-item"
                onClick={() => {
                  onChange(row.value)
                  setOpen(false)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  width: '100%',
                  padding: '7px 9px',
                  borderRadius: 7,
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text)',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 13,
                    display: 'flex',
                    color: chosen ? 'var(--accent)' : 'transparent',
                  }}
                >
                  <Icon.Check size={12} />
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span
                    dir={dirOf(row.label)}
                    style={{
                      display: 'block',
                      fontSize: 12.5,
                      fontWeight: chosen ? 650 : 500,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {row.label}
                  </span>
                  {row.hint && (
                    <span style={{ display: 'block', fontSize: 11, color: 'var(--text-dim)' }}>
                      {row.hint}
                    </span>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </span>
  )
}
