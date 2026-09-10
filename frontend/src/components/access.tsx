/**
 * `<AccessPanel>` — who can reach this, and one way to change it.
 *
 * **One component for every resource type**, mounted with the resource's own
 * API path: `connections/{id}`, `connections/{id}/knowledge`,
 * `connections/{id}/semantic`, and from Phase 8 reports, dashboards, model
 * configurations and conversations. Eight copies of a share dialog is eight
 * places for the privilege list to drift from what the server will accept, and
 * the only way anybody finds out is a Save that 403s after a form was filled
 * in.
 *
 * Six things here are deliberate, and each is the plan's §21.4 as an
 * interaction rather than a paragraph:
 *
 * * **The privilege radio is rendered from `GET …/actions`, not from a list in
 *   this file.** The server sends the five privileges *and the sentence each
 *   means on this resource type*, from the same `PRIVILEGE_MEANINGS` table a
 *   403 quotes. So the label beside a radio button and the refusal somebody
 *   would get are the same words, and adding a resource type needs no change
 *   here. **The sentence is the label and the enum member is the caption** —
 *   the shape the Roles tab already uses for a capability, and the reason
 *   somebody choosing who may read a database is not asked to first learn what
 *   this product means by `select`.
 * * **Every row shows the path.** *"Sara"* alone is a fact nobody can act on or
 *   verify. *"Sara · via this team"* tells them the revoke they want is on the
 *   team, and that clicking revoke on this row would do nothing. Ownership is
 *   shown as a path too, without a revoke control, because ownership is not a
 *   grant — it is moved by transferring.
 * * **The privilege on a row is a control, not a word.** A grant row is keyed
 *   by its privilege on the server, so raising somebody is a new row rather
 *   than an edit — which used to mean the only way to change what a colleague
 *   may do was to notice they had vanished from the share dialog, revoke them,
 *   and share again. `change()` does both calls, and the order it does them in
 *   is the fail-safe direction for each.
 * * **The dialog greys who already has access instead of hiding them.** The
 *   list looked filtered — or, on an installation where everybody had some
 *   access, empty — with nothing on screen saying why.
 * * **Teams are offered first and said to be the better answer.** A permission
 *   attached to a job survives the person leaving it; one attached to a person
 *   becomes a row nobody can attribute and nobody dares revoke. That is the
 *   whole reason teams shipped a phase before grants.
 * * **The panel renders nothing but a sentence when the viewer lacks `manage`.**
 *   Not a disabled form: a control somebody cannot use teaches them less than
 *   a sentence saying who can.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { access, teams as teamsApi, users as usersApi } from '../api/client'
import type { Actions, DenialReason, Grant, Reach, Team, User } from '../api/types'
import {
  Chip, DangerButton, ErrorNote, GhostButton, HoverButton, Icon, Modal,
  PrimaryButton, SearchField, Select, Spinner, initialOf,
} from './ui'

/** The five, in lattice order — the order the radio renders them in. */
const ORDER = ['describe', 'select', 'modify', 'delete', 'manage']

/** How a path reads on a row. `owner` is not a grant and says so. */
const PATH_LABEL: Record<string, string> = {
  owner: 'owner',
  direct: 'directly',
  team: 'via this team',
}

interface Principal {
  id: string
  name: string
  kind: 'HUMAN' | 'SERVICE' | 'TEAM'
  hint?: string
}

export function AccessPanel({
  base, title, description, onChanged, warn, extraActions,
}: {
  /**
   * The resource's path, **without** a leading slash and without `/api/v1`:
   * `connections/{id}`, `connections/{id}/knowledge`. The same string the
   * backend mounts these routes under, which is what makes one panel serve
   * every type.
   */
  base: string
  title?: string
  description?: string
  /** Called after any change, so a parent showing a share count can re-read. */
  onChanged?: () => void
  /**
   * **The cross-connection warning**, and the only resource-specific thing
   * this panel knows about.
   *
   * A dashboard is the one artifact whose tiles carry their own
   * `connection_id`, so it is the one place where *"share this"* can mean
   * *"and they will see two of these four tiles"*. The page passes a function
   * that asks the server what a named principal could not read; the dialog
   * renders the answer beside an **enabled** Share button, because §19.2 is
   * explicit that the share is still allowed and it is the surprise that is
   * not.
   *
   * A prop rather than a branch on `base` — the panel serves eight types and
   * a `startsWith('dashboards/')` here would be the ninth place the route
   * table is spelled out.
   */
  warn?: (principal: { user_id?: string; team_id?: string }) => Promise<string[]>
  /**
   * Further actions on *who owns this*, rendered in the same row as
   * **Give access** rather than stacked under it.
   *
   * `<TransferControl>` is the only thing that goes here today, and the slot
   * exists because it used to be dropped in by each page *below* the panel —
   * which put two buttons of the same subject on two rows at two widths, and
   * made every surface place it slightly differently. Sharing and transferring
   * are two answers to one question, so they belong on one line; the panel
   * owns the line, and the page says what else is on it.
   *
   * Rendered only when the viewer holds `manage`, because the panel's whole
   * action row is — a transfer button beside a sentence explaining you cannot
   * change access would be a control that only 403s.
   */
  extraActions?: React.ReactNode
}) {
  const [grants, setGrants] = useState<Grant[] | null>(null)
  const [actions, setActions] = useState<Actions | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sharing, setSharing] = useState(false)
  const [revoking, setRevoking] = useState<Grant | null>(null)
  /** The grant id whose privilege is mid-change, so its row can say so. */
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

  /**
   * Which privileges this resource type may be given, in lattice order.
   *
   * Straight from the server's `meanings`, whose keys are its own enum — a
   * type that narrows its share surface (a model configuration, where `modify`
   * is equivalent to handing over the API key) narrows it there and both the
   * dialog and the per-row changer follow with no second rule here.
   */
  const offered = useMemo(
    () => ORDER.filter((name) => name in (actions?.meanings ?? {})),
    [actions],
  )

  /**
   * Change what one grant allows, **without making somebody revoke and
   * re-share to do it**.
   *
   * A grant row is keyed by its privilege, so raising Reza from `describe` to
   * `select` is a different row rather than an edit of his. Until this existed
   * the dialog simply hid everyone who already had access — which turned *"let
   * him do a bit more"* into a puzzle whose answer was "revoke him first", and
   * left `Nobody left to give access to.` on screen as the only clue.
   *
   * **The order of the two calls depends on the direction**, and that is the
   * whole care in it. Widening grants the higher privilege first: if the
   * revoke then fails, the principal holds both, and the union of two
   * privileges in a lattice *is* the higher one — no more access than was
   * asked for. Narrowing revokes the higher one first: if the grant then
   * fails, they hold nothing, which is the wrong amount but the safe
   * direction. Either way the panel reloads from the server afterwards, so
   * what is on screen is what the server actually holds rather than what this
   * function intended.
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
        setError(
          err instanceof Error
            ? err.message
            : 'Could not change what this allows.',
        )
      } finally {
        setChanging(null)
        // Always, including after a failure: a half-applied change must show
        // as what it actually is, not as what was clicked.
        await reload().catch(() => undefined)
      }
    },
    [base, reload],
  )

  useEffect(() => {
    if (!mayShare) {
      setGrants([])
      return
    }
    let cancelled = false
    access.grants(base)
      .then((next) => !cancelled && setGrants(next))
      .catch(() => {
        if (cancelled) return
        setError('Could not load who has access.')
        setGrants([])
      })
    return () => {
      cancelled = true
    }
  }, [base, mayShare])

  if (actions === null) {
    return <Spinner />
  }

  if (!mayShare) {
    // Not a disabled form. Somebody who cannot share this can still be told
    // who to ask, and a greyed-out picker tells them nothing.
    const owner = grants?.find((g) => g.path === 'owner')
    return (
      <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.6 }}>
        You can see this, and you cannot change who else can — that needs the{' '}
        <strong>manage</strong> permission.{' '}
        {owner ? `Ask ${owner.principal_name}, who owns it.` : 'Ask its owner.'}
      </p>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {error && <ErrorNote>{error}</ErrorNote>}
      {description && (
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {description}
        </p>
      )}

      {grants === null ? (
        <Spinner />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {grants.map((grant) => (
            <GrantRow
              key={grant.id ?? `owner-${grant.principal_id}`}
              grant={grant}
              offered={offered}
              meanings={actions.meanings}
              busy={changing === grant.id}
              onChange={(next) => change(grant, next)}
              onRevoke={() => setRevoking(grant)}
            />
          ))}
        </div>
      )}

      {/* One row, because sharing and transferring are two answers to the same
          question — *who is this for* — and stacking them made the second look
          like an afterthought on every screen that mounted one. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <PrimaryButton onClick={() => setSharing(true)}>
          <Icon.Plus size={14} />
          Give access
        </PrimaryButton>
        {extraActions}
      </div>

      {sharing && actions && (
        <ShareModal
          base={base}
          title={title}
          meanings={actions.meanings}
          existing={grants ?? []}
          warn={warn}
          onClose={() => setSharing(false)}
          onShared={async () => {
            setSharing(false)
            setError(null)
            await reload()
          }}
        />
      )}

      {revoking && (
        <RevokeModal
          base={base}
          grant={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={async () => {
            setRevoking(null)
            setError(null)
            await reload()
          }}
        />
      )}
    </div>
  )
}

function GrantRow({
  grant, offered, meanings, busy, onChange, onRevoke,
}: {
  grant: Grant
  offered: string[]
  meanings: Record<string, string>
  busy: boolean
  onChange: (next: string) => void
  onRevoke: () => void
}) {
  const isOwner = grant.path === 'owner'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: 'var(--panel)',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 24,
          height: 24,
          flexShrink: 0,
          borderRadius: grant.principal_kind === 'TEAM' ? 7 : 20,
          display: 'grid',
          placeItems: 'center',
          background: 'var(--panel-alt)',
          color: 'var(--text-dim)',
          fontSize: 10.5,
          fontWeight: 700,
        }}
      >
        {grant.principal_kind === 'TEAM' ? (
          <Icon.Users size={12} />
        ) : (
          initialOf(grant.principal_name)
        )}
      </span>

      <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {grant.principal_name}
        </span>
        {/* The path. Without it a row is a fact nobody can act on: revoking
            here would not touch a permission that arrives through a team. */}
        <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          {isOwner ? `${grant.privilege} · ` : ''}
          {PATH_LABEL[grant.path] ?? grant.path}
        </span>
      </span>

      {grant.principal_kind === 'SERVICE' && <Chip tone="accent">Service</Chip>}
      {grant.principal_kind === 'TEAM' && <Chip tone="accent">Team</Chip>}

      {isOwner ? (
        // Ownership is not a grant and cannot be revoked — only transferred.
        // Saying so beats a disabled button with no explanation.
        <Chip tone="green">Owner</Chip>
      ) : (
        <>
          {/* The privilege is the editable thing on this row, so it is an
              editable control rather than a word — see `AccessPanel.change`
              for why the two calls behind it are ordered the way they are.
              `title` carries the server's own sentence for the level in
              force, so the row explains itself without a fifth line of text
              in a list that is mostly names. */}
          <Select
            aria-label={`What ${grant.principal_name} may do`}
            title={meanings[grant.privilege]}
            disabled={busy || offered.length === 0}
            value={grant.privilege}
            onChange={(event) => onChange(event.target.value)}
            style={{ width: 'auto', minWidth: 104, padding: '5px 8px', fontSize: 12 }}
          >
            {/* The privileges the server offers, plus — if this row somehow
                holds one it no longer offers — the one actually in force, so
                the control never silently displays a different level from
                the one the row has. */}
            {(offered.includes(grant.privilege)
              ? offered
              : [grant.privilege, ...offered]
            ).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
          <GhostButton
            onClick={onRevoke}
            disabled={busy}
            title="Remove this access"
          >
            {busy ? <Spinner /> : <Icon.Close size={13} />}
            Revoke
          </GhostButton>
        </>
      )}
    </div>
  )
}

function ShareModal({
  base, title, meanings, existing, onClose, onShared, warn,
}: {
  base: string
  title?: string
  meanings: Record<string, string>
  existing: Grant[]
  onClose: () => void
  onShared: () => void
  warn?: (principal: { user_id?: string; team_id?: string }) => Promise<string[]>
}) {
  const [principals, setPrincipals] = useState<Principal[] | null>(null)
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Principal | null>(null)
  const [privilege, setPrivilege] = useState('select')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  //: The data sources this principal would not be able to read. Asked when a
  //: principal is picked, and never blocking: a failed check leaves the list
  //: empty and the Share button exactly as usable as it was.
  const [unreadable, setUnreadable] = useState<string[]>([])

  useEffect(() => {
    if (!warn || !picked) {
      setUnreadable([])
      return
    }
    let cancelled = false
    warn(picked.kind === 'TEAM' ? { team_id: picked.id } : { user_id: picked.id })
      .then((names) => !cancelled && setUnreadable(names))
      .catch(() => !cancelled && setUnreadable([]))
    return () => {
      cancelled = true
    }
  }, [warn, picked])

  useEffect(() => {
    let cancelled = false
    Promise.all([
      usersApi.list().catch(() => [] as User[]),
      teamsApi.list().catch(() => [] as Team[]),
    ]).then(([people, teams]) => {
      if (cancelled) return
      setPrincipals([
        // Teams first, and the panel says why: a permission attached to a job
        // survives the person leaving it.
        ...teams.map<Principal>((team) => ({
          id: team.id,
          name: team.name,
          kind: 'TEAM',
          hint: `${team.members} ${team.members === 1 ? 'person' : 'people'}`,
        })),
        ...people.map<Principal>((person) => ({
          id: person.id,
          name: person.display_name || person.email,
          kind: person.kind === 'SERVICE' ? 'SERVICE' : 'HUMAN',
          hint: person.kind === 'SERVICE' ? 'service account' : person.email,
        })),
      ])
    })
    return () => {
      cancelled = true
    }
  }, [])

  /**
   * What each principal already has here, by id — **not** a set of people to
   * hide.
   *
   * Hiding them is what this used to do, and it read as an outage: the owner
   * and everyone already shared with simply were not in the list, and an
   * installation where everybody had some access showed `Nobody left to give
   * access to.` with no hint that the list was filtered at all. So they stay,
   * greyed, saying what they hold — and the row points at the control that
   * actually changes it, which is the one on the panel behind this dialog.
   */
  const already = useMemo(
    () =>
      new Map(
        existing.map((grant) => [
          grant.principal_id,
          grant.path === 'owner' ? 'owns this' : `already has ${grant.privilege}`,
        ]),
      ),
    [existing],
  )

  const candidates = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = needle
      ? (principals ?? []).filter((p) => p.name.toLowerCase().includes(needle))
      : (principals ?? [])
    // Everyone who already has access sinks to the bottom. They are kept
    // (see `already`) so the list never looks filtered, but a greyed row is
    // not something anybody came here to press, and leaving them interleaved
    // put the rows that can be picked below the fold on an installation where
    // most people already have some access.
    return [
      ...pool.filter((p) => !already.has(p.id)),
      ...pool.filter((p) => already.has(p.id)),
    ]
  }, [principals, query, already])

  /** How many of the rows on offer can actually be picked. */
  const grantable = candidates.filter((p) => !already.has(p.id)).length

  /**
   * Which privileges this type may be given.
   *
   * Straight from `meanings`, whose keys are the server's own enum — so a type
   * whose share surface should be narrower (a model configuration, where
   * `modify` is equivalent to disclosing the API key) narrows on the server and
   * this follows without a second rule here.
   */
  const offered = ORDER.filter((name) => name in meanings)

  async function submit() {
    if (!picked) return
    setBusy(true)
    setError(null)
    try {
      await access.grant(base, {
        privilege,
        ...(picked.kind === 'TEAM' ? { team_id: picked.id } : { user_id: picked.id }),
      })
      onShared()
    } catch (err) {
      // The server's refusals are the sentences worth showing: the
      // key-equivalent privilege on a model config, the wildcard needing
      // `role.manage`, and the 403 naming the privilege.
      setError(err instanceof Error ? err.message : 'Could not share this.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={title ? `Give access to “${title}”` : 'Give access'}
      onClose={onClose}
      width={520}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton disabled={!picked || busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Check size={15} />}
            Give access
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <ErrorNote>{error}</ErrorNote>}

        {/* §19.2: the share is still allowed; the surprise is not. So this is
            a note, and the button beside it stays enabled — a board spanning
            four warehouses is a legitimate thing to share with somebody who
            can read two of them. They get two tiles and two named
            placeholders, which is the rule working. */}
        {unreadable.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 9,
              padding: '10px 12px',
              borderRadius: 10,
              border: '1px solid var(--amber-border)',
              background: 'var(--amber-bg)',
            }}
          >
            <span aria-hidden style={{ color: 'var(--amber)', flexShrink: 0 }}>
              <Icon.Lock size={14} />
            </span>
            <span style={{ fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.55 }}>
              {picked?.name} cannot read{' '}
              <strong style={{ color: 'var(--text-strong)' }}>
                {unreadable.join(', ')}
              </strong>
              . Those tiles will render as placeholders. Share the data source
              too if they should see the numbers.
            </span>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <SearchField
            value={query}
            onChange={setQuery}
            ariaLabel="Search people and teams"
            placeholder="Search people and teams…"
          />
          {principals === null ? (
            <Spinner />
          ) : (
            /* A bordered, inset scroll box rather than a bare `maxHeight`.
               The list is longer than its window in any real installation, and
               an unframed one cut the last visible row in half against the
               dialog's own background — which reads as a rendering fault
               rather than as "there is more below". The frame makes the clip
               deliberate, and `scrollbar-gutter` keeps the rows from shifting
               sideways when the scrollbar appears. */
            <div
              role="listbox"
              aria-label="People and teams"
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                maxHeight: 208,
                overflowY: 'auto',
                scrollbarGutter: 'stable',
                padding: 5,
                borderRadius: 11,
                border: '1px solid var(--border)',
                background: 'var(--panel-alt)',
              }}
            >
              {candidates.map((principal) => {
                const held = already.get(principal.id)
                const chosen = picked?.id === principal.id
                return (
                  <button
                    key={`${principal.kind}-${principal.id}`}
                    type="button"
                    role="option"
                    aria-selected={chosen}
                    disabled={held !== undefined}
                    onClick={() => setPicked(principal)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      padding: '7px 10px',
                      borderRadius: 9,
                      textAlign: 'left',
                      border: `1px solid ${chosen ? 'var(--accent)' : 'var(--border)'}`,
                      background: chosen ? 'var(--accent-bg)' : 'var(--panel)',
                      color: 'var(--text-strong)',
                      cursor: held ? 'default' : 'pointer',
                      opacity: held ? 0.55 : 1,
                    }}
                  >
                    <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 600 }}>
                        {principal.name}
                      </span>
                      <span style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
                        {held ?? principal.hint}
                      </span>
                    </span>
                    {principal.kind === 'TEAM' && <Chip tone="accent">Team</Chip>}
                    {principal.kind === 'SERVICE' && <Chip tone="accent">Service</Chip>}
                    {/* A tick, not only a tint: the selected row has to survive
                        somebody who cannot tell two dark blues apart. */}
                    {chosen && (
                      <span aria-hidden style={{ color: 'var(--accent)', display: 'flex' }}>
                        <Icon.Check size={14} />
                      </span>
                    )}
                  </button>
                )
              })}
              {candidates.length === 0 && (
                <span style={{ fontSize: 12, color: 'var(--text-faint)', padding: '6px 6px' }}>
                  {query.trim()
                    ? `Nobody here matches “${query.trim()}”.`
                    : 'There is nobody else in this installation yet.'}
                </span>
              )}
            </div>
          )}
          {/* Said only when it is the answer to a question somebody is about
              to ask: every name on offer is greyed, so the dialog looks broken
              unless it explains that the change they want lives elsewhere. */}
          {principals !== null && candidates.length > 0 && grantable === 0 && (
            <p style={{ margin: 0, fontSize: 11, color: 'var(--text-faint)', lineHeight: 1.5 }}>
              Everyone here can already reach this. To change what one of them
              may do, use the dropdown on their row behind this dialog.
            </p>
          )}
          {/* Said once, where the decision is being made. */}
          <p style={{ margin: 0, fontSize: 11, color: 'var(--text-faint)', lineHeight: 1.5 }}>
            <strong style={{ color: 'var(--text-dim)' }}>Prefer a team.</strong>{' '}
            A permission given to a job survives the person leaving it; one given
            to a person becomes a row nobody can attribute a year from now.
          </p>
        </div>

        <div
          role="radiogroup"
          aria-label="What they may do"
          style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
        >
          <span style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--text-dim)' }}>
            What they may do
          </span>
          {/* Rendered from the server's own table, so the label here and the
              sentence in a 403 are the same words.

              **The sentence leads and the enum member follows**, which is the
              shape the Roles tab already uses for a capability — *"See the
              list of people and their accounts."* over `user.read`. It was the
              other way round here, and `select` in bold monospace over a grey
              explanation asks somebody choosing who may read a database to
              first learn what DataMind means by a SQL keyword. The token is
              still on screen, because it is the word the audit log and every
              403 will use. */}
          {offered.map((name) => (
            <label
              key={name}
              style={{
                display: 'flex',
                gap: 9,
                alignItems: 'flex-start',
                padding: '8px 10px',
                borderRadius: 9,
                border: `1px solid ${privilege === name ? 'var(--accent)' : 'var(--border)'}`,
                background: privilege === name ? 'var(--accent-bg)' : 'var(--panel)',
                cursor: 'pointer',
              }}
            >
              <input
                type="radio"
                name="privilege"
                checked={privilege === name}
                onChange={() => setPrivilege(name)}
                style={{ accentColor: 'var(--accent)', marginTop: 2 }}
              />
              <span style={{ minWidth: 0 }}>
                <span
                  style={{
                    display: 'block',
                    fontSize: 12.5,
                    fontWeight: 600,
                    color: 'var(--text-strong)',
                    lineHeight: 1.45,
                  }}
                >
                  {meanings[name]}
                </span>
                <span
                  className="mono"
                  style={{
                    display: 'block',
                    fontSize: 10.5,
                    color: 'var(--text-faint)',
                    marginTop: 2,
                  }}
                >
                  {name}
                </span>
              </span>
            </label>
          ))}
        </div>
      </div>
    </Modal>
  )
}

function RevokeModal({
  base, grant, onClose, onRevoked,
}: {
  base: string
  grant: Grant
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
      setError(err instanceof Error ? err.message : 'Could not revoke this.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Remove ${grant.principal_name}’s access?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Close size={14} />}
            Revoke
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          {grant.principal_kind === 'TEAM'
            ? `Everybody in ${grant.principal_name} loses this on their next request.`
            : `${grant.principal_name} loses this on their next request — not in fifteen minutes.`}{' '}
          Anything they built with it stays theirs.
        </p>
      </div>
    </Modal>
  )
}

/**
 * The transfer control. Separate from the panel because it is a different act.
 *
 * Sharing adds somebody; transferring hands the thing over and the previous
 * owner keeps nothing. Putting them side by side in one dialog would make the
 * second look like a stronger version of the first.
 */
/**
 * The one-line answer a header shows without opening the panel.
 *
 * "Only you" / "Shared with 3 people and 1 team". A header that said nothing
 * would put *"is this shared?"* one click away from every screen, and that is
 * the question somebody asks before they paste a number into an email.
 *
 * Counts **principals**, not rows: somebody granted both `select` and `manage`
 * is one person who can reach this, and saying "2 people" would be a number
 * nobody could reconcile with the list underneath it.
 */
export function accessSummary(grants: Grant[] | null): string {
  if (!grants) return ''
  const shares = grants.filter((g) => g.path !== 'owner')
  if (shares.length === 0) return 'Only you'
  const people = new Set(
    shares.filter((g) => g.path !== 'team').map((g) => g.principal_id),
  ).size
  const teams = new Set(
    shares.filter((g) => g.path === 'team').map((g) => g.principal_id),
  ).size
  const parts: string[] = []
  if (people) parts.push(`${people} ${people === 1 ? 'person' : 'people'}`)
  if (teams) parts.push(`${teams} ${teams === 1 ? 'team' : 'teams'}`)
  return `Shared with ${parts.join(' and ')}`
}


export function TransferControl({
  base, title, onTransferred,
}: {
  base: string
  title: string
  onTransferred: () => void
}) {
  const [open, setOpen] = useState(false)
  const [people, setPeople] = useState<User[]>([])
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    usersApi.list().then(setPeople).catch(() => setPeople([]))
  }, [open])

  async function submit() {
    setBusy(true)
    setError(null)
    try {
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
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {error && <ErrorNote>{error}</ErrorNote>}
            <Select
              aria-label="New owner"
              value={to}
              onChange={(event) => setTo(event.target.value)}
            >
              <option value="">Choose the new owner…</option>
              {people
                .filter((person) => person.status !== 'DISABLED')
                .map((person) => (
                  <option key={person.id} value={person.id}>
                    {person.display_name || person.email}
                    {person.kind === 'SERVICE' ? ' (service account)' : ''}
                  </option>
                ))}
            </Select>
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.55 }}>
              You keep <strong>nothing</strong>. If you should still be able to
              reach this afterwards, give yourself access first — one row
              somebody can see, rather than a residue of a transfer nobody
              remembers.
            </p>
          </div>
        </Modal>
      )}
    </>
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
  reason, label = 'Why can I not see this?',
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
          title="Why you cannot see this"
          onClose={() => setOpen(false)}
          width={520}
          footer={<GhostButton onClick={() => setOpen(false)}>Close</GhostButton>}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <p style={{ margin: 0, fontSize: 13, color: 'var(--text)', lineHeight: 1.6 }}>
              This needs <strong>{reason.needed}</strong> on this {reason.noun}
              {reason.meaning ? ` — ${reason.meaning.charAt(0).toLowerCase()}${reason.meaning.slice(1)}` : '.'}
            </p>

            <Line label="You hold">
              {reason.held.length === 0 ? (
                <span style={{ color: 'var(--text-faint)' }}>nothing on this</span>
              ) : (
                reason.held.map((privilege) => (
                  <Chip key={privilege} tone="neutral">{privilege}</Chip>
                ))
              )}
            </Line>

            {/* The authorizer's own words for the paths it tried. `via_team`
                and `via_role` are the two that tell a reader where to go: a
                permission that arrives through a team is changed on the team,
                not on them. */}
            {reason.because.length > 0 && (
              <Line label="Through">
                {reason.because.map((path) => (
                  <Chip key={path} tone="accent">{BECAUSE[path] ?? path}</Chip>
                ))}
              </Line>
            )}

            <p
              style={{
                margin: 0,
                fontSize: 11.5,
                color: 'var(--text-faint)',
                lineHeight: 1.6,
              }}
            >
              Permissions are decided per request, so the moment somebody gives
              you access it takes effect — there is nothing to refresh and no
              cache to wait out.
            </p>
          </div>
        </Modal>
      )}
    </>
  )
}

/** How each path in `because` reads. The authorizer's words, in English. */
const BECAUSE: Record<string, string> = {
  owner: 'owning it',
  direct: 'a direct share',
  via_team: 'a team you are in',
  via_role: 'one of your roles',
  wildcard: 'a wildcard grant',
  intersection: 'the data source behind it',
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
 * `<ReachBadge>` — *"you may look, not touch"*, said once and quietly.
 *
 * A shared board and a shared report both put a reader in front of a screen
 * whose controls do less than they used to. Every one of those controls is
 * already rendered from what the reader holds, so nothing is broken — but a
 * page that silently has fewer buttons than the last one you saw reads as a
 * page that failed to load, and the reader has no way to learn that they were
 * given `select` rather than `manage`.
 *
 * So one chip, from the privileges the detail response already carries. Three
 * states and no more, because a fourth would be a privilege list, and a
 * privilege list on a page header is the lattice leaking into the UI:
 *
 * * nothing to say — the reader can edit it, which is the ordinary case;
 * * **Read-only** — they hold `select`, so they can look and run nothing;
 * * **Limited** — they can edit but not share, which is what `modify` without
 *   `manage` means and is exactly the distinction people are surprised by.
 */
export function ReachBadge({ privileges }: { privileges: string[] }) {
  const held = new Set(privileges)
  if (held.has('manage')) return null
  // `<Chip>` takes no `title`, so the tooltip lives on a wrapper — the chip
  // stays a chip rather than growing a prop for one caller.
  return (
    <span
      title={
        held.has('modify')
          ? 'You can edit this, but not decide who else can reach it.'
          : 'You were given read access to this.'
      }
      style={{ display: 'inline-flex' }}
    >
      <Chip tone="neutral">{held.has('modify') ? 'Limited' : 'Read-only'}</Chip>
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
        {reason || 'You do not have access to the data source behind this.'}
        {connectionId && (
          <WhyNot
            label="Why?"
            reason={{
              needed: 'select',
              meaning: 'Ask questions through it.',
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
  warn?: (principal: { user_id?: string; team_id?: string }) => Promise<string[]>
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
      {open && (
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
        </Modal>
      )}
    </>
  )
}
