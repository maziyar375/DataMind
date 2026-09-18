/**
 * `<AccessPanel>` — who can reach this, and one place to change it.
 *
 * **One component for every resource type**, mounted with the resource's own
 * API path: `connections/{id}`, `connections/{id}/knowledge`,
 * `connections/{id}/semantic`, `dashboards/{id}`, `reports/{id}`,
 * `llm-configs/{id}`. Eight copies of a share dialog would be eight places for
 * the options to drift from what the server accepts.
 *
 * What it is built around, after a test pass found the previous version hard
 * to use (docs/plans/access-control-fixes.md, X1–X3):
 *
 * * **Three levels, named the way people say them.** The server sends the
 *   levels this type offers and a label for each — *Can view*, *Can edit*,
 *   *Full access*; a model configuration offers *Can use* alone — beside the
 *   sentence each one means. The five-word lattice stays the API's and the
 *   audit log's vocabulary; nobody sharing a dashboard has to learn it.
 * * **Share inline, not in a dialog on a dialog.** Search people and teams,
 *   pick a level, press Share. The directory is `GET /directory`, which any
 *   signed-in person may read — the old dialog read `/users`, which needs
 *   `user.read`, so everyone but an administrator could share with teams only.
 * * **Dashboard access and data access are different, and the panel says so
 *   at the moment it matters.** When the person you picked could not read the
 *   data behind a dashboard or report, it names the data source and — if you
 *   can share that too — offers one checkbox to do both. The share is never
 *   blocked: a board over four databases is a fine thing to share with
 *   somebody who may read two of them.
 * * **Rows show kind and level, and the level is the control.** Raising
 *   somebody is a new grant row on the server, so `change()` grants and
 *   revokes in the order that fails safe in each direction.
 * * **Somebody who cannot share is told who can**, by name, instead of being
 *   shown a form they cannot use.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { access } from '../api/client'
import { accessOf } from '../permissions'
import type {
  Actions, DenialReason, Directory, DirectoryEntry, Grant, Reach,
} from '../api/types'
import {
  Chip, DangerButton, ErrorNote, GhostButton, GlyphBadge, HoverButton, Icon, Modal,
  PrimaryButton, Select, Spinner, identityHue, initialOf, inputStyle,
} from './ui'

/** The five, in lattice order. Used to rank, never to offer. */
const ORDER = ['describe', 'select', 'modify', 'delete', 'manage']

/** A data source by id and name — what a share check names. */
export interface DataSourceRef {
  id: string
  name: string
}

/**
 * Which data sources the named person or team could **not** read.
 *
 * Passed by the two artifacts whose content comes from data sources the
 * sharer may not have shared — a dashboard (one per tile) and a report (one).
 */
export type WarnFn = (principal: { user_id?: string; team_id?: string }) => Promise<DataSourceRef[]>

function principalOf(entry: { id: string; kind: string }): { user_id?: string; team_id?: string } {
  return entry.kind === 'TEAM' ? { team_id: entry.id } : { user_id: entry.id }
}

/** The highest privilege held, for "You can view this". */
function highest(privileges: string[]): string | null {
  return [...ORDER].reverse().find((p) => privileges.includes(p)) ?? null
}

function Avatar({ id, name, kind, size = 30 }: {
  id: string
  name: string
  kind: string
  size?: number
}) {
  return (
    <GlyphBadge hue={identityHue(id)} size={size} radius={kind === 'TEAM' ? 8 : size / 2}>
      {kind === 'TEAM' ? <Icon.Users size={Math.round(size * 0.45)} /> : initialOf(name)}
    </GlyphBadge>
  )
}

export function AccessPanel({
  base, title, description, onChanged, warn, extraActions,
}: {
  /**
   * The resource's path, **without** a leading slash and without `/api/v1`:
   * `connections/{id}`, `dashboards/{id}`. The same string the backend mounts
   * these routes under, which is what makes one panel serve every type.
   */
  base: string
  title?: string
  description?: string
  /** Called after any change, so a parent showing a share count can re-read. */
  onChanged?: () => void
  /** See `WarnFn`. A prop rather than a branch on `base`. */
  warn?: WarnFn
  /** *Transfer ownership*, rendered at the foot of the panel for a `manage` holder. */
  extraActions?: React.ReactNode
}) {
  const [actions, setActions] = useState<Actions | null>(null)
  const [grants, setGrants] = useState<Grant[] | null>(null)
  const [directory, setDirectory] = useState<Directory | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revoking, setRevoking] = useState<Grant | null>(null)
  /** The grant id whose level is mid-change, so its row can say so. */
  const [changing, setChanging] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const next = await access.grants(base)
    setGrants(next)
    onChanged?.()
    return next
  }, [base, onChanged])

  useEffect(() => {
    let cancelled = false
    access.actions(base)
      .then((next) => !cancelled && setActions(next))
      .catch(() => !cancelled && setActions(null))
    return () => {
      cancelled = true
    }
  }, [base])

  const mayShare = actions?.can?.share ?? false

  useEffect(() => {
    if (!mayShare) return
    let cancelled = false
    access.grants(base)
      .then((next) => !cancelled && setGrants(next))
      .catch(() => {
        if (cancelled) return
        setError('Could not load who has access.')
        setGrants([])
      })
    access.directory()
      .then((next) => !cancelled && setDirectory(next))
      .catch(() => !cancelled && setDirectory({ people: [], teams: [] }))
    return () => {
      cancelled = true
    }
  }, [base, mayShare])

  /**
   * Change what one grant allows **without** making somebody revoke and
   * re-share. A grant row is keyed by its privilege, so this is two calls,
   * ordered so that a failure half-way leaves the safe state: widening grants
   * first (holding both *is* the wider one), narrowing revokes first (holding
   * nothing is too little, never too much). The panel reloads either way.
   */
  const change = useCallback(
    async (grant: Grant, next: string) => {
      if (!grant.id || next === grant.privilege) return
      const widening = ORDER.indexOf(next) > ORDER.indexOf(grant.privilege)
      const principal =
        grant.principal_kind === 'TEAM'
          ? { team_id: grant.principal_id }
          : { user_id: grant.principal_id }
      setChanging(grant.id)
      setError(null)
      try {
        if (widening) {
          await access.grant(base, { privilege: next, ...principal })
          await access.revoke(base, grant.id)
        } else {
          await access.revoke(base, grant.id)
          await access.grant(base, { privilege: next, ...principal })
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not change their access.')
      } finally {
        setChanging(null)
        await reload().catch(() => undefined)
      }
    },
    [base, reload],
  )

  const rows = useMemo(() => {
    const rank = (g: Grant) => (g.path === 'owner' ? 0 : g.principal_kind === 'TEAM' ? 1 : 2)
    return [...(grants ?? [])].sort(
      (a, b) => rank(a) - rank(b) || a.principal_name.localeCompare(b.principal_name),
    )
  }, [grants])

  const members = useMemo(
    () => new Map((directory?.teams ?? []).map((team) => [team.id, team.members ?? 0])),
    [directory],
  )

  if (actions === null) return <Spinner />
  if (!mayShare) return <ReadOnlyNote actions={actions} />

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {error && <ErrorNote>{error}</ErrorNote>}
      {description && (
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {description}
        </p>
      )}

      <ShareComposer
        base={base}
        actions={actions}
        directory={directory}
        existing={grants ?? []}
        warn={warn}
        onError={setError}
        onShared={async () => {
          setError(null)
          await reload().catch(() => undefined)
        }}
      />

      <div>
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
            color: 'var(--text-faint)',
            margin: '2px 2px 8px',
          }}
        >
          {title ? 'Who has access' : 'People with access'}
        </div>
        {grants === null ? (
          <Spinner />
        ) : (
          <div
            role="list"
            aria-label="Who has access"
            style={{
              display: 'flex',
              flexDirection: 'column',
              border: '1px solid var(--border)',
              borderRadius: 12,
              background: 'var(--panel)',
              overflow: 'hidden',
            }}
          >
            {rows.map((grant, index) => (
              <GrantRow
                key={grant.id ?? `owner-${grant.principal_id}`}
                grant={grant}
                actions={actions}
                members={members.get(grant.principal_id)}
                first={index === 0}
                busy={changing === grant.id}
                onChange={(next) => change(grant, next)}
                onRevoke={() => setRevoking(grant)}
              />
            ))}
            {rows.length === 1 && (
              <div
                style={{
                  padding: '10px 14px',
                  fontSize: 12,
                  color: 'var(--text-faint)',
                  borderTop: '1px solid var(--border)',
                }}
              >
                Nobody else yet.
              </div>
            )}
          </div>
        )}
      </div>

      {extraActions && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>{extraActions}</div>
      )}

      {revoking && (
        <RevokeModal
          base={base}
          grant={revoking}
          label={actions.labels[revoking.privilege] ?? revoking.privilege}
          onClose={() => setRevoking(null)}
          onRevoked={async () => {
            setRevoking(null)
            setError(null)
            await reload().catch(() => undefined)
          }}
        />
      )}
    </div>
  )
}

/**
 * What somebody who cannot change access is shown: what they *can* do, and
 * who decides. A sentence, not a disabled form.
 */
function ReadOnlyNote({ actions }: { actions: Actions }) {
  const top = highest(actions.privileges)
  const label = top ? actions.labels[top] : null
  const can =
    top === 'describe' || !label
      ? 'You can see that this exists'
      : `You ${label.charAt(0).toLowerCase()}${label.slice(1)} this`
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        padding: '12px 14px',
        borderRadius: 10,
        background: 'var(--panel-alt)',
        border: '1px solid var(--border)',
      }}
    >
      <span aria-hidden style={{ color: 'var(--text-faint)', marginTop: 1 }}>
        <Icon.Lock size={14} />
      </span>
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6 }}>
        {can}.{' '}
        {actions.owner_name ? (
          <>
            <strong style={{ color: 'var(--text-strong)' }}>{actions.owner_name}</strong>{' '}
            owns it and decides who else has access.
          </>
        ) : (
          'Its owner decides who else has access.'
        )}
      </p>
    </div>
  )
}

function GrantRow({
  grant, actions, members, first, busy, onChange, onRevoke,
}: {
  grant: Grant
  actions: Actions
  members?: number
  first: boolean
  busy: boolean
  onChange: (next: string) => void
  onRevoke: () => void
}) {
  const isOwner = grant.path === 'owner'
  // The levels on offer, plus — if this row holds one the dialog no longer
  // offers, say `describe` granted through the API — the one in force, so the
  // control never shows a different level from the one the row has.
  const options = actions.levels.includes(grant.privilege)
    ? actions.levels
    : [...actions.levels, grant.privilege].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b))
  const detail =
    grant.principal_kind === 'TEAM'
      ? `Team${members !== undefined ? ` · ${members} ${members === 1 ? 'person' : 'people'}` : ''}`
      : grant.principal_kind === 'SERVICE'
        ? 'Service account'
        : null

  return (
    <div
      role="listitem"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 11,
        padding: '9px 12px 9px 14px',
        borderTop: first ? 'none' : '1px solid var(--border)',
        minHeight: 52,
      }}
    >
      <Avatar id={grant.principal_id} name={grant.principal_name} kind={grant.principal_kind} />
      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {grant.principal_name}
        </span>
        {detail && (
          <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>{detail}</span>
        )}
      </span>

      {isOwner ? (
        // Ownership is not a grant: it is moved by transferring, never revoked.
        <span style={{ fontSize: 12, color: 'var(--text-dim)', padding: '0 10px' }}>Owner</span>
      ) : (
        <>
          <Select
            aria-label={`What ${grant.principal_name} can do`}
            title={actions.meanings[grant.privilege]}
            disabled={busy}
            value={grant.privilege}
            onChange={(event) => onChange(event.target.value)}
            style={{ width: 'auto', minWidth: 118, padding: '5px 8px', fontSize: 12.5 }}
          >
            {options.map((name) => (
              <option key={name} value={name} title={actions.meanings[name]}>
                {actions.labels[name] ?? name}
              </option>
            ))}
          </Select>
          <HoverButton
            onClick={onRevoke}
            disabled={busy}
            title={`Remove ${grant.principal_name}’s access`}
            aria-label={`Remove ${grant.principal_name}’s access`}
            baseStyle={{
              display: 'grid',
              placeItems: 'center',
              width: 28,
              height: 28,
              borderRadius: 7,
              border: 'none',
              background: 'transparent',
              color: 'var(--text-faint)',
              cursor: busy ? 'default' : 'pointer',
            }}
            hoverStyle={{ color: 'var(--red)', background: 'var(--red-bg)' }}
          >
            {busy ? <Spinner size={12} /> : <Icon.Close size={13} />}
          </HoverButton>
        </>
      )}
    </div>
  )
}

/** One data source the picked person could not read, and what to do about it. */
interface DataGap extends DataSourceRef {
  /** The sharer may share this data source too. */
  canShare: boolean
  /** Who owns it, when the sharer may not — so the note can say whom to ask. */
  owner: string | null
  include: boolean
}

/**
 * Search, pick a level, Share — the whole act on one line.
 *
 * The level's meaning is shown beneath once somebody is picked, so the three
 * short labels never have to carry the full rule on their own.
 */
function ShareComposer({
  base, actions, directory, existing, warn, onShared, onError,
}: {
  base: string
  actions: Actions
  directory: Directory | null
  existing: Grant[]
  warn?: WarnFn
  onShared: () => void | Promise<void>
  onError: (message: string | null) => void
}) {
  const [picked, setPicked] = useState<DirectoryEntry | null>(null)
  const [level, setLevel] = useState(actions.levels[0] ?? 'select')
  const [busy, setBusy] = useState(false)
  const [gaps, setGaps] = useState<DataGap[]>([])

  // Ask the server what the picked person could not read, then — for each such
  // data source — whether the sharer may share it too. Never blocking: a
  // failed check leaves the note off and Share exactly as usable as it was.
  useEffect(() => {
    setGaps([])
    if (!warn || !picked) return
    let cancelled = false
    void (async () => {
      try {
        const refs = await warn(principalOf(picked))
        const checked = await Promise.all(
          refs.map(async (ref): Promise<DataGap> => {
            try {
              const theirs = await access.actions(`connections/${ref.id}`)
              return { ...ref, canShare: theirs.can.share, owner: theirs.owner_name, include: false }
            } catch {
              return { ...ref, canShare: false, owner: null, include: false }
            }
          }),
        )
        if (!cancelled) setGaps(checked)
      } catch {
        if (!cancelled) setGaps([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [warn, picked])

  const holders = useMemo(
    () => new Map(existing.map((grant) => [grant.principal_id, grant])),
    [existing],
  )
  const options = useMemo(
    () => [
      ...(directory?.teams ?? []),
      ...(directory?.people ?? []).filter((person) => !person.is_you),
    ],
    [directory],
  )

  async function submit() {
    if (!picked) return
    setBusy(true)
    onError(null)
    const principal = principalOf(picked)
    try {
      await access.grant(base, { privilege: level, ...principal })
      for (const gap of gaps) {
        if (gap.include && gap.canShare) {
          await access.grant(`connections/${gap.id}`, { privilege: 'select', ...principal })
        }
      }
      setPicked(null)
      setGaps([])
      await onShared()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not share this.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 220px', minWidth: 0 }}>
          <PrincipalPicker
            options={options}
            holders={holders}
            labels={actions.labels}
            loading={directory === null}
            picked={picked}
            onPick={setPicked}
          />
        </div>
        {actions.levels.length > 1 ? (
          <Select
            aria-label="What they can do"
            value={level}
            onChange={(event) => setLevel(event.target.value)}
            style={{ width: 'auto', minWidth: 124, height: 38 }}
          >
            {actions.levels.map((name) => (
              <option key={name} value={name}>
                {actions.labels[name] ?? name}
              </option>
            ))}
          </Select>
        ) : (
          <span
            style={{
              height: 38,
              display: 'inline-flex',
              alignItems: 'center',
              padding: '0 10px',
              fontSize: 12.5,
              color: 'var(--text-dim)',
            }}
          >
            {actions.labels[level] ?? level}
          </span>
        )}
        <PrimaryButton
          disabled={!picked || busy}
          onClick={() => void submit()}
          style={{ height: 38, padding: '0 16px' }}
        >
          {busy ? <Spinner /> : null}
          Share
        </PrimaryButton>
      </div>

      {picked && (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.55 }}>
          <strong style={{ color: 'var(--text-dim)', fontWeight: 600 }}>
            {actions.labels[level] ?? level}:
          </strong>{' '}
          {actions.meanings[level]}
        </p>
      )}

      {picked && gaps.length > 0 && (
        <DataGapNote
          who={picked.name}
          gaps={gaps}
          onToggle={(id, include) =>
            setGaps((current) => current.map((g) => (g.id === id ? { ...g, include } : g)))
          }
        />
      )}
    </div>
  )
}

/**
 * *"Maya can't see the data in Sales warehouse"* — and the one click that
 * fixes it, when the sharer is allowed to.
 *
 * Unchecked by default: sharing a dashboard is not a decision about who may
 * query a database, and the second decision should be made on purpose.
 */
function DataGapNote({
  who, gaps, onToggle,
}: {
  who: string
  gaps: DataGap[]
  onToggle: (id: string, include: boolean) => void
}) {
  const names = gaps.map((g) => g.name)
  const listed =
    names.length <= 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '11px 13px',
        borderRadius: 10,
        border: '1px solid var(--amber-border)',
        background: 'var(--amber-bg)',
      }}
    >
      <div style={{ display: 'flex', gap: 9, alignItems: 'flex-start' }}>
        <span aria-hidden style={{ color: 'var(--amber)', flexShrink: 0, marginTop: 1 }}>
          <Icon.Database size={14} />
        </span>
        <span style={{ fontSize: 12.5, color: 'var(--text)', lineHeight: 1.55 }}>
          {who} can’t see the data in{' '}
          <strong style={{ color: 'var(--text-strong)' }}>{listed}</strong>. Sharing this
          doesn’t share the data behind it — where it would show numbers, they’ll see a lock.
        </span>
      </div>
      {gaps.map((gap) =>
        gap.canShare ? (
          <label
            key={gap.id}
            style={{
              display: 'flex',
              gap: 8,
              alignItems: 'center',
              fontSize: 12.5,
              color: 'var(--text-strong)',
              cursor: 'pointer',
              paddingLeft: 23,
            }}
          >
            <input
              type="checkbox"
              checked={gap.include}
              onChange={(event) => onToggle(gap.id, event.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            <span>
              Also let {who} query <strong>{gap.name}</strong>
            </span>
          </label>
        ) : (
          <span key={gap.id} style={{ fontSize: 12, color: 'var(--text-dim)', paddingLeft: 23 }}>
            Only {gap.owner ?? 'its owner'} can share {gap.name} — ask them if {who} should see
            the numbers.
          </span>
        ),
      )}
    </div>
  )
}

/**
 * The people-and-teams search. Inline rather than a floating menu, so it is
 * never clipped by the dialog it sits in; the list appears while searching and
 * goes away once somebody is picked.
 */
function PrincipalPicker({
  options, holders, labels, loading, picked, onPick,
}: {
  options: DirectoryEntry[]
  holders: Map<string, Grant>
  labels: Record<string, string>
  loading: boolean
  picked: DirectoryEntry | null
  onPick: (entry: DirectoryEntry | null) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = needle
      ? options.filter((option) => option.name.toLowerCase().includes(needle))
      : options
    // Those who already have access sink to the bottom, greyed and saying what
    // they hold — kept so the list never looks filtered for no reason.
    return [
      ...pool.filter((option) => !holders.has(option.id)),
      ...pool.filter((option) => holders.has(option.id)),
    ].slice(0, 50)
  }, [options, holders, query])

  const selectable = matches.filter((option) => !holders.has(option.id))

  function choose(entry: DirectoryEntry) {
    onPick(entry)
    setQuery('')
    setOpen(false)
  }

  if (picked) {
    return (
      <div
        style={{
          ...inputStyle,
          height: 38,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '0 6px 0 7px',
        }}
      >
        <Avatar id={picked.id} name={picked.name} kind={picked.kind} size={24} />
        <span
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--text-strong)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {picked.name}
        </span>
        {picked.kind === 'TEAM' && <Chip tone="accent" small>Team</Chip>}
        <button
          type="button"
          aria-label={`Clear ${picked.name}`}
          onClick={() => onPick(null)}
          style={{
            display: 'grid',
            placeItems: 'center',
            width: 24,
            height: 24,
            border: 'none',
            borderRadius: 6,
            background: 'transparent',
            color: 'var(--text-faint)',
            cursor: 'pointer',
          }}
        >
          <Icon.Close size={12} />
        </button>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ position: 'relative' }}>
        <span
          aria-hidden
          style={{
            position: 'absolute',
            left: 11,
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--text-faint)',
            display: 'flex',
          }}
        >
          <Icon.Search size={14} />
        </span>
        <input
          role="combobox"
          aria-expanded={open}
          aria-controls="access-principal-list"
          aria-label="Add people or teams"
          placeholder={loading ? 'Loading people…' : 'Add people or teams…'}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setActive(0)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              setOpen(true)
              setActive((index) => Math.min(index + 1, Math.max(selectable.length - 1, 0)))
            } else if (event.key === 'ArrowUp') {
              event.preventDefault()
              setActive((index) => Math.max(index - 1, 0))
            } else if (event.key === 'Enter' && open && selectable[active]) {
              event.preventDefault()
              choose(selectable[active])
            } else if (event.key === 'Escape') {
              setOpen(false)
            }
          }}
          style={{ ...inputStyle, height: 38, paddingLeft: 32 }}
        />
      </div>
      {open && !loading && (
        <div
          id="access-principal-list"
          role="listbox"
          aria-label="People and teams"
          style={{
            display: 'flex',
            flexDirection: 'column',
            maxHeight: 232,
            overflowY: 'auto',
            scrollbarGutter: 'stable',
            padding: 4,
            borderRadius: 10,
            border: '1px solid var(--border)',
            background: 'var(--panel)',
            boxShadow: '0 8px 24px -12px rgba(0,0,0,0.35)',
          }}
        >
          {matches.length === 0 && (
            <span style={{ fontSize: 12.5, color: 'var(--text-faint)', padding: '8px 10px' }}>
              {query.trim() ? `Nobody matches “${query.trim()}”.` : 'Nobody else here yet.'}
            </span>
          )}
          {matches.map((option) => {
            const held = holders.get(option.id)
            const index = selectable.indexOf(option)
            const isActive = index === active && !held
            const hint = held
              ? held.path === 'owner'
                ? 'Owner'
                : `Has access · ${labels[held.privilege] ?? held.privilege}`
              : option.kind === 'TEAM'
                ? `Team · ${option.members ?? 0} ${option.members === 1 ? 'person' : 'people'}`
                : option.kind === 'SERVICE'
                  ? 'Service account'
                  : null
            return (
              <button
                key={`${option.kind}-${option.id}`}
                type="button"
                role="option"
                aria-selected={isActive}
                aria-disabled={held !== undefined}
                disabled={held !== undefined}
                // `mousedown`, so the pick lands before the input's blur
                // closes the list.
                onMouseDown={(event) => {
                  event.preventDefault()
                  if (!held) choose(option)
                }}
                onMouseEnter={() => index >= 0 && setActive(index)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '6px 8px',
                  borderRadius: 8,
                  border: 'none',
                  textAlign: 'left',
                  background: isActive ? 'var(--accent-bg)' : 'transparent',
                  color: 'var(--text-strong)',
                  cursor: held ? 'default' : 'pointer',
                  opacity: held ? 0.5 : 1,
                }}
              >
                <Avatar id={option.id} name={option.name} kind={option.kind} size={26} />
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                  <span style={{ fontSize: 13, fontWeight: 550 }}>{option.name}</span>
                  {hint && (
                    <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>{hint}</span>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function RevokeModal({
  base, grant, label, onClose, onRevoked,
}: {
  base: string
  grant: Grant
  label: string
  onClose: () => void
  onRevoked: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!grant.id) return
    setBusy(true)
    setError(null)
    try {
      await access.revoke(base, grant.id)
      onRevoked()
    } catch (err) {
      // The last-manager refusal lands here, and it names the two ways out.
      setError(err instanceof Error ? err.message : 'Could not remove this access.')
      setBusy(false)
    }
  }

  const team = grant.principal_kind === 'TEAM'
  return (
    <Modal
      title={`Remove ${grant.principal_name}’s access?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : null}
            Remove access
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.6 }}>
          {team
            ? `People in ${grant.principal_name} lose “${label}” through the team right away. Anyone given access on their own keeps it.`
            : `${grant.principal_name} loses “${label}” right away. Anything they made from it stays theirs.`}
        </p>
      </div>
    </Modal>
  )
}

/**
 * The one-line answer a header shows without opening the panel:
 * "Only you" / "Shared with 3 people and 1 team". Counts principals, not rows.
 */
export function accessSummary(grants: Grant[] | null): string {
  if (!grants) return ''
  const shares = grants.filter((g) => g.path !== 'owner')
  if (shares.length === 0) return 'Only you'
  const people = new Set(
    shares.filter((g) => g.principal_kind !== 'TEAM').map((g) => g.principal_id),
  ).size
  const teams = new Set(
    shares.filter((g) => g.principal_kind === 'TEAM').map((g) => g.principal_id),
  ).size
  const parts: string[] = []
  if (people) parts.push(`${people} ${people === 1 ? 'person' : 'people'}`)
  if (teams) parts.push(`${teams} ${teams === 1 ? 'team' : 'teams'}`)
  return `Shared with ${parts.join(' and ')}`
}

/**
 * Hand this to somebody else. Separate from sharing because it is a different
 * act: sharing adds somebody, transferring makes them the owner.
 *
 * The previous owner keeps nothing by default — the server never leaves an
 * implicit residue — so the dialog offers to keep *Can edit* for you, which
 * is one visible grant made before the transfer rather than a surprise after.
 */
export function TransferControl({
  base, title, onTransferred,
}: {
  base: string
  title: string
  onTransferred: () => void
}) {
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState<DirectoryEntry[] | null>(null)
  const [me, setMe] = useState<DirectoryEntry | null>(null)
  const [to, setTo] = useState('')
  const [keep, setKeep] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    access.directory()
      .then((dir) => {
        setPeople(dir.people.filter((person) => !person.is_you))
        setMe(dir.people.find((person) => person.is_you) ?? null)
      })
      .catch(() => setPeople([]))
  }, [open])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      if (keep && me) {
        await access.grant(base, { privilege: 'modify', user_id: me.id })
      }
      await access.transfer(base, to)
      setOpen(false)
      onTransferred()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not transfer this.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <GhostButton onClick={() => setOpen(true)}>
        <Icon.ArrowRight size={14} />
        Transfer ownership
      </GhostButton>
      {open && (
        <Modal
          title={`Transfer “${title}”`}
          onClose={() => setOpen(false)}
          width={460}
          footer={
            <>
              <GhostButton onClick={() => setOpen(false)}>Cancel</GhostButton>
              <PrimaryButton disabled={!to || busy} onClick={submit}>
                {busy ? <Spinner /> : <Icon.ArrowRight size={15} />}
                Transfer
              </PrimaryButton>
            </>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {error && <ErrorNote>{error}</ErrorNote>}
            <Select
              aria-label="New owner"
              value={to}
              disabled={people === null}
              onChange={(event) => setTo(event.target.value)}
            >
              <option value="">{people === null ? 'Loading people…' : 'Choose the new owner…'}</option>
              {(people ?? []).map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                  {person.kind === 'SERVICE' ? ' (service account)' : ''}
                </option>
              ))}
            </Select>
            <label
              style={{
                display: 'flex',
                gap: 8,
                alignItems: 'center',
                fontSize: 13,
                color: 'var(--text-strong)',
                cursor: 'pointer',
              }}
            >
              <input
                type="checkbox"
                checked={keep}
                onChange={(event) => setKeep(event.target.checked)}
                style={{ accentColor: 'var(--accent)' }}
              />
              Keep edit access for me
            </label>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.55 }}>
              They become the owner and decide who else has access.
              {keep ? '' : ' You won’t be able to open this afterwards unless they share it with you.'}
            </p>
          </div>
        </Modal>
      )}
    </>
  )
}

/**
 * `<EffectiveAccess>` — what one principal can reach, on their own detail page.
 *
 * The by-principal lens of the access review, embedded where the question
 * actually gets asked. An administrator looking at Sara's account is one click
 * from *"and what can she reach?"*, and that click used to be a different
 * screen, a dropdown and a re-selection of the person already on the page.
 *
 * The same rows and the same rule as `/admin/access`: **reach and never
 * data** — a resource's name and a privilege, never a host, a username or a
 * stored statement. Three details render it and one of them is a *team*,
 * which is not a special case: a team is a principal, and *"what does Finance
 * have"* is the question somebody asks before adding a person to it.
 *
 * It renders nothing at all when the viewer lacks `access.review`, rather than
 * an error: this sits inside a page somebody legitimately reached for another
 * reason, and a red box on an account screen would read as the account being
 * broken.
 */
export function EffectiveAccess({ principalId }: { principalId: string }) {
  const [rows, setRows] = useState<Reach[] | null>(null)
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    let cancelled = false
    setRows(null)
    setDenied(false)
    access.review({ principal_id: principalId })
      .then((next) => !cancelled && setRows(next))
      .catch(() => !cancelled && setDenied(true))
    return () => {
      cancelled = true
    }
  }, [principalId])

  if (denied) return null
  if (rows === null) return <Spinner size={14} />

  if (rows.length === 0) {
    return (
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6 }}>
        Nothing. They own no data source, dashboard or report, and nobody has
        shared one with them — directly, through a team, or through a role.
      </p>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {rows.map((row, index) => (
        <div
          key={index}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 9,
            padding: '7px 11px',
            borderRadius: 9,
            border: '1px solid var(--border)',
            background: 'var(--panel)',
            flexWrap: 'wrap',
          }}
        >
          <span
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: 'var(--text-strong)',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {row.resource_name}
          </span>
          <Chip tone="neutral">{row.resource_type.replace('_', ' ')}</Chip>
          <code className="mono" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {row.privilege}
          </code>
          {/* The path, and it is the reason this list is worth reading: a
              permission that arrives through a team is changed on the team. */}
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>
            {row.path}
            {row.via ? `: ${row.via}` : ''}
          </span>
        </div>
      ))}
    </div>
  )
}

/**
 * `<WhyNot>` — the **"Why can I not see this?"** popover, written once.
 *
 * Requirement 6 asks that the UI *"clearly communicate what a user can and
 * cannot access"*, and the expensive way to fail it is not saying nothing —
 * it is saying eight slightly different things. Every surface that can be
 * refused (a dashboard tile, a report figure, a chat turn, and every 403 a
 * save produces) would otherwise invent its own sentence from whatever the
 * server happened to return, and a rule explained eight ways is a rule nobody
 * learns.
 *
 * So there is one component and the server hands it the components of the
 * answer. `services/policy.require` attaches a structured `reason` to every
 * 403 it raises — what was needed, what that privilege *means on this type*,
 * what the caller holds, and the paths that were tried — and this renders
 * them. The prose in `detail` and the structure here are the same values, so
 * they cannot drift.
 *
 * Two things it deliberately does not do:
 *
 * * **It does not name who to ask.** That is the owner, and this component
 *   does not know it; the placeholder that hosts it names the resource, and
 *   *"ask whoever owns Payroll"* is a sentence the reader can act on without
 *   the product volunteering a colleague's name into a tooltip.
 * * **It does not offer a request button.** Access requests are a workflow
 *   with an approval, a notification and an audit story, and Phase 9's *Not
 *   included* list says so. A button that emailed somebody would be the
 *   cheap half of it, and the cheap half is the one that erodes trust.
 */
export function WhyNot({
  reason, label = 'Why can’t I see this?',
}: {
  /** The `reason` from a 403's problem body, or one a placeholder built. */
  reason: DenialReason | null | undefined
  label?: string
}) {
  const [open, setOpen] = useState(false)
  if (!reason) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{
          background: 'none',
          border: 'none',
          padding: 0,
          font: 'inherit',
          fontSize: 11.5,
          color: 'var(--text-dim)',
          textDecoration: 'underline',
          textUnderlineOffset: 3,
          cursor: 'pointer',
        }}
      >
        {label}
      </button>
      {open && (
        <Modal
          title="Why you can’t see this"
          onClose={() => setOpen(false)}
          width={520}
          footer={<GhostButton onClick={() => setOpen(false)}>Close</GhostButton>}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <p style={{ margin: 0, fontSize: 13.5, color: 'var(--text)', lineHeight: 1.6 }}>
              This needs permission to <strong>{VERB[reason.needed] ?? reason.needed}</strong>{' '}
              this {reason.noun}.
              {reason.meaning && (
                <span style={{ display: 'block', color: 'var(--text-dim)', fontSize: 12.5 }}>
                  {reason.meaning}
                </span>
              )}
            </p>

            <Line label="You can">
              {reason.held.length === 0 ? (
                <span style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>
                  nothing here yet
                </span>
              ) : (
                reason.held
                  .filter((privilege) => privilege !== 'describe' || reason.held.length === 1)
                  .map((privilege) => (
                    <Chip key={privilege} tone="neutral">{VERB[privilege] ?? privilege}</Chip>
                  ))
              )}
            </Line>

            {/* The authorizer's own words for the paths it tried. A
                permission that arrives through a team is changed on the team. */}
            {reason.because.length > 0 && (
              <Line label="Through">
                {reason.because.map((path) => (
                  <Chip key={path} tone="accent">{BECAUSE[path] ?? path}</Chip>
                ))}
              </Line>
            )}

            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-faint)', lineHeight: 1.6 }}>
              Ask whoever owns it. Access works the moment it is given — nothing
              to refresh.
            </p>
          </div>
        </Modal>
      )}
    </>
  )
}

/** Each privilege as the verb a person would use. */
const VERB: Record<string, string> = {
  describe: 'see',
  select: 'view',
  modify: 'edit',
  delete: 'delete',
  manage: 'manage access to',
}

/** How each path in `because` reads. The authorizer's words, in English. */
const BECAUSE: Record<string, string> = {
  owner: 'owning it',
  direct: 'a direct share',
  via_team: 'a team you are in',
  via_role: 'one of your roles',
  wildcard: 'a wildcard grant',
  intersection: 'the data source behind it',
  bound: 'what this is built on',
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-dim)', minWidth: 62 }}>
        {label}
      </span>
      {children}
    </div>
  )
}

/**
 * `<ReachBadge>` — *"you can look, not touch"*, said once and quietly.
 *
 * Somebody who opens a board or report shared with them sees fewer controls
 * than on their own, because every control renders from what they hold. A
 * page that silently has fewer buttons reads as one that failed to load, so
 * the header says why — and whose it is, which is who to ask.
 *
 * Nothing for a `manage` holder: that is the ordinary case of your own work.
 */
export function ReachBadge({
  privileges, owner,
}: {
  privileges: string[]
  /** The owner's display name, when it is not the reader. */
  owner?: string | null
}) {
  const held = accessOf(privileges)
  if (held.share) return null
  const label = held.edit ? 'Can edit' : 'View only'
  const title = held.edit
    ? `You can edit this.${owner ? ` ${owner} decides who else has access.` : ''}`
    : `You can look, but not change anything.${owner ? ` Ask ${owner} if you need to edit it.` : ''}`
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        height: 26,
        padding: '0 10px',
        borderRadius: 999,
        fontSize: 11.5,
        fontWeight: 500,
        whiteSpace: 'nowrap',
        color: 'var(--text-dim)',
        background: 'var(--panel-alt)',
        border: '1px solid var(--border)',
      }}
    >
      {held.edit ? <Icon.Pencil size={11} /> : <Icon.Eye size={12} />}
      {label}
      {owner && (
        <span style={{ color: 'var(--text-faint)', fontWeight: 400 }}>· {owner}</span>
      )}
    </span>
  )
}

/**
 * `<Restricted>` — the named placeholder, everywhere the intersection rule
 * puts one.
 *
 * A dashboard tile, a report figure and a chat turn all reach the same state:
 * *you may see this thing, and not the database behind it.* §19.2 says what to
 * draw —
 *
 * > A tile they cannot see renders as a **named placeholder** — not hidden,
 * > because hiding it makes the dashboard silently wrong, and a partly visible
 * > dashboard is a better product than a refused one **and** a better product
 * > than a leaking one.
 *
 * Three decisions, and each is one this could easily have got wrong:
 *
 * * **It fills the space the content would have taken.** A tile that shrank to
 *   a line of text would reflow the grid and make a shared board look broken
 *   rather than partial.
 * * **It is neutral, not an error.** Amber, not red, and the same reasoning
 *   the audit screen uses for a denial: this is the system working. Red would
 *   teach a reader that a correctly shared dashboard is full of failures.
 * * **The sentence comes from the server.** The backend already writes it —
 *   naming the connection, and saying which privilege to ask for — and a
 *   second copy here would be a second chance to describe the rule wrongly.
 *   The fallback exists for an old response, not as an alternative wording.
 */
export function Restricted({
  reason, compact = false, connectionId,
}: {
  /** The server's sentence. It names the data source and what to ask for. */
  reason?: string | null
  /** For a report figure or a chat turn, which sit in flowing text. */
  compact?: boolean
  /**
   * The data source behind this, when the caller knows it. With it the
   * placeholder carries **Why can I not see this?** — the same popover every
   * 403 surface uses, built from the one fact this state always has: `select`
   * on a connection is what was missing, and nothing reaches this reader
   * through it.
   */
  connectionId?: string | null
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: compact ? 'row' : 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: compact ? 10 : 8,
        height: compact ? undefined : '100%',
        minHeight: compact ? undefined : 80,
        padding: compact ? '10px 12px' : '18px 16px',
        borderRadius: 10,
        border: '1px dashed var(--amber-border)',
        background: 'var(--amber-bg)',
        textAlign: compact ? 'left' : 'center',
      }}
    >
      <span
        aria-hidden
        style={{ color: 'var(--amber)', display: 'grid', placeItems: 'center' }}
      >
        <Icon.Lock size={compact ? 14 : 18} />
      </span>
      <span
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 5,
          alignItems: compact ? 'flex-start' : 'center',
          fontSize: compact ? 12 : 12.5,
          color: 'var(--text-dim)',
          lineHeight: 1.55,
          maxWidth: 320,
        }}
      >
        {reason || 'This uses a data source that hasn’t been shared with you.'}
        {connectionId && (
          <WhyNot
            label="Why?"
            reason={{
              needed: 'select',
              meaning: 'Query it, and see the numbers anything built on it shows.',
              // Nothing reaches them through the connection — that is exactly
              // what this state *is*. An empty list here is the honest answer
              // and reads as "you hold nothing on this", which is what the
              // popover prints.
              held: [],
              because: ['intersection'],
              resource_type: 'connection',
              resource_id: connectionId,
              noun: 'data source',
            }}
          />
        )}
      </span>
    </div>
  )
}


/**
 * The Access control for a header that has no room for a panel.
 *
 * The Knowledge console and the Semantic tab live *inside* a connection's
 * detail screen, so neither can afford a fifth tab of its own — but both are
 * separately grantable resources, and that is the whole of requirement 2: a
 * Knowledge Manager may be given `(knowledge, manage)` on a connection whose
 * data they cannot read. A button that opens the same `<AccessPanel>` in a
 * modal is how those two surfaces get a share control without a second
 * navigation level.
 *
 * It **renders nothing when the viewer cannot share**, rather than rendering a
 * disabled button: on these headers a greyed control would sit beside a screen
 * the person can otherwise use fully, and it would read as "this is broken"
 * rather than "this is not yours". The panel behind it is refused by the
 * server regardless — `manage` on all five calls — so hiding it is an
 * affordance, never the boundary.
 */
export function AccessPopover({
  base, resourceLabel, label, warn, extraActions, onChanged,
  buttonStyle, buttonHoverStyle,
}: {
  base: string
  /** What to call this thing in the button's title and the modal's heading. */
  resourceLabel: string
  /**
   * Override the button's text. The default is the share summary — "2 people"
   * — which reads well beside a heading and badly inside a kebab menu, where
   * "Share…" is what everything else in the list looks like.
   */
  label?: string
  /** Passed straight through — see `AccessPanel`'s own `warn`. */
  warn?: WarnFn
  /**
   * Passed straight through — see `AccessPanel`'s own `extraActions`.
   *
   * A dashboard and a report are reachable from two share surfaces — the
   * index card's kebab and this button on their own header — and a control
   * present on one but not the other is the kind of difference nobody
   * discovers until they are on the wrong screen. The derived types
   * (knowledge, semantic layer) pass nothing, because they have no owner of
   * their own to transfer: theirs is their connection's.
   */
  extraActions?: React.ReactNode
  /** Called after any change, so a header showing the summary can re-read. */
  onChanged?: () => void
  /**
   * The trigger's own look, as `HoverButton`'s two halves.
   *
   * The default is `GhostButton` — a border, near-white ink, `8px 14px` — and
   * that is right on the Knowledge and Semantic headers, where the buttons
   * beside it are ghost buttons of exactly that size. It is wrong on the two
   * headers that have a toolbar vocabulary of their own: a dashboard's ran
   * 36px tall at 13px beside a 28px toolgroup and a 34px primary, which made
   * *"who can reach this"* the loudest control on a screen where it is the
   * quietest thing that happens — louder than **Add tile**, the page's one
   * real action. A report's was the same button beside `toolbarBtn` siblings.
   *
   * So the host says how its own chrome is sized rather than this component
   * guessing, and the two values are the two `HoverButton` takes, because a
   * button whose resting state is overridden and whose hover is not stops
   * responding to the cursor.
   */
  buttonStyle?: React.CSSProperties
  buttonHoverStyle?: React.CSSProperties
}) {
  const [open, setOpen] = useState(false)
  const [actions, setActions] = useState<Actions | null>(null)
  const [grants, setGrants] = useState<Grant[] | null>(null)

  // `…/actions` decides whether the button exists at all, and it is the same
  // answer the API will check on the next request. The summary beside it is a
  // second call, made only when the button is going to be shown.
  useEffect(() => {
    let cancelled = false
    access.actions(base)
      .then((next) => !cancelled && setActions(next))
      .catch(() => !cancelled && setActions(null))
    return () => {
      cancelled = true
    }
  }, [base])

  useEffect(() => {
    if (!actions?.can.share) return
    let cancelled = false
    access.grants(base)
      .then((rows) => !cancelled && setGrants(rows))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [base, actions])

  if (!actions?.can.share) return null

  return (
    <>
      {/* `HoverButton` rather than `GhostButton`, so a host can override the
          hover as well as the rest. The defaults below are `GhostButton`'s own
          values, so a caller passing nothing gets the button it always had. */}
      <HoverButton
        onClick={() => setOpen(true)}
        title={`Who can reach ${resourceLabel}`}
        baseStyle={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 13,
          fontWeight: 500,
          background: 'transparent',
          color: 'var(--text-strong)',
          border: '1px solid var(--border-strong)',
          padding: '8px 14px',
          borderRadius: 7,
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          ...buttonStyle,
        }}
        hoverStyle={buttonHoverStyle ?? { borderColor: 'var(--accent)' }}
      >
        <Icon.Users size={14} />
        {label ?? (accessSummary(grants) || 'Access')}
      </HoverButton>
      {/* Portalled to the body: this button lives inside host chrome — the
          dashboard's `.rm-toolgroup` — whose button rules otherwise reach
          every control in the dialog and strip the Share button's fill. */}
      {open && createPortal(
        <Modal
          title={`Access to ${resourceLabel}`}
          onClose={() => setOpen(false)}
          width={620}
          footer={<GhostButton onClick={() => setOpen(false)}>Done</GhostButton>}
        >
          <AccessPanel
            base={base}
            title={resourceLabel}
            warn={warn}
            extraActions={extraActions}
            // The button's own label is a share summary, so a change made
            // inside the modal has to reach it or the header goes on saying
            // "Only you" about something just shared.
            onChanged={() => {
              access.grants(base).then(setGrants).catch(() => undefined)
              onChanged?.()
            }}
          />
        </Modal>,
        document.body,
      )}
    </>
  )
}
