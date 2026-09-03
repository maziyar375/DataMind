/**
 * The learning loop, at the top level.
 *
 * Everything on this screen already existed — `KnowledgeTab` renders it, and
 * has since the loop was built. What did not exist was a way to *find* it: a
 * curation console with a work queue, a suggestion backlog and a maintenance
 * sweep lived as the fourth tab of one connection's detail pane, three clicks
 * deep and per-connection. Nobody discovers a queue they have to go looking
 * for, and a queue nobody looks at is not a queue.
 *
 * So this page is deliberately **not a rewrite**. It is the same component,
 * with a connection picker in front of it, and `/sources/:id/knowledge` is
 * the same component with the picker skipped — a curator working on one
 * database is not forced up a level to reach the screen they were already on.
 * Two renderings of one console; if they ever disagree, one of them is wrong.
 *
 * The column is what the promotion actually buys: the queue per connection,
 * visible without opening any of them, busiest first.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'
import { connections as api } from '../api/client'
import type { Connection } from '../api/types'
import { useQueue } from '../shell'
import {
  Chip, EmptyState, GlyphBadge, Icon, PrimaryButton, engineHue,
} from '../components/ui'
import { DetailHeader, MasterColumn, MasterItem } from '../components/settings'
import { ListScrim, ListToggle, useListDrawer } from '../components/list-drawer'
import { KnowledgeTab } from '../components/knowledge'
import { byUrgency, forConnection, queueSentence } from '../components/knowledge-queue'
import type { QueueRow } from '../components/knowledge-queue'

export default function KnowledgePage() {
  const [list, setList] = useState<Connection[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const navigate = useNavigate()
  // Below 700px the index is an overlay; above it this does nothing.
  const listDrawer = useListDrawer()
  const match = useMatch('/knowledge/:id')
  const routeId = match?.params.id ?? null
  const { rows: queue } = useQueue()

  const select = useCallback(
    (id: string | null, { replace = false } = {}) =>
      navigate(id ? `/knowledge/${id}` : '/knowledge', { replace }),
    [navigate],
  )

  useEffect(() => {
    api
      .list()
      .then((items) => {
        setList(items)
        // Open where the work is. Landing on the console and being shown a
        // connection with an empty queue, while another has six flags on it,
        // would waste the one decision this screen exists to make for you.
        if (!routeId && items.length > 0) {
          const busiest = byUrgency(
            items.map(
              (item) =>
                queue.find((row) => row.connectionId === item.id)
                ?? { connectionId: item.id, name: item.name, reviews: 0, suggestions: 0 },
            ),
          )[0]
          select(busiest.connectionId, { replace: true })
        }
      })
      .catch(() => undefined)
      .finally(() => setLoading(false))
    // Once: re-running this on every queue change would yank the reader to
    // another connection the moment a flag arrived on it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selected = useMemo(
    () => list.find((c) => c.id === routeId) ?? null,
    [list, routeId],
  )

  const ordered = useMemo(() => {
    const rows: QueueRow[] = list.map(
      (item) =>
        queue.find((row) => row.connectionId === item.id)
        ?? { connectionId: item.id, name: item.name, reviews: 0, suggestions: 0 },
    )
    const needle = filter.trim().toLowerCase()
    return byUrgency(rows).filter(
      (row) => !needle || row.name.toLowerCase().includes(needle),
    )
  }, [list, queue, filter])

  return (
    <div style={{ display: 'flex', height: '100%', width: '100%', minWidth: 0 }}>
      <ListScrim open={listDrawer.open} onClick={listDrawer.close} />
      <MasterColumn
        title="Knowledge"
        open={listDrawer.open}
        icon={<Icon.Flag size={15} />}
        note={loading ? undefined : queueSentence(
          list.map(
            (item) =>
              queue.find((row) => row.connectionId === item.id)
              ?? { connectionId: item.id, name: item.name, reviews: 0, suggestions: 0 },
          ),
        )}
        count={list.length}
        loading={loading}
        query={filter}
        onQuery={setFilter}
        onNew={() => navigate('/sources/new')}
        newLabel="Add a connection"
        empty="Nothing to curate yet — the store belongs to a connection, and you have not added one."
      >
        {ordered.map((row) => {
          const waiting = forConnection(queue, row.connectionId)
          const connection = list.find((c) => c.id === row.connectionId)!
          return (
            <MasterItem
              key={row.connectionId}
              title={row.name}
              // The count, in the row's own subtitle slot: a badge here would
              // be a third mark on a row that already carries a glyph and a
              // state dot, and the queue is the only fact this list is for.
              subtitle={
                waiting
                  ? `${waiting} waiting`
                  : 'nothing waiting'
              }
              active={row.connectionId === routeId}
              tone={waiting ? 'red' : 'neutral'}
              // What the *dot* means, not a repeat of the subtitle beside it:
              // `toneLabel` is read aloud, and the count is already in the
              // line above it. Two identical announcements per row is how a
              // list of twenty becomes unlistenable.
              toneLabel={waiting ? 'Work waiting' : 'Nothing waiting'}
              glyph={
                <GlyphBadge size={30} hue={engineHue(connection.database_type)}>
                  <Icon.Database size={15} />
                </GlyphBadge>
              }
              onClick={() => select(row.connectionId)}
            />
          )
        })}
      </MasterColumn>

      <div
        className="rm-detail-pane"
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}
      >
        {selected ? (
          <>
            {/* Which store is open, said on the screen rather than only in
                the highlighted row of the column. It is the same header the
                settings pages put over a record, for the same reason: a pane
                that shows one connection's data and never names it is one
                screenshot away from being about the wrong database. Inside
                Data sources this is already there, which is why the tab
                itself does not carry one. */}
            <DetailHeader
              leading={
                <ListToggle open={listDrawer.open} label="Knowledge" onClick={listDrawer.toggle} />
              }
              glyph={
                <GlyphBadge size={40} hue={engineHue(selected.database_type)}>
                  <Icon.Database size={19} />
                </GlyphBadge>
              }
              title={selected.name}
              subtitle={`${selected.host}:${selected.port}/${selected.database_name}`}
              chips={<QueueChips queue={queue} connectionId={selected.id} />}
              actions={
                <GhostLink
                  onClick={() => navigate(`/sources/${selected.id}`)}
                  label="Connection settings"
                />
              }
            />
            {/* Keyed on the connection so switching one out replaces the
                console rather than letting the previous store's rows sit
                under the new one's header while it loads. */}
            <KnowledgeTab key={selected.id} connection={selected} />
          </>
        ) : (
          <div
            className="rm-emptyfield"
            style={{ flex: 1, display: 'grid', placeItems: 'center' }}
          >
            <EmptyState
              icon={<Icon.Flag size={20} />}
              title="Teach it what it gets wrong"
              body="Flag a wrong answer in chat and it lands here, next to the questions nothing answers yet. Every connection keeps its own store."
              action={
                <PrimaryButton onClick={() => navigate('/sources/new')}>
                  Add a connection
                </PrimaryButton>
              }
            />
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * The split behind the count, because the two halves mean different things.
 *
 * A flag is a person saying an answer was wrong; a suggestion is a question
 * nothing here answers. One is a complaint and one is an opportunity, and a
 * single number over both tells a curator how much work there is without
 * telling them what kind.
 */
function QueueChips({
  queue, connectionId,
}: {
  queue: QueueRow[]
  connectionId: string
}) {
  const row = queue.find((r) => r.connectionId === connectionId)
  if (!row) return null
  if (row.reviews === 0 && row.suggestions === 0) {
    return <Chip tone="green">Nothing waiting</Chip>
  }
  return (
    <>
      {row.reviews > 0 && (
        <Chip tone="red">
          {row.reviews} {row.reviews === 1 ? 'flag' : 'flags'} raised
        </Chip>
      )}
      {row.suggestions > 0 && (
        <Chip tone="amber">{row.suggestions} suggested</Chip>
      )}
    </>
  )
}

/** The way back to the connection this store belongs to. */
function GhostLink({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rm-tab"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12.5,
        fontWeight: 600,
        padding: '7px 11px',
        borderRadius: 8,
        border: '1px solid var(--border)',
        background: 'transparent',
        color: 'var(--text-dim)',
        cursor: 'pointer',
      }}
    >
      <Icon.Sliders size={13} />
      {label}
    </button>
  )
}
