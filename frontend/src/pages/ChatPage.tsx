import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { conversations, connections as connectionsApi, llmConfigs, streamRun } from '../api/client'
import type {
  Connection, ConversationSummary, LlmConfig, MessageWithRun, RunStep,
} from '../api/types'
import {
  AssistantTurn, RunErrorCard, ThinkingCard, UserBubble,
} from '../components/chat'
import {
  ErrorNote, Icon, PrimaryButton, Spinner, dirOf, initialOf,
} from '../components/ui'

export default function ChatPage() {
  const [conversationList, setConversationList] = useState<ConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<MessageWithRun[]>([])
  const [connections, setConnections] = useState<Connection[]>([])
  const [models, setModels] = useState<LlmConfig[]>([])
  const [connectionId, setConnectionId] = useState<string>('')
  const [modelId, setModelId] = useState<string>('')
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Live run state, kept separate from persisted messages so a refresh
  // mid-run recovers from the server rather than from this component.
  const [liveSteps, setLiveSteps] = useState<RunStep[]>([])
  const [liveText, setLiveText] = useState('')
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const stopStreamRef = useRef<(() => void) | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)
  const [showJump, setShowJump] = useState(false)

  // ── bootstrap ───────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [convs, conns, llms] = await Promise.all([
          conversations.list(),
          connectionsApi.list(),
          llmConfigs.list(),
        ])
        if (cancelled) return
        setConversationList(convs)
        setConnections(conns)
        setModels(llms)
        setConnectionId(conns.find((c) => c.is_default)?.id ?? conns[0]?.id ?? '')
        setModelId(llms.find((m) => m.is_default)?.id ?? llms[0]?.id ?? '')
        if (convs.length > 0) setActiveId(convs[0].id)
      } catch {
        if (!cancelled) setError('Could not load your workspace.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      stopStreamRef.current?.()
    }
  }, [])

  // ── load a conversation ─────────────────────────────────────────────────
  const loadMessages = useCallback(async (conversationId: string) => {
    const loaded = await conversations.messages(conversationId)
    setMessages(loaded)

    // If the newest run is still in flight, reattach to its stream instead of
    // showing a conversation that looks frozen.
    const lastRun = loaded.at(-1)?.run
    if (lastRun && !isTerminal(lastRun.status)) {
      attachStream(lastRun.id, conversationId)
    }
  }, [])

  useEffect(() => {
    if (!activeId) {
      setMessages([])
      return
    }
    loadMessages(activeId).catch(() => setError('Could not load this conversation.'))
    const conversation = conversationList.find((c) => c.id === activeId)
    if (conversation?.default_connection_id) setConnectionId(conversation.default_connection_id)
    if (conversation?.default_llm_config_id) setModelId(conversation.default_llm_config_id)
  }, [activeId, loadMessages])

  // Follow new content only when the reader is already at the end. Scrolling
  // back to re-read an earlier answer should not be yanked forward by a
  // streaming token.
  function handleScroll() {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    followRef.current = distance < 120
    setShowJump(distance > 240)
  }

  function jumpToEnd() {
    const el = scrollRef.current
    if (!el) return
    followRef.current = true
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }

  useEffect(() => {
    if (!followRef.current) return
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, liveText, liveSteps])

  // ── streaming ───────────────────────────────────────────────────────────
  function attachStream(runId: string, conversationId: string) {
    stopStreamRef.current?.()
    setActiveRunId(runId)
    setLiveSteps([])
    setLiveText('')

    stopStreamRef.current = streamRun(runId, {
      onEvent: (event) => {
        switch (event.type) {
          case 'STEP_STARTED':
            setLiveSteps((prev) => [
              ...prev.filter((s) => s.seq !== event.data.seq),
              {
                seq: event.data.seq,
                name: event.data.name,
                status: 'RUNNING',
                detail: null,
                duration_ms: null,
              },
            ])
            break
          case 'STEP_FINISHED':
            setLiveSteps((prev) =>
              prev.map((step) =>
                step.seq === event.data.seq
                  ? {
                      ...step,
                      status: event.data.status,
                      detail: event.data.detail ?? null,
                      duration_ms: event.data.duration_ms ?? null,
                    }
                  : step,
              ),
            )
            break
          case 'TEXT_DELTA':
            setLiveText((prev) => prev + (event.data.text ?? ''))
            break
          default:
            break
        }
      },
      onDone: async () => {
        setActiveRunId(null)
        setLiveSteps([])
        setLiveText('')
        try {
          await loadMessages(conversationId)
          setConversationList(await conversations.list())
        } catch {
          /* the run finished; a list refresh failure is not worth an error */
        }
      },
      onError: () => {
        /* the client falls back to polling internally */
      },
    })
  }

  // ── send ────────────────────────────────────────────────────────────────
  /** `override` lets a suggestion chip send without waiting for a state tick. */
  async function send(override?: string) {
    const content = (override ?? draft).trim()
    if (!content || activeRunId) return

    if (!connectionId || !modelId) {
      setError('Add a data source and a model before asking a question.')
      return
    }

    setError(null)
    setDraft('')

    try {
      let conversationId = activeId
      if (!conversationId) {
        const created = await conversations.create({
          connection_id: connectionId,
          llm_config_id: modelId,
        })
        conversationId = created.id
        setActiveId(created.id)
        setConversationList((prev) => [created, ...prev])
      }

      // Optimistic user turn, so the question appears immediately.
      setMessages((prev) => [
        ...prev,
        {
          id: `pending-${Date.now()}`,
          seq: (prev.at(-1)?.seq ?? 0) + 1,
          role: 'USER',
          content,
          created_at: new Date().toISOString(),
          run: null,
        },
      ])

      const accepted = await conversations.send(conversationId, {
        content,
        connection_id: connectionId,
        llm_config_id: modelId,
      })
      attachStream(accepted.run_id, conversationId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send that message.')
      setDraft(content)
    }
  }

  async function newChat() {
    stopStreamRef.current?.()
    setActiveRunId(null)
    const created = await conversations.create({
      connection_id: connectionId || undefined,
      llm_config_id: modelId || undefined,
    })
    setConversationList((prev) => [created, ...prev])
    setActiveId(created.id)
    setMessages([])
  }

  async function deleteConversation(id: string) {
    // Remove optimistically so the row disappears the instant it's confirmed.
    const remaining = conversationList.filter((c) => c.id !== id)
    setConversationList(remaining)
    if (activeId === id) {
      stopStreamRef.current?.()
      setActiveRunId(null)
      setMessages([])
      setActiveId(remaining[0]?.id ?? null)
    }
    try {
      await conversations.remove(id)
    } catch (err) {
      // Put it back if the server refused, so the list stays truthful.
      setConversationList(await conversations.list().catch(() => conversationList))
      setError(err instanceof Error ? err.message : 'Could not delete that conversation.')
    }
  }

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
        <Spinner size={20} />
      </div>
    )
  }

  const activeTitle =
    conversationList.find((c) => c.id === activeId)?.title ?? 'New chat'

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      {/* conversation list */}
      <aside
        style={{
          width: 244,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          padding: '20px 14px',
          borderRight: '1px solid var(--border)',
          overflowY: 'auto',
        }}
      >
        <PrimaryButton onClick={newChat} style={{ width: '100%', borderRadius: 9 }}>
          <Icon.Plus size={15} />
          New chat
        </PrimaryButton>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {conversationList.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === activeId}
              onSelect={() => setActiveId(conversation.id)}
              onDelete={() => deleteConversation(conversation.id)}
            />
          ))}
        </div>
      </aside>

      {/* main column */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            padding: '14px 28px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
          }}
        >
          <div
            dir={dirOf(activeTitle)}
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              minWidth: 0,
            }}
          >
            {activeTitle}
          </div>

          <div
            style={{
              marginLeft: 'auto',
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              flexShrink: 0,
            }}
          >
            <HeaderSelect
              icon={<Icon.Database size={15} stroke="var(--accent)" />}
              label="Database"
              value={connectionId}
              onChange={setConnectionId}
              options={connections.map((c) => ({ value: c.id, label: c.name }))}
            />
            <HeaderSelect
              icon={<Icon.Sparkle size={15} stroke="var(--accent)" />}
              label="Model"
              value={modelId}
              onChange={setModelId}
              options={models.map((m) => ({ value: m.id, label: m.name }))}
            />
            <DisclosureChip
              policy={connections.find((c) => c.id === connectionId)?.disclosure_policy}
            />
          </div>
        </header>

        <div style={{ flex: 1, position: 'relative', minHeight: 0 }}>
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            style={{ height: '100%', overflowY: 'auto' }}
          >
            <div
              style={{
                maxWidth: 820,
                margin: '0 auto',
                padding: '28px 28px 16px',
                display: 'flex',
                flexDirection: 'column',
                gap: 22,
              }}
            >
              {error && <ErrorNote>{error}</ErrorNote>}

              {messages.length === 0 && !activeRunId && (
                <Welcome onPick={(text) => void send(text)} />
              )}

              {messages.map((message) => {
                if (message.role === 'USER') {
                  // A run that died before writing an answer has no assistant
                  // message to hang off, so the server attaches it here.
                  // Dropping it was what made a failed turn look like the
                  // question had simply vanished.
                  return (
                    <Fragment key={message.id}>
                      <UserBubble text={message.content ?? ''} />
                      {message.run && isFailure(message.run.status) && (
                        <RunErrorCard run={message.run} />
                      )}
                    </Fragment>
                  )
                }
                if (message.run && isFailure(message.run.status)) {
                  return <RunErrorCard key={message.id} run={message.run} />
                }
                return (
                  <AssistantTurn
                    key={message.id}
                    text={message.content ?? ''}
                    run={message.run}
                  />
                )
              })}

              {activeRunId &&
                (liveText ? (
                  <AssistantTurn text={liveText} run={null} streaming />
                ) : (
                  <ThinkingCard steps={liveSteps} />
                ))}
            </div>
          </div>

          {showJump && (
            <button
              onClick={jumpToEnd}
              aria-label="Jump to latest"
              title="Jump to latest"
              style={{
                position: 'absolute',
                bottom: 14,
                left: '50%',
                transform: 'translateX(-50%)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 32,
                height: 32,
                borderRadius: '50%',
                background: 'var(--panel)',
                border: '1px solid var(--border-strong)',
                color: 'var(--text-dim)',
                cursor: 'pointer',
                boxShadow: '0 4px 14px rgba(0,0,0,0.16)',
              }}
            >
              <Icon.ArrowDown size={15} />
            </button>
          )}
        </div>

        <Composer
          value={draft}
          onChange={setDraft}
          onSubmit={() => void send()}
          busy={!!activeRunId}
        />
      </div>
    </div>
  )
}

/**
 * The opening screen. An empty transcript is the worst place to be told only
 * what the product does, so it also offers questions that are safe to ask of
 * any schema — the first one routes as METADATA and never touches SQL.
 */
const STARTERS = [
  'What tables do I have?',
  'Which tables can I join together?',
  'How many records are in each table?',
  'Show me a sample of rows',
]

function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 14,
        padding: '56px 16px 24px',
        textAlign: 'center',
      }}
    >
      <span
        style={{
          width: 46,
          height: 46,
          borderRadius: 13,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--accent-bg)',
          border: '1px solid var(--accent-border)',
        }}
      >
        <Icon.Sparkle size={22} stroke="var(--accent)" />
      </span>

      <div style={{ fontSize: 19, fontWeight: 700, color: 'var(--text-strong)' }}>
        Ask a question about your data
      </div>
      <p
        style={{
          fontSize: 13.5,
          color: 'var(--text-dim)',
          maxWidth: 440,
          lineHeight: 1.6,
          margin: 0,
        }}
      >
        Raymand writes the SQL, checks it against your schema, runs it on a
        read-only connection, and shows you exactly what it did.
      </p>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 8,
          justifyContent: 'center',
          marginTop: 6,
        }}
      >
        {STARTERS.map((text) => (
          <StarterChip key={text} text={text} onClick={() => onPick(text)} />
        ))}
      </div>
    </div>
  )
}

function StarterChip({ text, onClick }: { text: string; onClick: () => void }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        fontSize: 12.5,
        fontWeight: 500,
        color: hover ? 'var(--text-strong)' : 'var(--text-dim)',
        background: hover ? 'var(--panel-hover)' : 'var(--panel)',
        border: `1px solid ${hover ? 'var(--accent-border)' : 'var(--border)'}`,
        padding: '8px 13px',
        borderRadius: 20,
        cursor: 'pointer',
        transition: 'background .12s ease, border-color .12s ease, color .12s ease',
      }}
    >
      {text}
    </button>
  )
}

function ConversationItem({
  conversation, active, onSelect, onDelete,
}: {
  conversation: ConversationSummary
  active: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const [hover, setHover] = useState(false)
  const [confirming, setConfirming] = useState(false)

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => {
        setHover(false)
        setConfirming(false)
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        width: '100%',
        padding: '9px 8px 9px 10px',
        borderRadius: 8,
        background: active || hover ? 'var(--panel-hover)' : 'transparent',
        transition: 'background .12s ease',
      }}
    >
      <button
        onClick={onSelect}
        title={conversation.title}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flex: 1,
          minWidth: 0,
          padding: 0,
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span
          style={{
            width: 26,
            height: 26,
            borderRadius: 7,
            background: 'var(--panel-alt)',
            color: 'var(--text-dim)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 11,
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          {initialOf(conversation.title)}
        </span>
        <span
          style={{
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            lineHeight: 1.25,
          }}
        >
          <span
            dir={dirOf(conversation.title)}
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: active ? 'var(--text-strong)' : 'var(--text)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {conversation.title}
          </span>
          <span
            style={{
              fontSize: 11,
              color: 'var(--text-faint)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {conversation.preview ?? `${conversation.message_count} messages`}
          </span>
        </span>
      </button>

      {confirming ? (
        <span style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}>
          <button
            className="rm-icon-btn"
            onClick={onDelete}
            title="Confirm delete"
            aria-label="Confirm delete"
            style={iconBtnStyle('var(--red)', 'var(--red-bg)')}
          >
            <Icon.Check size={13} stroke="var(--red)" />
          </button>
          <button
            className="rm-icon-btn"
            onClick={() => setConfirming(false)}
            title="Cancel"
            aria-label="Cancel delete"
            style={iconBtnStyle('var(--text-dim)', 'var(--panel-alt)')}
          >
            <Icon.Close size={12} stroke="var(--text-dim)" />
          </button>
        </span>
      ) : (
        <button
          className="rm-icon-btn"
          onClick={() => setConfirming(true)}
          title="Delete conversation"
          aria-label="Delete conversation"
          style={{
            ...iconBtnStyle('var(--text-faint)', 'var(--panel-alt)'),
            visibility: hover ? 'visible' : 'hidden',
          }}
        >
          <Icon.Trash size={13} stroke="var(--text-faint)" />
        </button>
      )}
    </div>
  )
}

// `--rm-hover-bg` is picked up by the `.rm-icon-btn:hover` rule in styles.css.
function iconBtnStyle(color: string, hoverBg: string): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 24,
    height: 24,
    borderRadius: 6,
    border: 'none',
    background: 'transparent',
    color,
    cursor: 'pointer',
    flexShrink: 0,
    transition: 'background .1s ease',
    ...({ '--rm-hover-bg': hoverBg } as React.CSSProperties),
  }
}

function Composer({
  value, onChange, onSubmit, busy,
}: {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  busy: boolean
}) {
  const [focus, setFocus] = useState(false)
  const ref = useRef<HTMLTextAreaElement>(null)

  // Grow the textarea to fit its content, up to a cap, then scroll.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [value])

  const canSend = value.trim().length > 0 && !busy

  return (
    <div style={{ padding: '12px 28px 22px', flexShrink: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 10,
          background: 'var(--panel)',
          border: `1px solid ${focus ? 'var(--accent)' : 'var(--border-strong)'}`,
          borderRadius: 16,
          padding: '11px 12px 11px 16px',
          boxShadow: focus
            ? '0 0 0 3px var(--accent-bg), 0 6px 20px rgba(0,0,0,0.10)'
            : '0 2px 10px rgba(0,0,0,0.06)',
          transition: 'border-color .15s ease, box-shadow .15s ease',
        }}
      >
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              if (canSend) onSubmit()
            }
          }}
          rows={1}
          dir={dirOf(value)}
          placeholder="Ask about your data…  •  دربارهٔ داده‌هایتان بپرسید…"
          aria-label="Ask about your data"
          style={{
            flex: 1,
            resize: 'none',
            maxHeight: 160,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            color: 'var(--text)',
            fontSize: 14,
            lineHeight: 1.6,
            padding: 0,
          }}
        />
        <button
          onClick={() => canSend && onSubmit()}
          disabled={!canSend}
          aria-label="Send"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 34,
            height: 34,
            borderRadius: 10,
            border: 'none',
            flexShrink: 0,
            background: canSend ? 'var(--accent)' : 'var(--panel-alt)',
            color: canSend ? 'var(--on-accent)' : 'var(--text-faint)',
            cursor: canSend ? 'pointer' : 'not-allowed',
            transition: 'background .15s ease',
          }}
        >
          {busy ? <Spinner size={15} /> : <Icon.Send size={16} />}
        </button>
      </div>
    </div>
  )
}

function HeaderSelect({
  icon, label, value, onChange, options,
}: {
  icon: React.ReactNode
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: 'var(--panel)',
        border: '1px solid var(--border-strong)',
        borderRadius: 9,
        padding: '6px 10px',
        cursor: 'pointer',
      }}
    >
      {icon}
      <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.1 }}>
        <span
          style={{
            fontSize: 9.5,
            color: 'var(--text-faint)',
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
          }}
        >
          {label}
        </span>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-strong)',
            fontSize: 12.5,
            fontWeight: 600,
            outline: 'none',
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {options.length === 0 && <option value="">None configured</option>}
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </span>
    </label>
  )
}

/**
 * If result rows leave the customer's database for a third-party API, the
 * person asking should see that at the moment they ask, not by reading docs.
 */
function DisclosureChip({ policy }: { policy?: string }) {
  if (!policy) return null

  const copy: Record<string, { text: string; tone: string }> = {
    NONE: { text: 'no rows shared with model', tone: 'var(--green)' },
    AGGREGATE: { text: 'only totals shared with model', tone: 'var(--green)' },
    SAMPLE: { text: 'sample rows shared with model', tone: 'var(--amber)' },
    FULL: { text: 'all rows shared with model', tone: 'var(--amber)' },
  }
  const entry = copy[policy]
  if (!entry) return null

  return (
    <span
      title="Controls how much of a query result is sent to the model provider."
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 10.5,
        fontWeight: 500,
        color: entry.tone,
        border: '1px solid var(--border)',
        background: 'var(--panel)',
        padding: '5px 9px',
        borderRadius: 7,
        whiteSpace: 'nowrap',
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: entry.tone,
        }}
      />
      {entry.text}
    </span>
  )
}

function isTerminal(status: string): boolean {
  return ['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(status)
}

/** Terminal states that owe the reader an explanation. */
function isFailure(status: string): boolean {
  return ['FAILED', 'CANCELLED', 'TIMED_OUT'].includes(status)
}
