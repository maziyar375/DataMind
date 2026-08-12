/**
 * Users: who can sign in, what they may do, and how they get a password.
 *
 * Shaped as an index page, because that is what it is — the third one in the
 * product, after Dashboards and Reports. It borrows their furniture rather
 * than restating it: the same page header, the same toolbar (search, a filter
 * offered only when there is something to filter, a sort), the same skeleton
 * while the list loads, and the same empty states. A fourth hand-written
 * variation of all of that is how three pages that should feel identical
 * quietly stop matching.
 *
 * Where it deliberately parts company is the shape of the list. Dashboards and
 * Reports offer cards *or* rows, because a dashboard has a face worth showing
 * — a name, a description, a tile count. A person does not: the facts about an
 * account are a name, an address, a role, a state and a date, which is a
 * **row**, one per line, comparable down the column. So this page is a list,
 * with no layout switch to make.
 *
 * The two things this page has that the others do not are both about care with
 * a person's account: the one-time password is shown once, in a panel that
 * says so, and every destructive act is confirmed before it happens.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { users as api } from '../api/client'
import type { User } from '../api/types'
import {
  Chip, CopyButton, DangerButton, EmptyState, ErrorNote, Field, GhostButton,
  GlyphBadge, Icon, MetaDot, Modal, PageHeader, PrimaryButton, SearchField,
  Segmented, Select, Spinner, TextInput, identityHue, initialOf,
} from '../components/ui'

type Role = 'ADMIN' | 'MEMBER'
type Status = 'ACTIVE' | 'INVITED' | 'DISABLED'
type RoleFilter = 'ALL' | 'ADMIN' | 'MEMBER'
type StatusFilter = 'ALL' | 'INVITED' | 'DISABLED'
type SortKey = 'name' | 'joined' | 'role'

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'name', label: 'Name (A–Z)' },
  { value: 'joined', label: 'Recently added' },
  { value: 'role', label: 'Admins first' },
]

function statusOf(user: User): Status {
  return (user.status as Status) ?? 'ACTIVE'
}

function sortUsers(users: User[], key: SortKey): User[] {
  const name = (user: User) => (user.display_name || user.email).toLowerCase()
  const joined = (user: User) => (user.created_at ? new Date(user.created_at).getTime() : 0)
  return [...users].sort((a, b) => {
    if (key === 'joined') return joined(b) - joined(a)
    if (key === 'role') {
      if (a.role !== b.role) return a.role === 'ADMIN' ? -1 : 1
      return name(a).localeCompare(name(b))
    }
    return name(a).localeCompare(name(b))
  })
}

export default function UsersPage({ currentUser }: { currentUser: User }) {
  // `null` is "not read yet", which is what the skeleton renders for. An empty
  // array is a real answer and gets the empty state instead.
  const [list, setList] = useState<User[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [role, setRole] = useState<RoleFilter>('ALL')
  const [status, setStatus] = useState<StatusFilter>('ALL')
  const [sort, setSort] = useState<SortKey>('name')

  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const [removing, setRemoving] = useState<User | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ email: string; password: string } | null>(null)

  const refresh = useCallback(async () => {
    setList(await api.list())
  }, [])

  useEffect(() => {
    refresh().catch(() => {
      setError('Could not load the user list.')
      setList([])
    })
  }, [refresh])

  const stats = useMemo(() => {
    const users = list ?? []
    return {
      total: users.length,
      admins: users.filter((user) => user.role === 'ADMIN').length,
      invited: users.filter((user) => statusOf(user) === 'INVITED').length,
      disabled: users.filter((user) => statusOf(user) === 'DISABLED').length,
    }
  }, [list])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matched = (list ?? []).filter((user) => {
      if (role !== 'ALL' && user.role !== role) return false
      if (status !== 'ALL' && statusOf(user) !== status) return false
      if (!needle) return true
      return (
        user.display_name.toLowerCase().includes(needle)
        || user.email.toLowerCase().includes(needle)
      )
    })
    return sortUsers(matched, sort)
  }, [list, query, role, status, sort])

  const filtering = query.trim().length > 0 || role !== 'ALL' || status !== 'ALL'

  function clearFilters() {
    setQuery('')
    setRole('ALL')
    setStatus('ALL')
  }

  const rowProps = useCallback(
    (user: User) => ({
      user,
      isSelf: user.id === currentUser.id,
      onEdit: () => {
        setNotice(null)
        setError(null)
        setEditing(user)
      },
      onRemove: () => setRemoving(user),
    }),
    [currentUser.id],
  )

  return (
    <div className="rm-dash-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <PageHeader
        title="Users"
        subtitle={
          list === null || list.length === 0 ? (
            'Who can sign in to this workspace, what they may change, and how they get a password.'
          ) : (
            <>
              <strong style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
                {stats.total}
              </strong>
              {` ${stats.total === 1 ? 'person' : 'people'}`}
              <MetaDot />
              {`${stats.admins} ${stats.admins === 1 ? 'admin' : 'admins'}`}
              {stats.invited > 0 && (
                <>
                  <MetaDot />
                  {`${stats.invited} invited`}
                </>
              )}
            </>
          )
        }
        actions={
          <PrimaryButton style={{ padding: '10px 17px' }} onClick={() => setAdding(true)}>
            <Icon.Plus /> Add user
          </PrimaryButton>
        }
      />

      {error && <div style={{ marginBottom: 14 }}><ErrorNote>{error}</ErrorNote></div>}

      {invite && <InvitePanel invite={invite} onDismiss={() => setInvite(null)} />}

      {notice && <Notice onDismiss={() => setNotice(null)}>{notice}</Notice>}

      {/* Shown only when an account is in a state somebody has to finish: an
          invitation nobody has accepted, or an account that was switched off.
          A strip that is absent when all is well is one that gets read when it
          appears — the same rule the Dashboards index follows. */}
      {(stats.invited > 0 || stats.disabled > 0) && !filtering && (
        <div className="rm-attention">
          <span aria-hidden style={{ display: 'flex', color: 'var(--amber)' }}>
            <Icon.Alert size={14} />
          </span>
          <span>
            {[
              stats.invited > 0
                && `${stats.invited} ${stats.invited === 1 ? 'invitation has' : 'invitations have'} not been used yet`,
              stats.disabled > 0
                && `${stats.disabled} ${stats.disabled === 1 ? 'account is' : 'accounts are'} disabled`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
          <button
            type="button"
            onClick={() => setStatus(stats.invited > 0 ? 'INVITED' : 'DISABLED')}
          >
            Show them
          </button>
        </div>
      )}

      {list !== null && list.length > 0 && (
        <div className="rm-dash-toolbar">
          <SearchField
            value={query}
            onChange={setQuery}
            ariaLabel="Search users"
            placeholder={`Search ${list.length} ${list.length === 1 ? 'person' : 'people'}…`}
          />
          <div className="rm-toolbar-group">
            <Segmented
              ariaLabel="Filter by role"
              value={role}
              onChange={setRole}
              options={[
                { value: 'ALL', label: 'Everyone' },
                { value: 'ADMIN', label: `Admins (${stats.admins})` },
                { value: 'MEMBER', label: 'Members' },
              ]}
            />
            {/* Offered only once an account is in one of those states; a filter
                that can never match anything is furniture, not a control. */}
            {(stats.invited > 0 || stats.disabled > 0) && (
              <Segmented
                ariaLabel="Filter by status"
                value={status}
                onChange={setStatus}
                options={[
                  { value: 'ALL', label: 'All' },
                  ...(stats.invited > 0
                    ? [{ value: 'INVITED' as const, label: `Invited (${stats.invited})` }]
                    : []),
                  ...(stats.disabled > 0
                    ? [{ value: 'DISABLED' as const, label: `Disabled (${stats.disabled})` }]
                    : []),
                ]}
              />
            )}
            <select
              aria-label="Sort users"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
              className="rm-toolbar-select"
            >
              {SORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {list === null ? (
        <IndexSkeleton />
      ) : list.length === 0 ? (
        <EmptyState
          icon={<Icon.Users size={20} />}
          title="No users yet"
          body="Add your first teammate. They receive a one-time password and are asked to change it on first sign-in."
          action={
            <PrimaryButton onClick={() => setAdding(true)}>
              <Icon.Plus size={15} />
              Add user
            </PrimaryButton>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={<Icon.Search size={20} />}
          title="Nothing matches"
          body={
            query.trim()
              ? `Nobody here is called “${query.trim()}”, and no address contains it.`
              : 'No account has that role and status.'
          }
          action={<GhostButton onClick={clearFilters}>Clear filters</GhostButton>}
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Column captions for the two right-hand columns, so the list reads
              as a table without being drawn as one. Dropped with those columns
              on a narrow screen, where the name is the whole row. */}
          <div
            className="rm-user-legend"
            aria-hidden
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 13,
              /* 15px, not 14: the rows below carry a 1px border this does
                 not, and the captions have to sit over their columns. */
              padding: '2px 15px 4px',
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: 'var(--text-faint)',
            }}
          >
            {/* Stands in for the avatar, so "Person" starts where the names do. */}
            <span style={{ width: 36 }} />
            <span style={{ flex: 1, minWidth: 0 }}>Person</span>
            <span style={{ width: 210 }}>Role &amp; status</span>
            <span style={{ width: 150 }}>Added</span>
            <span style={{ width: 27 }} />
          </div>
          {visible.map((user) => (
            <UserRow key={user.id} {...rowProps(user)} />
          ))}
        </div>
      )}

      {adding && (
        <AddUserModal
          onClose={() => setAdding(false)}
          onCreated={(created) => {
            setInvite(created)
            setAdding(false)
            void refresh()
          }}
        />
      )}

      {editing && (
        <EditUserModal
          user={editing}
          isSelf={editing.id === currentUser.id}
          isOnlyAdmin={editing.role === 'ADMIN' && stats.admins <= 1}
          onClose={() => setEditing(null)}
          onDone={(message) => {
            setEditing(null)
            if (message) setNotice(message)
            void refresh()
          }}
        />
      )}

      {removing && (
        <RemoveUserModal
          user={removing}
          onClose={() => setRemoving(null)}
          onDone={(message) => {
            setRemoving(null)
            setNotice(message)
            void refresh()
          }}
        />
      )}
    </div>
  )
}

// ── one person ──────────────────────────────────────────────────────────────
/**
 * One person, one line.
 *
 * The columns line up under the legend above the list rather than being drawn
 * in a grid: a bordered table with rules between every cell is heavier than
 * five facts need, and the row-as-a-card shape is the one the Dashboards list
 * already uses, so the two read as the same product.
 *
 * The hue on the avatar is the person's, derived from their id exactly as a
 * dashboard card's is — stable, decorative, and carrying no meaning of its
 * own. What *does* mean something (admin, invited, disabled, you) is a chip,
 * in words.
 */
function UserRow({
  user, isSelf, onEdit, onRemove,
}: {
  user: User
  isSelf: boolean
  onEdit: () => void
  onRemove: () => void
}) {
  return (
    <div
      className={`rm-dash-row rm-user-card${isSelf ? ' rm-user-self' : ''}`}
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        gap: 13,
        padding: '11px 14px',
        background: 'var(--panel)',
        borderRadius: 11,
        opacity: statusOf(user) === 'DISABLED' ? 0.66 : 1,
      }}
    >
      <Avatar user={user} size={36} />

      <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <button
          className="rm-dash-card-link"
          onClick={onEdit}
          dir="auto"
          title={`Edit ${user.display_name || user.email}`}
          style={{
            minWidth: 0,
            textAlign: 'left',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            color: 'var(--text-strong)',
            fontSize: 13.5,
            fontWeight: 600,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {user.display_name || user.email}
        </button>
        <span
          style={{
            fontSize: 12,
            color: 'var(--text-faint)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {user.email}
        </span>
      </div>

      {/* Fixed widths, matching the legend above the list: the point of a list
          is that the same fact is in the same place on every line, which a
          shrink-to-fit column cannot promise. */}
      <div
        className="rm-user-chips"
        style={{ display: 'flex', alignItems: 'center', gap: 6, width: 210, flexShrink: 0 }}
      >
        <UserChips user={user} isSelf={isSelf} />
      </div>

      <span
        className="rm-user-added"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          width: 150,
          flexShrink: 0,
          fontSize: 11.5,
          color: 'var(--text-faint)',
        }}
      >
        <Icon.Calendar size={12} />
        {joinedLabel(user.created_at)}
      </span>

      <UserMenu user={user} isSelf={isSelf} onEdit={onEdit} onRemove={onRemove} />
    </div>
  )
}

/** Role, status, and whether this is you — the three facts a face carries. */
function UserChips({ user, isSelf }: { user: User; isSelf: boolean }) {
  const status = statusOf(user)
  return (
    <>
      <Chip tone={user.role === 'ADMIN' ? 'accent' : 'neutral'}>
        {user.role === 'ADMIN' ? 'Admin' : 'Member'}
      </Chip>
      {status === 'INVITED' && <Chip tone="amber">Invited</Chip>}
      {status === 'DISABLED' && <Chip tone="red">Disabled</Chip>}
      {isSelf && <Chip tone="green">You</Chip>}
    </>
  )
}

/** Edit / remove, shared by the card and the row so the two cannot drift. */
function UserMenu({
  user, isSelf, onEdit, onRemove,
}: {
  user: User
  isSelf: boolean
  onEdit: () => void
  onRemove: () => void
}) {
  const [open, setOpen] = useState(false)
  const items = [
    { label: 'Edit user', run: onEdit },
    // Removing yourself is the one action that would lock the door behind you.
    ...(isSelf ? [] : [{ label: 'Remove', run: onRemove, danger: true }]),
  ]

  return (
    // Above the card-wide link overlay, so the kebab stays clickable.
    <div className="rm-tile-actions" style={{ position: 'relative', zIndex: 2, flexShrink: 0 }}>
      <button
        className="rm-icon-btn"
        aria-label={`Actions for ${user.display_name || user.email}`}
        onClick={() => setOpen((current) => !current)}
        style={{
          display: 'flex',
          width: 27,
          height: 27,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 8,
          border: 'none',
          background: open ? 'var(--panel-alt)' : 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel-alt)',
        }}
      >
        <Icon.More size={14} />
      </button>
      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 40 }}
            aria-hidden
          />
          <div
            role="menu"
            className="rm-enter"
            style={{
              position: 'absolute',
              top: 31,
              right: 0,
              zIndex: 41,
              minWidth: 152,
              padding: 5,
              background: 'var(--panel)',
              border: '1px solid var(--border-strong)',
              borderRadius: 10,
              boxShadow: '0 16px 40px -14px rgba(0,0,0,.5)',
            }}
          >
            {items.map((item) => (
              <button
                key={item.label}
                className="rm-menu-item"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  item.run()
                }}
                style={{
                  display: 'block',
                  width: '100%',
                  textAlign: 'left',
                  padding: '7px 9px',
                  borderRadius: 6,
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  fontSize: 12.5,
                  color: item.danger ? 'var(--red)' : 'var(--text)',
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ── add-user modal ────────────────────────────────────────────────────────────
function AddUserModal({
  onClose, onCreated,
}: {
  onClose: () => void
  onCreated: (invite: { email: string; password: string }) => void
}) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('MEMBER')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const canSubmit = name.trim().length > 0 && email.trim().length > 0 && !busy

  async function submit() {
    if (!canSubmit) return
    setBusy(true)
    setErr(null)
    try {
      const created = await api.create({
        display_name: name.trim(),
        email: email.trim(),
        role,
      })
      onCreated({ email: created.user.email, password: created.temporary_password })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not add that user.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Add user"
      subtitle="They receive a one-time password to sign in with."
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={submit} disabled={!canSubmit}>
            {busy ? <Spinner /> : <Icon.Plus size={15} />}
            Create user
          </PrimaryButton>
        </>
      }
    >
      {err && <ErrorNote>{err}</ErrorNote>}
      <Field label="Full name">
        <TextInput
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="e.g. Ada Lovelace"
        />
      </Field>
      <Field label="Email">
        <TextInput
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="e.g. ada@company.com"
        />
      </Field>
      <Field label="Role" hint="Admins manage users, connections, and models.">
        <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="MEMBER">Member</option>
          <option value="ADMIN">Admin</option>
        </Select>
      </Field>
    </Modal>
  )
}

// ── edit-user modal ───────────────────────────────────────────────────────────
function EditUserModal({
  user, isSelf, isOnlyAdmin, onClose, onDone,
}: {
  user: User
  isSelf: boolean
  isOnlyAdmin: boolean
  onClose: () => void
  onDone: (notice?: string) => void
}) {
  const initialStatus = statusOf(user)

  const [name, setName] = useState(user.display_name)
  const [email, setEmail] = useState(user.email)
  const [role, setRole] = useState<Role>(user.role)
  const [status, setStatus] = useState<Status>(initialStatus)
  const [savingProfile, setSavingProfile] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [pw, setPw] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [pwOk, setPwOk] = useState(false)

  const trimmedName = name.trim()
  const trimmedEmail = email.trim()
  const dirty =
    trimmedName !== user.display_name ||
    trimmedEmail.toLowerCase() !== user.email.toLowerCase() ||
    role !== user.role ||
    status !== initialStatus
  const canSave = dirty && !!trimmedName && !!trimmedEmail && !savingProfile

  const roleLocked = isSelf || isOnlyAdmin
  const roleHint = isSelf
    ? "You can't change your own role."
    : isOnlyAdmin
      ? 'The only administrator cannot be demoted.'
      : undefined

  async function saveProfile() {
    if (!canSave) return
    setSavingProfile(true)
    setErr(null)
    const payload: {
      display_name?: string
      email?: string
      role?: string
      status?: string
    } = {}
    if (trimmedName !== user.display_name) payload.display_name = trimmedName
    if (trimmedEmail.toLowerCase() !== user.email.toLowerCase()) payload.email = trimmedEmail
    if (role !== user.role) payload.role = role
    if (status !== initialStatus) payload.status = status
    try {
      await api.update(user.id, payload)
      onDone(`Saved changes to ${trimmedName || trimmedEmail}.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save those changes.')
      setSavingProfile(false)
    }
  }

  async function setPassword() {
    if (pw.length < 8) return
    setPwBusy(true)
    setErr(null)
    setPwOk(false)
    try {
      await api.setPassword(user.id, pw)
      setPw('')
      setPwOk(true)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not set that password.')
    } finally {
      setPwBusy(false)
    }
  }

  return (
    <Modal
      title="Edit user"
      subtitle={user.email}
      width={480}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton onClick={saveProfile} disabled={!canSave}>
            {savingProfile ? <Spinner /> : <Icon.Check size={15} />}
            Save changes
          </PrimaryButton>
        </>
      }
    >
      {err && <ErrorNote>{err}</ErrorNote>}

      {/* Who is being edited, stated at the top of the form rather than only in
          the dialog's subtitle — this is the one place a wrong click is
          expensive, and an avatar beside the name is what catches it. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <Avatar user={user} size={38} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          <span
            style={{
              fontSize: 13.5,
              fontWeight: 600,
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {user.display_name || user.email}
          </span>
          <span style={{ display: 'flex', gap: 6 }}>
            <UserChips user={user} isSelf={isSelf} />
          </span>
        </div>
      </div>

      <Divider />

      <Field label="Full name">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Ada Lovelace" />
      </Field>
      <Field label="Email">
        <TextInput
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="e.g. ada@company.com"
        />
      </Field>

      <div className="rm-col-2" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Role" hint={roleHint}>
          <Select
            value={role}
            disabled={roleLocked}
            onChange={(e) => setRole(e.target.value as Role)}
            style={{ opacity: roleLocked ? 0.6 : 1 }}
          >
            <option value="MEMBER">Member</option>
            <option value="ADMIN">Admin</option>
          </Select>
        </Field>
        <Field label="Status" hint={isSelf ? "You can't change your own status." : undefined}>
          <Select
            value={status}
            disabled={isSelf}
            onChange={(e) => setStatus(e.target.value as Status)}
            style={{ opacity: isSelf ? 0.6 : 1 }}
          >
            <option value="ACTIVE">Active</option>
            <option value="INVITED">Invited</option>
            <option value="DISABLED">Disabled</option>
          </Select>
        </Field>
      </div>

      <Divider />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Set a new password</label>
        <div style={{ display: 'flex', gap: 8 }}>
          <TextInput
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => {
              setPw(e.target.value)
              setPwOk(false)
            }}
            onKeyDown={(e) => e.key === 'Enter' && setPassword()}
            placeholder="At least 8 characters"
            aria-label={`New password for ${user.email}`}
            style={{ flex: 1 }}
          />
          <PrimaryButton onClick={setPassword} disabled={pwBusy || pw.length < 8}>
            {pwBusy ? <Spinner /> : <Icon.Key size={14} />}
            Set
          </PrimaryButton>
        </div>
        {pwOk ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: 'var(--green)' }}>
            <Icon.Check size={12} stroke="var(--green)" />
            Password updated — existing sessions were signed out.
          </span>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            Sets a permanent password and signs out any active sessions.
          </span>
        )}
      </div>
    </Modal>
  )
}

// ── remove-user modal ─────────────────────────────────────────────────────────
/**
 * Removal is confirmed on its own, rather than behind an in-place two-click
 * dance inside the edit form. It is the one action here that cannot be undone,
 * and it now has one home reachable from the card, the row, and the menu.
 */
function RemoveUserModal({
  user, onClose, onDone,
}: {
  user: User
  onClose: () => void
  onDone: (notice: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function confirm() {
    setBusy(true)
    setErr(null)
    try {
      await api.remove(user.id)
      onDone(`Removed ${user.display_name || user.email}.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not remove that user.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="Remove user"
      subtitle={user.email}
      width={420}
      onClose={onClose}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton onClick={confirm} disabled={busy}>
            {busy ? <Spinner /> : <Icon.Trash size={13} />}
            Remove user
          </DangerButton>
        </>
      }
    >
      {err && <ErrorNote>{err}</ErrorNote>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
        <Avatar user={user} size={38} />
        <span style={{ fontSize: 13.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {user.display_name || user.email}
        </span>
      </div>
      <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--text-dim)' }}>
        This deletes the account and revokes access immediately. Conversations,
        dashboards, and reports they made keep their recorded history — only the
        sign-in goes away.
      </p>
    </Modal>
  )
}

// ── small pieces ──────────────────────────────────────────────────────────────
function Avatar({ user, size = 36 }: { user: User; size?: number }) {
  return (
    <GlyphBadge size={size} hue={identityHue(user.id)}>
      {initialOf(user.display_name || user.email)}
    </GlyphBadge>
  )
}

function Divider() {
  return <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
}

/**
 * The one-time password, shown once and never again.
 *
 * Deliberately the loudest thing on the page while it is up: it is the only
 * state in the product where dismissing a panel destroys information the
 * server will not repeat.
 */
function InvitePanel({
  invite, onDismiss,
}: {
  invite: { email: string; password: string }
  onDismiss: () => void
}) {
  return (
    <div
      className="rm-enter"
      style={{
        display: 'flex',
        gap: 12,
        marginBottom: 16,
        padding: '14px 16px',
        border: '1px solid var(--amber-border)',
        background: 'var(--amber-bg)',
        borderRadius: 12,
      }}
    >
      <span
        aria-hidden
        style={{
          display: 'grid',
          placeItems: 'center',
          width: 32,
          height: 32,
          flexShrink: 0,
          borderRadius: 10,
          border: '1px solid var(--amber-border)',
          color: 'var(--amber)',
        }}
      >
        <Icon.Key size={15} />
      </span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 9, minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-strong)' }}>
          Temporary password for {invite.email}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <code
            className="mono"
            style={{
              fontSize: 13.5,
              padding: '8px 12px',
              background: 'var(--code-bg)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              color: 'var(--code-text)',
              userSelect: 'all',
            }}
          >
            {invite.password}
          </code>
          <CopyButton text={invite.password} label="Copy" />
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text-dim)', lineHeight: 1.5 }}>
          Copy this now — it is shown once and cannot be retrieved later. The user
          is asked to change it on first sign-in.
        </div>
      </div>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        title="Dismiss"
        className="rm-icon-btn"
        style={{
          alignSelf: 'flex-start',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 26,
          height: 26,
          flexShrink: 0,
          border: 'none',
          borderRadius: 7,
          background: 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel)',
        }}
      >
        <Icon.Close size={13} />
      </button>
    </div>
  )
}

/** A saved change, acknowledged and dismissible. */
function Notice({ children, onDismiss }: { children: ReactNode; onDismiss: () => void }) {
  return (
    <div
      className="rm-enter"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 9,
        marginBottom: 16,
        padding: '9px 13px',
        fontSize: 12.5,
        color: 'var(--text2)',
        background: 'var(--green-bg)',
        border: '1px solid var(--green-border)',
        borderRadius: 10,
      }}
    >
      <Icon.Check size={14} stroke="var(--green)" />
      <span style={{ flex: 1 }}>{children}</span>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="rm-icon-btn"
        style={{
          display: 'flex',
          width: 22,
          height: 22,
          alignItems: 'center',
          justifyContent: 'center',
          border: 'none',
          borderRadius: 6,
          background: 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          ['--rm-hover-bg' as string]: 'var(--panel)',
        }}
      >
        <Icon.Close size={13} />
      </button>
    </div>
  )
}

/** Rows in outline while the list loads, so the page does not jump. */
function IndexSkeleton() {
  return (
    <div aria-hidden style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {[0, 1, 2, 3, 4, 5].map((index) => (
        <div
          key={index}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 13,
            padding: '11px 14px',
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 11,
          }}
        >
          <div className="rm-bone" style={{ width: 36, height: 36, borderRadius: 10 }} />
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div className="rm-bone" style={{ width: `${34 - index * 3}%`, height: 10, borderRadius: 5 }} />
            <div className="rm-bone" style={{ width: `${46 - index * 3}%`, height: 9, borderRadius: 5 }} />
          </div>
          <div className="rm-user-chips" style={{ display: 'flex', gap: 6, width: 210 }}>
            <div className="rm-bone" style={{ width: 58, height: 18, borderRadius: 5 }} />
          </div>
          <div className="rm-user-added rm-bone" style={{ width: 110, height: 9, borderRadius: 5 }} />
          <div style={{ width: 27 }} />
        </div>
      ))}
    </div>
  )
}

/** The date alone: the column above it is already captioned "Added". */
function joinedLabel(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}
