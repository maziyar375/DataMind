/**
 * The audit log: who did what, and **what was refused**.
 *
 * `audit_logs` has existed since the first migration and had no screen until
 * now, which is a specific kind of failure: a log nobody can read answers
 * *"who did what"* exactly as badly as an empty one. Thirty lines of table is
 * the difference between a table and a feature.
 *
 * Four decisions here, and each is a thing this screen could easily have got
 * wrong:
 *
 * * **Denials are rendered in the same list, in a different tone.** Not a
 *   separate tab. A denial *beside the grant that preceded it* is the story —
 *   "Sara was refused at 14:02, and at 14:06 somebody gave her access" is one
 *   narrative, and two lists would make the reader reconstruct it from
 *   timestamps.
 * * **The actor is a display name, never an address.** The rule the review
 *   queue already follows, and the API enforces it: an audit screen answers
 *   "who did this" with something a person recognises, and an email is a
 *   personal identifier the screen has no need of.
 * * **The action filter is fetched, never hardcoded.** The vocabulary is
 *   closed in the backend and grows a phase at a time, and it is served from
 *   the *rows* — so the dropdown never offers a word that matches nothing and
 *   never misses one that does.
 * * **Paging is keyset.** "Older" carries the `at` of the last row you have, so
 *   reading a log that is being appended to while you read it never skips a
 *   row or shows one twice.
 *
 * The `detail` column is deliberately rendered as-is rather than prettified per
 * action. It carries identifiers and counts — never SQL, never a question,
 * never a key, enforced by the writer — so there is nothing to unfold, and a
 * renderer with a case per action would be a second copy of the vocabulary.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { audit as api, users as usersApi } from '../api/client'
import type { AuditEntry, User } from '../api/types'
import {
  Chip, ErrorNote, Field, GhostButton, Icon, Select, Spinner,
  initialOf, relativeTime,
} from '../components/ui'

/** One page. The log grows without bound, so there is no "everything". */
const PAGE = 100

/**
 * What each outcome looks like.
 *
 * `DENIED` is amber rather than red, and that is a considered choice: a denial
 * is the system **working**, not an error. Red would train an administrator to
 * read a healthy permission model as a wall of failures and stop looking.
 */
const TONE: Record<string, 'green' | 'amber' | 'red' | 'neutral'> = {
  SUCCESS: 'green',
  DENIED: 'amber',
  FAILED: 'red',
}

export default function AuditTab() {
  const [rows, setRows] = useState<AuditEntry[] | null>(null)
  const [actions, setActions] = useState<string[]>([])
  const [people, setPeople] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [action, setAction] = useState('')
  const [outcome, setOutcome] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [actor, setActor] = useState('')
  const [since, setSince] = useState('')

  // Every page fetched so far, so "Older" appends rather than replaces: the
  // story this screen tells is a sequence, and a reader who had to hold the
  // previous page in their head would be reading a worse log than the table.
  const [pages, setPages] = useState<AuditEntry[][]>([])

  const filters = useMemo(
    () => ({
      action: action || undefined,
      outcome: outcome || undefined,
      resource_type: resourceType || undefined,
      actor: actor || undefined,
      since: since ? new Date(since).toISOString() : undefined,
      limit: PAGE,
    }),
    [action, outcome, resourceType, actor, since],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const first = await api.list(filters)
      setPages([first])
      setRows(first)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read the audit log.')
      setRows([])
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    let cancelled = false
    api.actions().then((next) => !cancelled && setActions(next)).catch(() => undefined)
    // A best-effort fetch: an Auditor holds `audit.read` **and** `user.read`,
    // but a custom role need not, and the actor filter is worth losing before
    // the screen is.
    usersApi.list().then((next) => !cancelled && setPeople(next)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  async function older() {
    const flat = pages.flat()
    const last = flat[flat.length - 1]
    if (!last) return
    setLoading(true)
    try {
      const next = await api.list({ ...filters, before: last.at })
      setPages([...pages, next])
      setRows([...flat, ...next])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not read further back.')
    } finally {
      setLoading(false)
    }
  }

  const exhausted =
    pages.length > 0 && (pages[pages.length - 1]?.length ?? 0) < PAGE

  // The resource types that appear in what is on screen. Derived rather than
  // fetched: unlike the action words, this is a short closed list the rows
  // themselves reveal, and a dropdown of eight types where only two have ever
  // been touched is a dropdown of six dead ends.
  const types = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.resource_type).filter(Boolean))].sort(),
    [rows],
  )

  return (
    // No `rm-index` and no `PageHeader`: this is a tab of the Administration
    // section, and the section carries both. Its own wash used to start under
    // the tab strip — a second accent edge across the screen, below the one
    // the strip already draws — and its own <h1> restated the section's at the
    // same size. The sentence that was here is now the section's subtitle
    // while this tab is open, which is where `UsersPage` beside it already
    // put the same thing.
    <div className="rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      {error && <ErrorNote>{error}</ErrorNote>}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
          gap: 12,
          marginBottom: 16,
        }}
      >
        <Field label="Outcome">
          <Select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">Everything</option>
            <option value="SUCCESS">Succeeded</option>
            <option value="DENIED">Refused</option>
            <option value="FAILED">Failed</option>
          </Select>
        </Field>
        <Field label="Action">
          <Select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Any action</option>
            {actions.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Resource">
          <Select value={resourceType} onChange={(e) => setResourceType(e.target.value)}>
            <option value="">Any kind</option>
            {types.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Who">
          <Select value={actor} onChange={(e) => setActor(e.target.value)}>
            <option value="">Anybody</option>
            {people.map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name || person.email}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Since">
          <input
            type="date"
            value={since}
            onChange={(e) => setSince(e.target.value)}
            className="rm-input"
            style={{
              width: '100%',
              padding: '8px 10px',
              borderRadius: 9,
              border: '1px solid var(--border)',
              background: 'var(--panel)',
              color: 'var(--text)',
              font: 'inherit',
              fontSize: 13,
            }}
          />
        </Field>
      </div>

      {rows === null ? (
        <div style={{ display: 'grid', placeItems: 'center', padding: 40 }}>
          <Spinner size={18} />
        </div>
      ) : rows.length === 0 ? (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.6 }}>
          Nothing matches. The log records permission changes, shares, transfers,
          disclosure changes and refusals — if this installation has not had any
          yet, that is what an empty screen here means.
        </p>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {rows.map((row, index) => (
              <Row key={`${row.at}-${index}`} row={row} />
            ))}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 14 }}>
            {!exhausted && (
              <GhostButton onClick={older} disabled={loading}>
                {loading ? <Spinner size={13} /> : <Icon.ArrowDown size={13} />}
                Older
              </GhostButton>
            )}
            <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
              {rows.length} {rows.length === 1 ? 'entry' : 'entries'}
              {exhausted ? ' · that is everything' : ''}
            </span>
          </div>
        </>
      )}
    </div>
  )
}

function Row({ row }: { row: AuditEntry }) {
  const tone = TONE[row.outcome] ?? 'neutral'
  const denied = row.outcome === 'DENIED'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        padding: '9px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        // A denial is tinted rather than boxed off: it belongs in the sequence
        // beside the grant that answered it, and a separate treatment would
        // make the reader reassemble the story from timestamps.
        background: denied ? 'var(--amber-bg)' : 'var(--panel)',
        borderColor: denied ? 'var(--amber-border)' : 'var(--border)',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 24,
          height: 24,
          borderRadius: 7,
          display: 'grid',
          placeItems: 'center',
          background: 'var(--panel-alt)',
          color: 'var(--text-dim)',
          fontSize: 10.5,
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {row.actor ? initialOf(row.actor) : <Icon.Gear size={12} />}
      </span>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
            {row.actor || 'the system'}
          </span>
          <code className="mono" style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
            {row.action}
          </code>
          {row.resource_type && <Chip tone="neutral">{row.resource_type}</Chip>}
        </div>
        {Object.keys(row.detail).length > 0 && (
          <span
            className="mono"
            style={{
              fontSize: 10.5,
              color: 'var(--text-faint)',
              wordBreak: 'break-word',
              lineHeight: 1.5,
            }}
          >
            {Object.entries(row.detail)
              .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
              .join('  ')}
          </span>
        )}
      </div>

      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'flex-end',
          gap: 4,
          flexShrink: 0,
        }}
      >
        <Chip tone={tone}>{denied ? 'Refused' : row.outcome.toLowerCase()}</Chip>
        <span
          style={{ fontSize: 10.5, color: 'var(--text-faint)' }}
          title={new Date(row.at).toLocaleString()}
        >
          {relativeTime(row.at)}
        </span>
      </div>
    </div>
  )
}
