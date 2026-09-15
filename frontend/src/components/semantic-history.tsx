/**
 * A semantic layer's history: every version, one version's changes, restore.
 *
 * Three screens, each with an address, read by the editor with `useMatch` so
 * the editor stays mounted — and so a person's unsaved edits stay in the tab —
 * while somebody looks back:
 *
 *  - `/sources/:id/semantic/history` — the versions, newest first;
 *  - `/sources/:id/semantic/history?entity=&item=` — one table, column or
 *    metric's changes, from a card's History button;
 *  - `/sources/:id/semantic/history/:version` — what one version changed, and
 *    *Restore as v14*.
 *
 * Every sentence comes from `semantic-changes.ts`, which words the server's
 * change list and never computes one.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, semantic as api } from '../api/client'
import type {
  SemanticChange, SemanticHistoryEntry, SemanticLayer, SemanticVersionSummary,
} from '../api/types'
import {
  Chip, ErrorNote, Field, GhostButton, Icon, Modal, PrimaryButton, Select, Spinner,
  TextArea, dirOf, relativeTime,
} from './ui'
import {
  authorship, byVersion, changeCount, firstLine, groupChanges, historyPath,
} from './semantic-changes'
import type { ChangeGroup, Segment } from './semantic-changes'

export function SemanticHistory({
  connectionId, layer, version, entity, item, dirty, onRestored, onOpenEntity,
}: {
  connectionId: string
  layer: SemanticLayer | null
  /** From the route: one version's page, or `null` for the list. */
  version: number | null
  /** From the query string: the list filtered to one entry. */
  entity: string | null
  item: string | null
  /** Unsaved edits in the editor. A restore would replace them, so it waits. */
  dirty: boolean
  onRestored: (next: SemanticLayer) => void
  onOpenEntity: (table: string) => void
}) {
  if (version !== null) {
    return (
      <VersionPage
        connectionId={connectionId}
        layer={layer}
        version={version}
        dirty={dirty}
        onRestored={onRestored}
        onOpenEntity={onOpenEntity}
      />
    )
  }
  if (entity || item) {
    return <EntryHistory connectionId={connectionId} entity={entity} item={item} />
  }
  return <VersionList connectionId={connectionId} layer={layer} />
}

// ── the list ───────────────────────────────────────────────────────────────
function VersionList({
  connectionId, layer,
}: {
  connectionId: string
  layer: SemanticLayer | null
}) {
  const navigate = useNavigate()
  const [rows, setRows] = useState<SemanticVersionSummary[] | null>(null)
  const [next, setNext] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [more, setMore] = useState(false)

  useEffect(() => {
    let live = true
    setRows(null)
    api.versions(connectionId)
      .then((page) => {
        if (!live) return
        setRows(page.versions)
        setNext(page.next_before)
      })
      .catch(() => live && setError('Could not load this layer’s history.'))
    return () => {
      live = false
    }
    // The revision moves on every write, so a save or a restore elsewhere in
    // the tab refreshes the list without a manual reload.
  }, [connectionId, layer?.revision])

  async function loadMore() {
    if (next === null) return
    setMore(true)
    try {
      const page = await api.versions(connectionId, { before: next })
      setRows((current) => [...(current ?? []), ...page.versions])
      setNext(page.next_before)
    } catch {
      setError('Could not load older versions.')
    } finally {
      setMore(false)
    }
  }

  return (
    <>
      <HistoryHeader
        title="History"
        subtitle="Every version of this layer that reached the model, newest first."
        back={{ label: 'Back to the editor', to: `/sources/${connectionId}/semantic` }}
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {rows === null && !error && <Loading label="Loading history…" />}
      {rows !== null && rows.length === 0 && (
        <Empty>
          Nothing has been saved yet. Every save, generation, restore and delete
          will be listed here.
        </Empty>
      )}
      {rows !== null && rows.length > 0 && (
        <section style={CARD}>
          {rows.map((row, index) => (
            <VersionRow
              key={row.version}
              row={row}
              live={row.version === layer?.published_version}
              last={index === rows.length - 1}
              onOpen={() => navigate(historyPath(connectionId, { version: row.version }))}
            />
          ))}
        </section>
      )}
      {next !== null && (
        <div>
          <GhostButton onClick={loadMore} disabled={more}>
            {more && <Spinner />}
            Older versions
          </GhostButton>
        </div>
      )}
    </>
  )
}

function VersionRow({
  row, live, last, onOpen,
}: {
  row: SemanticVersionSummary
  live: boolean
  last: boolean
  onOpen: () => void
}) {
  const note = firstLine(row.note)
  return (
    <button
      onClick={onOpen}
      className="rm-history-row"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        width: '100%',
        padding: '12px 16px',
        background: 'transparent',
        border: 'none',
        borderBottom: last ? 'none' : '1px solid var(--border)',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <VersionBadge version={row.version} />
      <span style={{ display: 'flex', flexDirection: 'column', gap: 3, flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap',
            fontSize: 13, color: 'var(--text-strong)',
          }}
        >
          <span style={{ fontWeight: 600 }}>
            {sentenceCase(authorship(row.origin, row.published_by_name))}
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }} title={row.created_at}>
            {relativeTime(row.created_at)}
          </span>
        </span>
        {note ? (
          <span
            dir={dirOf(note)}
            style={{
              fontSize: 12.5, color: 'var(--text-dim)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}
          >
            {note}
          </span>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--text-faint)', fontStyle: 'italic' }}>
            No note
          </span>
        )}
      </span>
      <span style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
        {live && <Chip tone="green">● live</Chip>}
        {row.affects_sql && <NumbersChip />}
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)', whiteSpace: 'nowrap' }}>
          {changeCount(row.changes)}
        </span>
      </span>
    </button>
  )
}

// ── one version ────────────────────────────────────────────────────────────
function VersionPage({
  connectionId, layer, version, dirty, onRestored, onOpenEntity,
}: {
  connectionId: string
  layer: SemanticLayer | null
  version: number
  dirty: boolean
  onRestored: (next: SemanticLayer) => void
  onOpenEntity: (table: string) => void
}) {
  const [summary, setSummary] = useState<SemanticVersionSummary | null>(null)
  const [others, setOthers] = useState<SemanticVersionSummary[]>([])
  const [against, setAgainst] = useState<number | null | undefined>(undefined)
  const [changes, setChanges] = useState<SemanticChange[] | null>(null)
  const [base, setBase] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)

  useEffect(() => {
    let live = true
    setSummary(null)
    setAgainst(undefined)
    // The page's own row — the list paged to exactly it, since a summary
    // carries everything the header shows and the document is not needed —
    // and the recent versions it can be compared against.
    Promise.all([
      api.versions(connectionId, { before: version + 1, limit: 1 }),
      api.versions(connectionId, { limit: 100 }),
    ])
      .then(([own, recent]) => {
        if (!live) return
        setOthers(recent.versions.filter((v) => v.version !== version))
        const row = own.versions[0]
        if (row && row.version === version) setSummary(row)
        else setError(`Version ${version} of this layer does not exist.`)
      })
      .catch(() => live && setError('Could not load this version.'))
    return () => {
      live = false
    }
  }, [connectionId, version, layer?.revision])

  useEffect(() => {
    let live = true
    setChanges(null)
    api.changes(connectionId, version, against ?? undefined)
      .then((list) => {
        if (!live) return
        setChanges(list.changes)
        setBase(list.against)
      })
      .catch((err) => {
        if (live) setError(err instanceof ApiError ? err.message : 'Could not load the changes.')
      })
    return () => {
      live = false
    }
  }, [connectionId, version, against])

  const groups = useMemo(() => (changes ? groupChanges(changes) : []), [changes])
  const published = layer?.published_version ?? null
  const isLive = version === published

  return (
    <>
      <HistoryHeader
        title={`Version ${version}`}
        subtitle={
          summary
            ? `${sentenceCase(authorship(summary.origin, summary.published_by_name))} · ${relativeTime(summary.created_at)} · against schema v${summary.schema_version}`
            : undefined
        }
        back={{ label: 'All versions', to: historyPath(connectionId) }}
        badge={
          <>
            {isLive && <Chip tone="green">● live</Chip>}
            {summary?.affects_sql && <NumbersChip />}
          </>
        }
      />

      {error && <ErrorNote>{error}</ErrorNote>}

      {summary?.note && (
        <section style={{ ...CARD, padding: '12px 16px' }}>
          <div style={LABEL}>Note</div>
          <div
            dir={dirOf(summary.note)}
            style={{
              fontSize: 13, lineHeight: 1.6, color: 'var(--text-strong)',
              whiteSpace: 'pre-wrap', marginTop: 4,
            }}
          >
            {summary.note}
          </div>
        </section>
      )}

      <section style={CARD}>
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
            padding: '12px 16px', borderBottom: '1px solid var(--border)',
          }}
        >
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-strong)' }}>
              What changed
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-dim)', marginTop: 2 }}>
              {base === null ? 'Against an empty layer.' : `Against version ${base}.`}
            </div>
          </div>
          {others.length > 0 && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
              <span style={{ color: 'var(--text-dim)' }}>Compare with</span>
              <Select
                value={against === undefined ? '' : String(against)}
                onChange={(e) =>
                  setAgainst(e.target.value === '' ? undefined : Number(e.target.value))
                }
                style={{ width: 'auto', padding: '5px 8px', fontSize: 12 }}
                aria-label="Compare this version with"
              >
                <option value="">
                  {summary?.parent_version ? `v${summary.parent_version} (previous)` : 'previous'}
                </option>
                {others.map((v) => (
                  <option key={v.version} value={v.version}>v{v.version}</option>
                ))}
              </Select>
            </label>
          )}
        </div>

        {changes === null && !error && <Loading label="Reading the changes…" />}
        {changes !== null && changes.length === 0 && (
          <div style={{ padding: '16px', fontSize: 12.5, color: 'var(--text-dim)' }}>
            These two versions say the same thing to the model.
          </div>
        )}
        {groups.map((group, index) => (
          <Group
            key={group.key}
            group={group}
            last={index === groups.length - 1}
            onOpenEntity={onOpenEntity}
          />
        ))}
      </section>

      {summary && (
        <section
          style={{
            ...CARD,
            display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
            padding: '12px 16px',
          }}
        >
          <span style={{ flex: 1, minWidth: 220, fontSize: 12.5, color: 'var(--text-dim)' }}>
            {isLive
              ? 'This is the version the model reads now.'
              : dirty
                ? 'You have unsaved edits in the editor. Save or discard them before restoring, so nothing is replaced without you seeing it.'
                : `Restoring publishes this version again as v${(published ?? 0) + 1}. Nothing is deleted — every version stays here.`}
          </span>
          <PrimaryButton
            onClick={() => setRestoring(true)}
            disabled={isLive || dirty || !layer}
          >
            <Icon.History size={13} />
            Restore as v{(published ?? 0) + 1}
          </PrimaryButton>
        </section>
      )}

      {restoring && layer && (
        <RestoreDialog
          connectionId={connectionId}
          version={version}
          layer={layer}
          onClose={() => setRestoring(false)}
          onRestored={(next) => {
            setRestoring(false)
            onRestored(next)
          }}
        />
      )}
    </>
  )
}

function Group({
  group, last, onOpenEntity,
}: {
  group: ChangeGroup
  last: boolean
  onOpenEntity: (table: string) => void
}) {
  return (
    <div
      style={{
        padding: '12px 16px',
        borderBottom: last ? 'none' : '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', gap: 7,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {group.titleIsCode ? (
          <button
            onClick={() => onOpenEntity(group.entityKey)}
            className="mono"
            title="Open this table in the editor"
            style={{
              padding: 0, border: 'none', background: 'transparent', cursor: 'pointer',
              fontSize: 12.5, fontWeight: 600, color: 'var(--accent)',
            }}
          >
            {group.title}
          </button>
        ) : (
          <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-strong)' }}>
            {group.title}
          </span>
        )}
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 5 }}>
        {group.lines.map((line, index) => (
          <li
            key={`${line.kind}:${line.itemKey}:${index}`}
            style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 12.5, lineHeight: 1.55 }}
          >
            <span
              aria-hidden
              style={{ color: line.affectsSql ? 'var(--amber)' : 'var(--text-faint)', flexShrink: 0 }}
            >
              {line.affectsSql ? '◆' : '·'}
            </span>
            <span style={{ flex: 1, minWidth: 0, color: 'var(--text-strong)' }}>
              <Words segments={line.segments} />
            </span>
            {line.affectsSql && (
              <span style={{ fontSize: 11, color: 'var(--amber)', whiteSpace: 'nowrap' }}>
                changes numbers
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function RestoreDialog({
  connectionId, version, layer, onClose, onRestored,
}: {
  connectionId: string
  version: number
  layer: SemanticLayer
  onClose: () => void
  onRestored: (next: SemanticLayer) => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const next = (layer.published_version ?? 0) + 1

  async function restore() {
    setBusy(true)
    setError(null)
    try {
      onRestored(await api.restore(connectionId, version, {
        baseRevision: layer.revision, note: note.trim(),
      }))
    } catch (err) {
      if (err instanceof ApiError && err.code === 'E_SEMANTIC_CONFLICT') {
        const who = err.detail?.updated_by_name || 'Someone'
        setError(`${who} changed this layer (now v${err.detail?.published_version ?? '?'}) since this page loaded. Go back to the list and look before restoring.`)
      } else {
        setError(err instanceof Error ? err.message : 'Could not restore this version.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Restore version ${version}?`}
      subtitle={`It will be published as v${next}, and it is what the model reads from the next question on.`}
      onClose={onClose}
      width={480}
      footer={
        <>
          <GhostButton onClick={onClose} disabled={busy}>Cancel</GhostButton>
          <PrimaryButton onClick={restore} disabled={busy}>
            {busy && <Spinner />}
            Restore as v{next}
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--text-dim)' }}>
          Anything that no longer matches the schema comes back flagged rather
          than missing, so you can see what the schema has moved past.
        </p>
        <Field label="Why" hint="Optional. The next person to read this history will want to know.">
          <TextArea
            value={note}
            maxLength={2000}
            placeholder="e.g. v12 double-counted refunds"
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        {error && <ErrorNote>{error}</ErrorNote>}
      </div>
    </Modal>
  )
}

// ── one entry's history ────────────────────────────────────────────────────
function EntryHistory({
  connectionId, entity, item,
}: {
  connectionId: string
  entity: string | null
  item: string | null
}) {
  const navigate = useNavigate()
  const [rows, setRows] = useState<SemanticHistoryEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setRows(null)
    api.history(connectionId, { entity: entity ?? undefined, item: item ?? undefined })
      .then((list) => live && setRows(list))
      .catch(() => live && setError('Could not load this history.'))
    return () => {
      live = false
    }
  }, [connectionId, entity, item])

  const subject: Segment[] = item
    ? [{ text: item, code: true }, ...(entity ? [{ text: ' on ' }, { text: entity, code: true }] : [])]
    : [{ text: entity ?? '', code: true }]

  return (
    <>
      <HistoryHeader
        title={<>History of <Words segments={subject} /></>}
        subtitle="Each version that changed it, newest first."
        back={{ label: 'All versions', to: historyPath(connectionId) }}
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {rows === null && !error && <Loading label="Loading history…" />}
      {rows !== null && rows.length === 0 && (
        <Empty>
          No saved version has changed this yet. A table described before
          versions were kept has its history start at its next change.
        </Empty>
      )}
      {rows !== null && rows.length > 0 && (
        <section style={CARD}>
          {byVersion(rows).map(({ version, rows: changes }, index, all) => {
            const first = changes[0]
            const note = firstLine(first.note)
            const lines = groupChanges(changes).flatMap((group) => group.lines)
            return (
              <button
                key={version}
                onClick={() => navigate(historyPath(connectionId, { version }))}
                className="rm-history-row"
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 12, width: '100%',
                  padding: '11px 16px', background: 'transparent', border: 'none',
                  borderBottom: index === all.length - 1 ? 'none' : '1px solid var(--border)',
                  cursor: 'pointer', textAlign: 'left',
                }}
              >
                <VersionBadge version={version} />
                <span style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 0 }}>
                  {lines.map((line, i) => (
                    <span
                      key={`${line.kind}:${line.itemKey}:${i}`}
                      style={{ display: 'flex', gap: 7, fontSize: 13, color: 'var(--text-strong)' }}
                    >
                      <span aria-hidden style={{ color: line.affectsSql ? 'var(--amber)' : 'var(--text-faint)' }}>
                        {line.affectsSql ? '◆' : '·'}
                      </span>
                      <span style={{ minWidth: 0 }}><Words segments={line.segments} /></span>
                    </span>
                  ))}
                  <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
                    {sentenceCase(authorship(first.origin, first.published_by_name))} · {relativeTime(first.created_at)}
                    {note && (
                      <>
                        {' · '}
                        <span dir={dirOf(note)} style={{ color: 'var(--text-dim)' }}>{note}</span>
                      </>
                    )}
                  </span>
                </span>
                {changes.some((c) => c.affects_sql) && <NumbersChip />}
              </button>
            )
          })}
        </section>
      )}
    </>
  )
}

// ── pieces ─────────────────────────────────────────────────────────────────
const CARD: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 12,
  background: 'var(--panel)',
  overflow: 'hidden',
}

const LABEL: React.CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: 0.4,
  textTransform: 'uppercase',
  color: 'var(--text-faint)',
}

function HistoryHeader({
  title, subtitle, back, badge,
}: {
  title: React.ReactNode
  subtitle?: string
  back: { label: string; to: string }
  badge?: React.ReactNode
}) {
  const navigate = useNavigate()
  return (
    <header style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <button
        onClick={() => navigate(back.to)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6, alignSelf: 'flex-start',
          padding: 0, border: 'none', background: 'transparent', cursor: 'pointer',
          fontSize: 12.5, color: 'var(--text-dim)',
        }}
      >
        <Icon.ArrowLeft size={13} />
        {back.label}
      </button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--accent)', display: 'flex' }}>
          <Icon.History size={17} />
        </span>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--text-strong)' }}>
          {title}
        </h2>
        {badge}
      </div>
      {subtitle && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)' }}>{subtitle}</p>
      )}
    </header>
  )
}

function VersionBadge({ version }: { version: number }) {
  return (
    <span
      className="mono"
      style={{
        flexShrink: 0,
        minWidth: 38,
        textAlign: 'center',
        fontSize: 11.5,
        fontWeight: 700,
        padding: '3px 7px',
        borderRadius: 6,
        color: 'var(--accent)',
        background: 'var(--accent-bg)',
        border: '1px solid var(--accent-border)',
      }}
    >
      v{version}
    </span>
  )
}

/** Status is never colour alone: a glyph and a word. */
function NumbersChip() {
  return <Chip tone="amber">◆ changes numbers</Chip>
}

function Words({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((segment, index) =>
        segment.code ? (
          <code
            key={index}
            className="mono"
            // Names and SQL read left to right in either document direction.
            dir="ltr"
            style={{
              fontSize: '0.92em',
              padding: '0 4px',
              borderRadius: 4,
              background: 'var(--panel-alt)',
              border: '1px solid var(--border)',
              unicodeBidi: 'isolate',
            }}
          >
            {segment.text}
          </code>
        ) : (
          <span key={index} dir={dirOf(segment.text)} style={{ unicodeBidi: 'isolate' }}>
            {segment.text}
          </span>
        ),
      )}
    </>
  )
}

function Loading({ label }: { label: string }) {
  return (
    <div
      style={{
        display: 'flex', gap: 9, alignItems: 'center', padding: '18px 16px',
        color: 'var(--text-dim)', fontSize: 13,
      }}
    >
      <Spinner />
      {label}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        border: '1px dashed var(--border-strong)',
        borderRadius: 10,
        padding: '24px 20px',
        textAlign: 'center',
        fontSize: 13,
        lineHeight: 1.6,
        color: 'var(--text-dim)',
      }}
    >
      {children}
    </div>
  )
}

function sentenceCase(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

export { Words as ChangeWords, NumbersChip }
