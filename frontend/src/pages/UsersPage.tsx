import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { users as api } from '../api/client'
import type { User } from '../api/types'
import {
  Chip, CopyButton, DangerButton, EmptyState, ErrorNote, Field, GhostButton,
  Icon, Modal, PrimaryButton, Select, Spinner, TextInput, initialOf,
} from '../components/ui'

type Role = 'ADMIN' | 'MEMBER'
type Status = 'ACTIVE' | 'INVITED' | 'DISABLED'

const GRID = '1fr 120px 116px 132px 56px'

export default function UsersPage({ currentUser }: { currentUser: User }) {
  const [list, setList] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [invite, setInvite] = useState<{ email: string; password: string } | null>(null)

  const refresh = useCallback(async () => {
    setList(await api.list())
  }, [])

  useEffect(() => {
    refresh()
      .catch(() => setError('Could not load the user list.'))
      .finally(() => setLoading(false))
  }, [refresh])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter(
      (u) =>
        u.display_name.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q),
    )
  }, [list, query])

  const adminCount = useMemo(
    () => list.filter((u) => u.role === 'ADMIN').length,
    [list],
  )

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '16px 28px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-strong)' }}>
            Users
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-faint)' }}>
            Manage who can access this workspace, their roles, and passwords.
          </div>
        </div>
        <PrimaryButton onClick={() => setAdding(true)}>
          <Icon.Plus size={15} />
          Add user
        </PrimaryButton>
      </header>

      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '22px 28px',
          width: '100%',
          maxWidth: 960,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {error && <ErrorNote>{error}</ErrorNote>}

        {notice && (
          <Banner tone="green" icon={<Icon.Check size={14} stroke="var(--green)" />} onDismiss={() => setNotice(null)}>
            {notice}
          </Banner>
        )}

        {invite && (
          <div
            style={{
              border: '1px solid var(--amber-border)',
              background: 'var(--amber-bg)',
              borderRadius: 10,
              padding: '14px 16px',
              display: 'flex',
              flexDirection: 'column',
              gap: 9,
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)' }}>
              Temporary password for {invite.email}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <code
                className="mono"
                style={{
                  fontSize: 13.5,
                  padding: '8px 12px',
                  background: 'var(--code-bg)',
                  borderRadius: 7,
                  color: 'var(--code-text)',
                  userSelect: 'all',
                }}
              >
                {invite.password}
              </code>
              <CopyButton text={invite.password} label="Copy" />
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-dim)' }}>
              Copy this now — it is shown once and cannot be retrieved later. The
              user is asked to change it on first sign-in.
            </div>
            <button
              onClick={() => setInvite(null)}
              style={{
                alignSelf: 'flex-start',
                fontSize: 12,
                color: 'var(--text-dim)',
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                padding: 0,
                textDecoration: 'underline',
              }}
            >
              Dismiss
            </button>
          </div>
        )}

        {/* toolbar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ position: 'relative', flex: 1, maxWidth: 300 }}>
            <span
              style={{
                position: 'absolute',
                left: 11,
                top: '50%',
                transform: 'translateY(-50%)',
                display: 'flex',
                color: 'var(--text-faint)',
                pointerEvents: 'none',
              }}
            >
              <Icon.Search size={14} />
            </span>
            <TextInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search name or email"
              aria-label="Search users"
              style={{ borderRadius: 8, padding: '9px 12px 9px 32px' }}
            />
          </div>
          <span style={{ fontSize: 12, color: 'var(--text-faint)', whiteSpace: 'nowrap' }}>
            {list.length} {list.length === 1 ? 'user' : 'users'}
          </span>
        </div>

        {/* table */}
        <div
          style={{
            border: '1px solid var(--border)',
            borderRadius: 12,
            overflow: 'hidden',
            background: 'var(--panel)',
          }}
        >
          <div style={{ overflowX: 'auto' }}>
            <div style={{ minWidth: 640 }}>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: GRID,
                  alignItems: 'center',
                  gap: 12,
                  padding: '10px 16px',
                  borderBottom: '1px solid var(--border)',
                  fontSize: 11,
                  fontWeight: 700,
                  color: 'var(--text-faint)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                }}
              >
                <span>User</span>
                <span>Role</span>
                <span>Status</span>
                <span>Joined</span>
                <span style={{ textAlign: 'right' }}>Edit</span>
              </div>

              {loading ? (
                <div style={{ display: 'grid', placeItems: 'center', padding: '40px 0' }}>
                  <Spinner size={18} />
                </div>
              ) : filtered.length === 0 ? (
                <EmptyState
                  title={query ? 'No matching users' : 'No users yet'}
                  body={
                    query
                      ? 'Try a different name or email.'
                      : 'Add your first teammate to give them access.'
                  }
                  action={
                    query ? undefined : (
                      <PrimaryButton onClick={() => setAdding(true)}>
                        <Icon.Plus size={15} />
                        Add user
                      </PrimaryButton>
                    )
                  }
                />
              ) : (
                filtered.map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    isSelf={user.id === currentUser.id}
                    onEdit={() => {
                      setNotice(null)
                      setError(null)
                      setEditing(user)
                    }}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {adding && (
        <AddUserModal
          onClose={() => setAdding(false)}
          onCreated={(inv) => {
            setInvite(inv)
            setAdding(false)
            void refresh()
          }}
        />
      )}

      {editing && (
        <EditUserModal
          user={editing}
          isSelf={editing.id === currentUser.id}
          isOnlyAdmin={editing.role === 'ADMIN' && adminCount <= 1}
          onClose={() => setEditing(null)}
          onDone={(msg) => {
            setEditing(null)
            if (msg) setNotice(msg)
            void refresh()
          }}
        />
      )}
    </div>
  )
}

// ── row ─────────────────────────────────────────────────────────────────────
function UserRow({
  user, isSelf, onEdit,
}: {
  user: User
  isSelf: boolean
  onEdit: () => void
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: GRID,
        alignItems: 'center',
        gap: 12,
        padding: '11px 16px',
        borderTop: '1px solid var(--border)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 11, minWidth: 0 }}>
        <Avatar user={user} />
        <div style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.25, minWidth: 0 }}>
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
            {isSelf && (
              <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-faint)', marginLeft: 7 }}>
                You
              </span>
            )}
          </span>
          <span
            style={{
              fontSize: 11.5,
              color: 'var(--text-faint)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {user.email}
          </span>
        </div>
      </div>

      <span>
        <Chip tone={user.role === 'ADMIN' ? 'accent' : 'neutral'}>
          {user.role === 'ADMIN' ? 'Admin' : 'Member'}
        </Chip>
      </span>

      <span>
        <StatusChip status={user.status} />
      </span>

      <span style={{ fontSize: 12.5, color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
        {joinedLabel(user.created_at)}
      </span>

      <span style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          onClick={onEdit}
          title="Edit user"
          aria-label={`Edit ${user.display_name || user.email}`}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 32,
            height: 32,
            background: 'transparent',
            color: 'var(--text-dim)',
            border: '1px solid var(--border-strong)',
            borderRadius: 7,
            cursor: 'pointer',
          }}
        >
          <Icon.Pencil size={14} />
        </button>
      </span>
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
          placeholder="Ada Lovelace"
        />
      </Field>
      <Field label="Email">
        <TextInput
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="ada@company.com"
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
  const initialStatus = (user.status as Status) ?? 'ACTIVE'

  const [name, setName] = useState(user.display_name)
  const [email, setEmail] = useState(user.email)
  const [role, setRole] = useState<Role>(user.role)
  const [status, setStatus] = useState<Status>(initialStatus)
  const [savingProfile, setSavingProfile] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const [pw, setPw] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [pwOk, setPwOk] = useState(false)

  const [confirmRemove, setConfirmRemove] = useState(false)
  const [removing, setRemoving] = useState(false)

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

  async function remove() {
    setRemoving(true)
    setErr(null)
    try {
      await api.remove(user.id)
      onDone(`Removed ${user.display_name || user.email}.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not remove that user.')
      setRemoving(false)
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

      <Field label="Full name">
        <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
      </Field>
      <Field label="Email">
        <TextInput
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email@company.com"
        />
      </Field>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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

      {!isSelf && (
        <>
          <Divider />
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text)' }}>
                Remove user
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-faint)' }}>
                Deletes the account and revokes access immediately.
              </div>
            </div>
            {confirmRemove ? (
              <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                <GhostButton onClick={() => setConfirmRemove(false)}>Cancel</GhostButton>
                <DangerButton onClick={remove} disabled={removing}>
                  {removing ? <Spinner /> : <Icon.Trash size={13} />}
                  Confirm
                </DangerButton>
              </div>
            ) : (
              <DangerButton onClick={() => setConfirmRemove(true)}>
                <Icon.Trash size={13} />
                Remove
              </DangerButton>
            )}
          </div>
        </>
      )}
    </Modal>
  )
}

// ── small pieces ──────────────────────────────────────────────────────────────
function Avatar({ user }: { user: User }) {
  const admin = user.role === 'ADMIN'
  return (
    <span
      style={{
        width: 36,
        height: 36,
        borderRadius: 10,
        background: admin ? 'var(--accent-bg)' : 'var(--panel-alt)',
        color: admin ? 'var(--accent)' : 'var(--text-dim)',
        border: `1px solid ${admin ? 'var(--accent-border)' : 'var(--border)'}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: 14,
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      {initialOf(user.display_name || user.email)}
    </span>
  )
}

function StatusChip({ status }: { status?: string }) {
  const map = {
    ACTIVE: { tone: 'green', label: 'Active' },
    INVITED: { tone: 'amber', label: 'Invited' },
    DISABLED: { tone: 'neutral', label: 'Disabled' },
  } as const
  const s = map[status as keyof typeof map] ?? map.ACTIVE
  return <Chip tone={s.tone}>{s.label}</Chip>
}

function Divider() {
  return <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
}

function Banner({
  tone, icon, children, onDismiss,
}: {
  tone: 'green'
  icon: ReactNode
  children: ReactNode
  onDismiss: () => void
}) {
  const colors = { green: { color: 'var(--green)', bg: 'var(--green-bg)' } }[tone]
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        fontSize: 12.5,
        color: colors.color,
        background: colors.bg,
        borderRadius: 8,
        padding: '9px 12px',
      }}
    >
      {icon}
      <span style={{ flex: 1 }}>{children}</span>
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        style={{ display: 'flex', background: 'transparent', border: 'none', color: 'inherit', cursor: 'pointer', opacity: 0.7 }}
      >
        <Icon.Close size={13} />
      </button>
    </div>
  )
}

function joinedLabel(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}
