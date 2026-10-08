/**
 * The template editor: a question, its SQL, the parameters it may take, and
 * the guard's verdict, checked as it is typed. Opened from the knowledge
 * console and from a chat answer (*Teach this*).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { knowledge as api } from '../api/client'
import type {
  Connection, KnowledgeTemplate, ParamProposal, TemplateCheckResult,
  TemplateParam,
} from '../api/types'
import {
  Chip, Dot, ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, Spinner,
  TextArea, TextInput, dirOf,
} from './ui'
import { markLiterals, previewQuestion, readiness } from './knowledge-template'
import { CODE, messageOf } from './knowledge-parts'

/** How long the editor waits after a keystroke before asking the server.
 *  Long enough not to check every character, short enough that the verdict
 *  lands while the curator is still looking at the SQL box. */
const CHECK_DEBOUNCE_MS = 400


/**
 * The editor — the screen that matters most.
 *
 * The whole design goal in one sentence: **the curator pastes SQL and gets
 * offered a family.** Everything else follows from that — the live preview of
 * what the question would match, the literals marked in the statement itself,
 * and the refused proposal shown unticked with its reason rather than hidden.
 */
export function TemplateEditor({
  connection, template, prefill, onClose, onSaved,
}: {
  connection: Connection
  template: KnowledgeTemplate | null
  /**
   * A question and a statement to start from, when the editor is opened from
   * somewhere other than the Knowledge tab — a chat answer that worked, or a
   * backlog row. `source` decides whether the literals are the model's or a
   * person's, which is a disclosure question and not a cosmetic one.
   */
  prefill?: { question: string; sql: string; source?: string }
  onClose: () => void
  onSaved: (template: KnowledgeTemplate) => void
}) {
  const [question, setQuestion] = useState(
    template?.question ?? prefill?.question ?? '',
  )
  const [sql, setSql] = useState(template?.sql ?? prefill?.sql ?? '')
  const [note, setNote] = useState(template?.note ?? '')
  const [benchmark, setBenchmark] = useState(template?.role === 'BENCHMARK_ONLY')
  const [accepted, setAccepted] = useState<string[]>(
    () => (template?.params ?? []).map((p) => p.name),
  )
  const [check, setCheck] = useState<TemplateCheckResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hovered, setHovered] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // The verdict comes from the backend on every pause in typing, never from a
  // local guess. `null` means "not answered yet", which is a state the Save
  // button waits in rather than guesses through.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    if (!sql.trim()) {
      setCheck(null)
      return
    }
    timer.current = setTimeout(() => {
      setChecking(true)
      api
        .check(connection.id, { sql, question, accept: accepted })
        .then(setCheck)
        .catch(() => setCheck(null))
        .finally(() => setChecking(false))
    }, CHECK_DEBOUNCE_MS)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [connection.id, sql, question, accepted])

  const params: TemplateParam[] = check?.params ?? []
  const ready = readiness(question, sql, params, check?.valid ?? null)
  const proposals = check?.proposals ?? []
  const marked = useMemo(
    () => markLiterals(sql, proposals.filter((p) => p.eligible)),
    [sql, proposals],
  )

  function toggle(name: string) {
    setAccepted((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }

  // The proposals arrive with the backend's own defaults ticked. Adopted once,
  // on the first verdict for a new template, so a curator's un-tick is never
  // overwritten by the next keystroke's response.
  const adopted = useRef(template !== null)
  useEffect(() => {
    if (adopted.current || !check) return
    adopted.current = true
    setAccepted(check.proposals.filter((p) => p.suggested).map((p) => p.name))
  }, [check])

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const payload = {
        question,
        sql: check?.sql || sql,
        params,
        note,
        role: benchmark
          ? ('BENCHMARK_ONLY' as const)
          : ('RETRIEVABLE' as const),
        // If the curator edited the statement they were shown, the literals
        // are now theirs and travel with structure; if they only confirmed it,
        // they are still the model's and are gated like sample values.
        // `docs/reference/security.md`.
        source: prefill
          ? sql.trim() === prefill.sql.trim()
            ? prefill.source ?? 'CHAT_CONFIRMED'
            : 'CHAT_CORRECTED'
          : 'MANUAL',
      }
      const saved = template
        ? await api.update(connection.id, template.id, payload)
        : await api.create(connection.id, payload)
      onSaved(saved)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setSaving(false)
    }
  }

  const tables = (check?.referenced_tables ?? []).map((t) => t.split('.').pop() ?? t)
  const eligible = proposals.filter((p) => p.eligible)

  // What the box under the statement says about it, in one line. The long
  // green pill this replaces ("Valid · public.order_items, public.orders,
  // public.products") grew with the query until it pushed the label off its
  // own row; the verdict is a chip beside the label now, and the tables it
  // reads are a sentence in the box's own footer, where a long list can wrap.
  const boxStatus = checking
    ? 'Checking…'
    : !sql.trim()
      ? 'Paste the statement that answers the question.'
      : check?.valid
        ? tables.length > 0
          ? `Reads ${tables.join(', ')}`
          : 'Valid — it reads no tables.'
        : check
          // The guard's own sentence, in the footer of the box that produced
          // it — one place for the refusal, attached to the statement. It used
          // to be a chip saying *Rejected* and a red note underneath saying it
          // again before getting to the reason.
          ? check.issue || 'Rejected.'
          : 'Not checked yet'

  return (
    <Modal
      title={template ? 'Edit template' : 'Teach a question'}
      subtitle={
        template
          ? undefined
          : prefill
            ? 'From an answer you just saw work — check the question, then save it.'
            : 'Write it the way someone would ask it, then paste the SQL that answers it.'
      }
      width={720}
      onClose={onClose}
      footer={
        <div
          style={{
            display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap',
          }}
        >
          {/* Why Save is off, beside the button that is off — not at the far
              end of a scrolling form where the reader has to go looking for
              the reason they were refused. */}
          {ready.issue && (
            <span
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 7, flex: '1 1 240px',
                minWidth: 0, fontSize: 11.5, lineHeight: 1.5, color: 'var(--amber)',
              }}
            >
              <span style={{ display: 'flex', paddingTop: 1 }}>
                <Icon.Alert size={13} />
              </span>
              {ready.issue}
            </span>
          )}
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
            <GhostButton onClick={onClose}>Cancel</GhostButton>
            <PrimaryButton onClick={save} disabled={!ready.ready || saving}>
              {saving && <Spinner />}
              Save template
            </PrimaryButton>
          </div>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Field
          label="Question"
          hint={
            question.includes('{')
              ? undefined
              : 'Write it the way someone would ask it. Wrap the parts that change in braces — {region}.'
          }
        >
          <TextInput
            value={question}
            dir={dirOf(question)}
            placeholder="revenue by month for {region} in {year}"
            onChange={(e) => setQuestion(e.target.value)}
          />
          {question.includes('{') && params.length > 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-faint)', marginTop: 5 }}>
              Matches questions like &ldquo;{previewQuestion(question, params)}&rdquo;
            </div>
          )}
        </Field>

        <Field
          label="SQL"
          status={
            // The last verdict stays up while the next one is being fetched —
            // the box's own footer is what says "Checking…", and a chip that
            // vanished on every keystroke made the header flicker for the
            // whole time somebody was typing a statement.
            <span aria-live="polite" style={{ display: 'flex', alignItems: 'center' }}>
              {check?.valid && (
                <Chip tone="green">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                    <Dot color="var(--green)" />
                    Valid
                  </span>
                </Chip>
              )}
              {check && !check.valid && <Chip tone="red">Rejected</Chip>}
            </span>
          }
        >
          {/* The same shell the report and tile editors use: a statement and
              the verdict on it are one object, so they share one border. */}
          <div className="rm-sqlbox">
            <TextArea
              className="mono"
              dir="ltr"
              value={sql}
              spellCheck={false}
              placeholder="SELECT …"
              onChange={(e) => setSql(e.target.value)}
              // `.rm-sqlbox` owns the surface now, so the statement gives up
              // the code background it used to draw for itself — two shades
              // inside one border read as a box inside a box.
              style={{
                ...CODE, background: 'transparent', minHeight: 150,
                whiteSpace: 'pre-wrap',
              }}
            />
            <div className="rm-sqlbox-bar">
              <span
                className={`rm-sqlbox-hint${
                  check && !check.valid && !checking ? ' is-error' : ''
                }`}
                style={{ whiteSpace: 'normal' }}
              >
                {checking && <Spinner size={11} />}
                {boxStatus}
              </span>
            </div>
          </div>
          {marked.some((s) => s.slot) && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-faint)', marginBottom: 4 }}>
                Stored like this — the highlighted values become parameters:
              </div>
              <pre
                aria-hidden
                style={{ ...CODE, margin: 0, padding: 10, borderRadius: 8,
                         background: 'var(--code-bg)', whiteSpace: 'pre-wrap' }}
              >
                {marked.map((span, i) => (
                  <span
                    key={i}
                    style={
                      span.slot
                        ? {
                            background:
                              hovered === span.slot ? 'var(--accent)' : 'var(--accent-bg)',
                            color: hovered === span.slot ? 'var(--bg)' : 'var(--accent)',
                            borderRadius: 3,
                          }
                        : undefined
                    }
                  >
                    {span.text}
                  </span>
                ))}
              </pre>
            </div>
          )}
        </Field>

        {proposals.length > 0 && (
          <Field
            label="Parameters"
            hint="Found by reading your SQL — nothing was sent anywhere."
            status={
              <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                {accepted.length} of {eligible.length} used
              </span>
            }
          >
            <div
              style={{
                border: '1px solid var(--border)', borderRadius: 8,
                overflow: 'hidden',
                display: 'flex', flexDirection: 'column',
              }}
            >
              {proposals.map((proposal, index) => (
                <Proposal
                  key={`${proposal.name}-${proposal.occurrence}`}
                  proposal={proposal}
                  first={index === 0}
                  checked={accepted.includes(proposal.name)}
                  onToggle={() => toggle(proposal.name)}
                  onHover={setHovered}
                />
              ))}
            </div>
          </Field>
        )}

        <Field
          label="Note for the next person"
          hint="Written for people, not the model. This never goes into a prompt."
        >
          <TextArea
            value={note}
            dir={dirOf(note)}
            placeholder="Cancelled orders are never revenue…"
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>

        {/* An option with its consequence attached, rather than a bare
            checkbox and a sentence the reader has to finish themselves. */}
        <label
          style={{
            display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
            padding: '10px 12px', borderRadius: 8,
            border: `1px solid ${benchmark ? 'var(--accent-border)' : 'var(--border)'}`,
            background: benchmark ? 'var(--accent-bg)' : 'transparent',
            transition: 'border-color .12s ease, background .12s ease',
          }}
        >
          <input
            type="checkbox"
            checked={benchmark}
            onChange={(e) => setBenchmark(e.target.checked)}
            style={{ accentColor: 'var(--accent)', cursor: 'pointer', marginTop: 1 }}
          />
          <span style={{ minWidth: 0 }}>
            <span style={{ display: 'block', fontSize: 12.5, color: 'var(--text)' }}>
              Use this to measure accuracy, not to answer questions
            </span>
            <span
              style={{
                display: 'block', fontSize: 11, color: 'var(--text-faint)', marginTop: 2,
              }}
            >
              It joins the benchmark set. Questions are never answered from it.
            </span>
          </span>
        </label>
      </div>
    </Modal>
  )
}

/**
 * One parameter proposal: a real checkbox in a real list.
 *
 * A refusal is rendered rather than hidden, unticked and with its reason next
 * to it — showing the rejected candidate teaches the rule better than hiding
 * it, and the curator occasionally knows better.
 */
function Proposal({
  proposal, first, checked, onToggle, onHover,
}: {
  proposal: ParamProposal
  /** No rule above the first row: the container already draws that edge. */
  first: boolean
  checked: boolean
  onToggle: () => void
  onHover: (name: string | null) => void
}) {
  const [hover, setHover] = useState(false)
  return (
    <label
      onMouseEnter={() => {
        setHover(true)
        onHover(proposal.name)
      }}
      onMouseLeave={() => {
        setHover(false)
        onHover(null)
      }}
      style={{
        display: 'flex', gap: 9, alignItems: 'center', padding: '9px 11px',
        borderTop: first ? 'none' : '1px solid var(--border)', fontSize: 12,
        cursor: proposal.eligible ? 'pointer' : 'default',
        background: hover && proposal.eligible ? 'var(--panel-alt)' : 'transparent',
        transition: 'background .12s ease',
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={!proposal.eligible}
        onChange={onToggle}
        style={{
          accentColor: 'var(--accent)',
          cursor: proposal.eligible ? 'pointer' : 'default',
          flexShrink: 0,
        }}
      />
      {/* The substitution, read left to right as one phrase: this literal
          becomes this name. The arrow is the only thing between them. */}
      <span
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0,
          opacity: proposal.eligible ? 1 : 0.6,
        }}
      >
        <code style={{ ...CODE, background: 'var(--accent-bg)', color: 'var(--accent)',
                       padding: '1px 5px', borderRadius: 3, maxWidth: 160,
                       overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {proposal.literal}
        </code>
        <Icon.ArrowRight size={12} stroke="var(--text-faint)" />
        <code style={{ ...CODE, background: 'none', padding: 0,
                       color: 'var(--text)' }}>
          :{proposal.name}
        </code>
        <Chip small>{proposal.type}</Chip>
      </span>
      {/* A refusal is rendered rather than hidden, unticked and with its reason
          beside it — showing the rejected candidate teaches the rule better
          than hiding it, and the curator occasionally knows better. */}
      <span
        style={{
          flex: 1, minWidth: 0, textAlign: 'right', fontSize: 11.5,
          color: proposal.eligible ? 'var(--text-faint)' : 'var(--amber)',
        }}
      >
        {proposal.comment || proposal.reason}
      </span>
    </label>
  )
}
