/**
 * The threads, newest first, cut into the periods people actually think in.
 *
 * A chat list is unlike the other indexes in the product in one way that
 * decides its shape: nobody remembers what they called a thread, they remember
 * *when* they had it. So the ordering is recency and the grouping is recency.
 * The rest is the furniture every other index here has and this one lacked: a
 * heading with a count, a filter once the list is long enough to need one, and
 * something to read when it is empty.
 *
 * A row is one line and that line is the question — see `ConversationItem`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { Connection, ConversationSummary } from '../api/types'
import { GlyphBadge, Icon, ListNewButton, SearchField, dirOf, engineHue, iconBtnStyle } from './ui'
import { LIST_DRAWER_ID } from './list-drawer'

const DAY = 86_400_000

function bucketOf(iso: string, now: number): string {
  const age = now - new Date(iso).getTime()
  if (age < DAY) return 'Today'
  if (age < 7 * DAY) return 'Previous 7 days'
  if (age < 30 * DAY) return 'Previous 30 days'
  return 'Older'
}

const BUCKETS = ['Today', 'Previous 7 days', 'Previous 30 days', 'Older']

export function ConversationSidebar({
  conversations: list, connections, activeId, open, onSelect, onNew, onDelete,
  onRename,
}: {
  conversations: ConversationSummary[]
  /** To name the data source each thread is bound to, on its row. */
  connections: Connection[]
  activeId: string | null
  /** Below 700px this list is an overlay — see `list-drawer.tsx`. */
  open?: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}) {
  const [query, setQuery] = useState('')

  const byId = useMemo(
    () => new Map(connections.map((connection) => [connection.id, connection])),
    [connections],
  )

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    // Over what the rows actually show: the title and the source name. It used
    // to search the preview line too, and once that line stopped being drawn a
    // match on it would have been a row that appeared for no visible reason.
    const matched = list.filter((conversation) => {
      if (!needle) return true
      if (conversation.title.toLowerCase().includes(needle)) return true
      const source = byId.get(conversation.default_connection_id ?? '')
      return source !== undefined && source.name.toLowerCase().includes(needle)
    })
    const now = Date.now()
    const sorted = [...matched].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )
    return BUCKETS.map((label) => ({
      label,
      items: sorted.filter((conversation) => bucketOf(conversation.updated_at, now) === label),
    })).filter((group) => group.items.length > 0)
  }, [list, byId, query])

  return (
    <aside
      id={LIST_DRAWER_ID}
      className={`rm-chats${open ? ' is-open' : ''}`}
      style={{
        width: 252,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        background: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--border)',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          padding: '16px 12px 12px',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '0 2px' }}>
          <span style={{ fontSize: 14.5, fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text-strong)' }}>
            Chats
          </span>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: 'var(--text-faint)',
              background: 'var(--panel-alt)',
              padding: '2px 7px',
              borderRadius: 20,
            }}
          >
            {list.length}
          </span>
        </div>

        <ListNewButton label="New chat" onClick={onNew} />

        {/* Offered once the list outgrows a glance — the same rule the
            Dashboards toolbar follows for its archived filter. */}
        {list.length > 7 && (
          <div className="rm-chats-search">
            <SearchField
              value={query}
              onChange={setQuery}
              ariaLabel="Search chats"
              placeholder="Search chats…"
            />
          </div>
        )}
      </div>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 10px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}
      >
        {list.length === 0 ? (
          <p style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            No chats yet. Ask a question in plain language and DataMind writes
            the SQL, runs it read-only, and shows you both.
          </p>
        ) : groups.length === 0 ? (
          <p style={{ fontSize: 12.5, color: 'var(--text-dim)', padding: '4px 6px', margin: 0 }}>
            Nothing matches “{query.trim()}”.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span className="rm-chats-caption">{group.label}</span>
              {group.items.map((conversation) => (
                <ConversationItem
                  key={conversation.id}
                  conversation={conversation}
                  connection={byId.get(conversation.default_connection_id ?? '') ?? null}
                  active={conversation.id === activeId}
                  onSelect={() => onSelect(conversation.id)}
                  onDelete={() => onDelete(conversation.id)}
                  onRename={(title) => onRename(conversation.id, title)}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </aside>
  )
}

function ConversationItem({
  conversation, connection, active, onSelect, onDelete, onRename,
}: {
  conversation: ConversationSummary
  /** The data source the thread is bound to, resolved by the list. */
  connection: Connection | null
  active: boolean
  onSelect: () => void
  onDelete: () => void
  onRename: (title: string) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(conversation.title)
  const inputRef = useRef<HTMLInputElement>(null)

  function startEdit() {
    setValue(conversation.title)
    setEditing(true)
  }

  function commit() {
    setEditing(false)
    onRename(value)
  }

  // Select the whole title on entry, so a rename can start by just typing.
  useEffect(() => {
    if (editing) inputRef.current?.select()
  }, [editing])

  return (
    <div
      onMouseLeave={() => setConfirming(false)}
      className={`rm-chat-item${active ? ' is-on' : ''}`}
    >
      {editing ? (
        <input
          ref={inputRef}
          value={value}
          dir={dirOf(value)}
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setEditing(false)
            }
          }}
          style={{
            flex: 1,
            minWidth: 0,
            padding: '3px 7px',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-strong)',
            background: 'var(--input-bg)',
            border: '1px solid var(--accent)',
            borderRadius: 6,
            outline: 'none',
          }}
        />
      ) : (
        // The question, and the database it was asked of.
        //
        // The row used to carry an initial badge and a line of the answer's
        // opening words. Both are gone: the badge repeated the letter the
        // title started with two characters to its right, and the preview was
        // a sentence fragment cut mid-word, so twenty of them stacked read as
        // noise. Title alone was worse in the other direction — a column of
        // bare sentences reads as prose, not as a list of things you can open.
        //
        // So the second line is an *attribute* rather than a sentence: which
        // data source the thread is bound to. A thread is pinned to one
        // connection for its whole life (`_bind_connection`), so it is a fact
        // about the conversation and not a detail of its last turn — and it is
        // the thing you actually need when two threads ask the same question
        // of staging and of production. The engine-tinted glyph says the same
        // thing at a glance, in the colour the Data sources index uses.
        <button
          onClick={onSelect}
          onDoubleClick={startEdit}
          title={conversation.title}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            flex: 1,
            minWidth: 0,
            padding: 0,
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <GlyphBadge
            hue={connection ? engineHue(connection.database_type) : undefined}
            size={26}
            radius={8}
          >
            <Icon.Database size={13} />
          </GlyphBadge>
          <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: 1 }}>
            <span
              dir={dirOf(conversation.title)}
              style={{
                fontSize: 12.5,
                fontWeight: active ? 600 : 500,
                lineHeight: 1.3,
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
                fontSize: 10.5,
                lineHeight: 1.3,
                color: 'var(--text-faint)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {connection?.name ?? 'No data source'}
            </span>
          </span>
        </button>
      )}

      {editing ? null : confirming ? (
        // Same floating cluster, pinned open: a confirmation that moved the
        // title out from under the pointer would be answering a different
        // question than the one it asked.
        <span
          className="rm-chat-actions is-open"
          style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}
        >
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
        // Revealed on approach — and kept for the keyboard, which never
        // produces a hover, by `:focus-within` in the stylesheet.
        <span
          className="rm-chat-actions"
          style={{ display: 'flex', alignItems: 'center', gap: 2, flexShrink: 0 }}
        >
          <button
            className="rm-icon-btn"
            onClick={startEdit}
            title="Rename conversation"
            aria-label="Rename conversation"
            style={iconBtnStyle('var(--text-faint)', 'var(--panel-alt)')}
          >
            <Icon.Pencil size={13} stroke="var(--text-faint)" />
          </button>
          <button
            className="rm-icon-btn"
            onClick={() => setConfirming(true)}
            title="Delete conversation"
            aria-label="Delete conversation"
            style={iconBtnStyle('var(--text-faint)', 'var(--panel-alt)')}
          >
            <Icon.Trash size={13} stroke="var(--text-faint)" />
          </button>
        </span>
      )}
    </div>
  )
}
