/**
 * Roles: what each one carries, who holds it, and what that means in words.
 *
 * The Administration section's second tab, and the first screen in the product
 * where somebody decides what another person may do. Three things about it are
 * deliberate:
 *
 * * **The checklist and the matrix are rendered from the server's own
 *   vocabulary.** `GET /roles/capabilities` supplies the eighteen words, their
 *   grouping and their one-line explanations; `GET /roles/privileges` supplies
 *   the sentence each privilege means on each resource type — the same strings
 *   a backend conformance test asserts. A screen that wrote its own wording
 *   would drift from what the API actually does, and the drift would only show
 *   up as somebody having granted the wrong thing.
 * * **A system role is renameable and not re-scopeable, and it says so on the
 *   screen** rather than refusing after a save. "Normal User" is a bad name in
 *   some installations; what "Auditor" *can do* is a specification eight tests
 *   and one migration agree on.
 * * **Deleting a role that people hold is refused by the server, naming
 *   them**, and this screen does not pretend otherwise — the button is
 *   disabled with the count beside it, so the refusal is visible before it is
 *   provoked.
 *
 * It borrows the master–detail frame from `components/settings.tsx`, which is
 * the same shape Data sources and LLM providers use. A fourth hand-written
 * variation of a list beside a form is how three screens that should feel
 * identical quietly stop matching.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { roles as api } from '../api/client'
import type { CapabilityEntry, Role, ScopedPrivilege } from '../api/types'
import {
  Chip, DangerButton, ErrorNote, Field, GhostButton, GlyphBadge, Icon, Modal,
  PrimaryButton, Spinner, TextArea, TextInput,
} from '../components/ui'
import {
  DetailBody, DetailHeader, MasterColumn, MasterItem, Section,
} from '../components/settings'
import { useCan } from '../permissions'

/** The five rungs, weakest first — the lattice, in the order it is read. */
const PRIVILEGES = ['describe', 'select', 'modify', 'delete', 'manage'] as const

/** Resource types, in the order the product introduces them. */
const RESOURCE_ORDER = [
  'connection', 'knowledge', 'semantic_layer', 'llm_config',
  'dashboard', 'report', 'conversation', 'team',
]

function label(value: string): string {
  return value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

function key(privilege: ScopedPrivilege): string {
  return `${privilege.resource_type}:${privilege.privilege}`
}

export default function RolesTab() {
  const can = useCan()
  const mayManage = can('role.manage')

  const [list, setList] = useState<Role[] | null>(null)
  const [catalog, setCatalog] = useState<CapabilityEntry[]>([])
  const [meanings, setMeanings] = useState<Record<string, Record<string, string>>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [removing, setRemoving] = useState<Role | null>(null)

  const refresh = useCallback(async () => {
    const fetched = await api.list()
    setList(fetched)
    return fetched
  }, [])

  useEffect(() => {
    // The two vocabularies come with the list rather than when a role is
    // opened: they are the same for every role, and fetching them per
    // selection would put a spinner inside a checklist that never changes.
    Promise.all([refresh(), api.capabilities(), api.privileges()])
      .then(([, entries, matrix]) => {
        setCatalog(entries)
        setMeanings(matrix)
      })
      .catch(() => {
        setError('Could not load roles.')
        setList([])
      })
  }, [refresh])

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const rows = list ?? []
    if (!needle) return rows
    return rows.filter(
      (role) =>
        role.name.toLowerCase().includes(needle)
        || role.description.toLowerCase().includes(needle),
    )
  }, [list, query])

  const selected = useMemo(
    () => (list ?? []).find((role) => role.id === selectedId) ?? null,
    [list, selectedId],
  )

  // Open the first role once, so the pane is never an empty box beside a full
  // list — the same behaviour Data sources has.
  useEffect(() => {
    if (selectedId === null && list && list.length > 0) setSelectedId(list[0].id)
  }, [list, selectedId])

  const groups = useMemo(() => {
    const out = new Map<string, CapabilityEntry[]>()
    for (const entry of catalog) {
      const bucket = out.get(entry.group) ?? []
      bucket.push(entry)
      out.set(entry.group, bucket)
    }
    return [...out.entries()]
  }, [catalog])

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <MasterColumn
        title="Roles"
        icon={<Icon.Users size={15} />}
        note="A role is a set of permissions. People hold as many as they need; the union applies."
        count={(list ?? []).length}
        onNew={mayManage ? () => setCreating(true) : undefined}
        newLabel="New role"
        empty="No roles yet"
        query={query}
        onQuery={setQuery}
        loading={list === null}
      >
        {visible.map((role) => (
          <MasterItem
            key={role.id}
            title={role.name}
            subtitle={`${role.capabilities.length} permission${
              role.capabilities.length === 1 ? '' : 's'
            } · ${role.holders ?? 0} holder${role.holders === 1 ? '' : 's'}`}
            active={role.id === selectedId}
            tone={role.is_system ? 'neutral' : 'green'}
            toneLabel={role.is_system ? 'Built in' : 'Custom'}
            glyph={
              <GlyphBadge size={26}>
                <Icon.Shield size={13} />
              </GlyphBadge>
            }
            onClick={() => setSelectedId(role.id)}
          />
        ))}
      </MasterColumn>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {error && (
          <div style={{ padding: '16px 28px 0' }}>
            <ErrorNote>{error}</ErrorNote>
          </div>
        )}
        {list === null ? (
          <div style={{ display: 'grid', placeItems: 'center', flex: 1 }}>
            <Spinner size={18} />
          </div>
        ) : selected === null ? null : (
          <RoleDetail
            key={selected.id}
            role={selected}
            groups={groups}
            meanings={meanings}
            editable={mayManage}
            onSaved={(saved) =>
              setList((current) =>
                (current ?? []).map((row) => (row.id === saved.id ? { ...row, ...saved } : row)),
              )
            }
            onRemove={() => setRemoving(selected)}
          />
        )}
      </div>

      {creating && (
        <NewRoleModal
          groups={groups}
          onClose={() => setCreating(false)}
          onCreated={async (created) => {
            setCreating(false)
            await refresh()
            setSelectedId(created.id)
          }}
        />
      )}

      {removing && (
        <RemoveRoleModal
          role={removing}
          onClose={() => setRemoving(null)}
          onRemoved={async () => {
            setRemoving(null)
            setSelectedId(null)
            await refresh()
          }}
        />
      )}
    </div>
  )
}

// ── one role ──────────────────────────────────────────────────────────────
function RoleDetail({
  role, groups, meanings, editable, onSaved, onRemove,
}: {
  role: Role
  groups: [string, CapabilityEntry[]][]
  meanings: Record<string, Record<string, string>>
  editable: boolean
  onSaved: (role: Role) => void
  onRemove: () => void
}) {
  const [name, setName] = useState(role.name)
  const [description, setDescription] = useState(role.description)
  const [held, setHeld] = useState(() => new Set(role.capabilities))
  const [scoped, setScoped] = useState(() => new Set(role.scoped_privileges.map(key)))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // A built-in role's *permissions* are fixed by a migration; its name is not.
  // Stated once here and read three times below, so the two halves of that
  // rule cannot drift apart in the markup.
  const mayRename = editable
  const mayRescope = editable && !role.is_system

  const dirty =
    name !== role.name
    || description !== role.description
    || !sameSet(held, new Set(role.capabilities))
    || !sameSet(scoped, new Set(role.scoped_privileges.map(key)))

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const updated = await api.update(role.id, {
        name,
        description,
        ...(mayRescope
          ? {
            capabilities: [...held],
            scoped_privileges: [...scoped].map((entry) => {
              const [resource_type, privilege] = entry.split(':')
              return { resource_type, privilege }
            }),
          }
          : {}),
      })
      onSaved(updated)
      setSaved(true)
      window.setTimeout(() => setSaved(false), 2200)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save this role.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <DetailHeader
        glyph={
          <GlyphBadge size={40}>
            <Icon.Shield size={19} />
          </GlyphBadge>
        }
        title={role.name}
        subtitle={`${role.capabilities.length} permission${
          role.capabilities.length === 1 ? '' : 's'
        } · held by ${role.holders ?? 0}`}
        chips={
          <>
            {role.is_system && <Chip tone="neutral">Built in</Chip>}
            {(role.holders ?? 0) > 0 && (
              <Chip tone="accent">{role.holders} holder{role.holders === 1 ? '' : 's'}</Chip>
            )}
          </>
        }
        actions={
          <>
            {saved && !dirty && (
              <span style={{ fontSize: 12, color: 'var(--green)' }}>Saved</span>
            )}
            {editable && (
              <PrimaryButton disabled={!dirty || saving} onClick={save}>
                {saving ? <Spinner /> : <Icon.Check size={15} />}
                Save
              </PrimaryButton>
            )}
            {editable && !role.is_system && (
              <GhostButton
                onClick={onRemove}
                title={
                  (role.holders ?? 0) > 0
                    ? 'This role is still assigned to somebody'
                    : 'Delete this role'
                }
                disabled={(role.holders ?? 0) > 0}
              >
                <Icon.Trash size={14} />
                Delete
              </GhostButton>
            )}
          </>
        }
      />

      <DetailBody>
        {error && <ErrorNote>{error}</ErrorNote>}

        <Section
          title="Identity"
          description={
            role.is_system
              ? 'Built-in roles can be renamed and described in your own words. What they can do is fixed — create a custom role for a different set of permissions.'
              : 'What this role is called, and what it is for.'
          }
          icon={<Icon.Key size={13} />}
        >
          <Field label="Name">
            <TextInput
              value={name}
              disabled={!mayRename}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Description" hint="Shown beside the role wherever it is offered.">
            <TextArea
              rows={2}
              value={description}
              disabled={!mayRename}
              onChange={(event) => setDescription(event.target.value)}
            />
          </Field>
        </Section>

        <Section
          title="Permissions"
          description="App-wide verbs. Each one is something this role's holders may do anywhere in the product — not something they may do to a particular dashboard or database."
          icon={<Icon.Check size={13} />}
        >
          {!mayRescope && (
            <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.55 }}>
              {role.is_system
                ? 'These are fixed by the installation and shown here so you can see exactly what this role grants.'
                : 'You do not have permission to change what a role can do.'}
            </p>
          )}
          {groups.map(([group, entries]) => (
            <div key={group} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--text-faint)',
                }}
              >
                {group}
              </div>
              {entries.map((entry) => (
                <CapabilityRow
                  key={entry.name}
                  entry={entry}
                  checked={held.has(entry.name)}
                  disabled={!mayRescope}
                  onToggle={() =>
                    setHeld((current) => toggled(current, entry.name))
                  }
                />
              ))}
            </div>
          ))}
        </Section>

        <Section
          title="Resource privileges"
          description="What this role can do to every resource of a type — now and in future. A role never names one particular dashboard or database; that is a share, made beside the resource itself."
          icon={<Icon.Database size={13} />}
        >
          <ScopedMatrix
            meanings={meanings}
            held={scoped}
            disabled={!mayRescope}
            onToggle={(entry) => setScoped((current) => toggled(current, entry))}
          />
        </Section>
      </DetailBody>
    </>
  )
}

function CapabilityRow({
  entry, checked, disabled, onToggle,
}: {
  entry: CapabilityEntry
  checked: boolean
  disabled: boolean
  onToggle: () => void
}) {
  return (
    <label
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: '8px 10px',
        borderRadius: 9,
        border: '1px solid var(--border)',
        background: checked ? 'var(--accent-bg)' : 'var(--panel)',
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled && !checked ? 0.62 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={onToggle}
        style={{ marginTop: 2, accentColor: 'var(--accent)' }}
      />
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-strong)' }}>
          {entry.label}
        </span>
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--text-faint)' }}>
          {entry.name}
        </span>
      </span>
    </label>
  )
}

/**
 * The eight resource types down the side, the five privileges across the top.
 *
 * Every cell's tooltip is the backend's own sentence for that pair, so what
 * somebody is about to grant is stated in the same words the API's conformance
 * test asserts. A grid of unlabelled ticks would be a permission model nobody
 * could read.
 */
function ScopedMatrix({
  meanings, held, disabled, onToggle,
}: {
  meanings: Record<string, Record<string, string>>
  held: Set<string>
  disabled: boolean
  onToggle: (entry: string) => void
}) {
  const types = RESOURCE_ORDER.filter((type) => meanings[type])
  if (types.length === 0) {
    return (
      <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)' }}>
        Loading the privilege matrix…
      </p>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', minWidth: 520 }}>
        <thead>
          <tr>
            <th
              style={{
                textAlign: 'left',
                fontSize: 10.5,
                fontWeight: 700,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color: 'var(--text-faint)',
                padding: '0 10px 8px 0',
              }}
            >
              Resource
            </th>
            {PRIVILEGES.map((privilege) => (
              <th
                key={privilege}
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--text-faint)',
                  padding: '0 6px 8px',
                }}
              >
                {privilege}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {types.map((type) => (
            <tr key={type} style={{ borderTop: '1px solid var(--border)' }}>
              <th
                scope="row"
                style={{
                  textAlign: 'left',
                  fontSize: 12.5,
                  fontWeight: 600,
                  color: 'var(--text)',
                  padding: '8px 10px 8px 0',
                  whiteSpace: 'nowrap',
                }}
              >
                {label(type)}
              </th>
              {PRIVILEGES.map((privilege) => {
                const entry = `${type}:${privilege}`
                const meaning = meanings[type]?.[privilege] ?? ''
                return (
                  <td key={privilege} style={{ textAlign: 'center', padding: '6px' }}>
                    <input
                      type="checkbox"
                      aria-label={`${label(type)} — ${privilege}`}
                      title={meaning}
                      checked={held.has(entry)}
                      disabled={disabled}
                      onChange={() => onToggle(entry)}
                      style={{ accentColor: 'var(--accent)' }}
                    />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── create and delete ─────────────────────────────────────────────────────
function NewRoleModal({
  groups, onClose, onCreated,
}: {
  groups: [string, CapabilityEntry[]][]
  onClose: () => void
  onCreated: (role: Role) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [held, setHeld] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      onCreated(
        await api.create({
          name: name.trim(),
          description: description.trim(),
          capabilities: [...held],
        }),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create this role.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="New role"
      onClose={onClose}
      width={560}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <PrimaryButton disabled={!name.trim() || busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Plus size={15} />}
            Create role
          </PrimaryButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Field label="Name">
          <TextInput
            autoFocus
            value={name}
            placeholder="Analyst"
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field
          label="Description"
          hint="One sentence, for whoever assigns this later."
        >
          <TextArea
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            Permissions can be changed after the role exists — resource
            privileges too, on the role's own screen.
          </span>
          {groups.map(([group, entries]) => (
            <div key={group} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--text-faint)',
                }}
              >
                {group}
              </div>
              {entries.map((entry) => (
                <CapabilityRow
                  key={entry.name}
                  entry={entry}
                  checked={held.has(entry.name)}
                  disabled={false}
                  onToggle={() => setHeld((current) => toggled(current, entry.name))}
                />
              ))}
            </div>
          ))}
        </div>
      </div>
    </Modal>
  )
}

function RemoveRoleModal({
  role, onClose, onRemoved,
}: {
  role: Role
  onClose: () => void
  onRemoved: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      await api.remove(role.id)
      onRemoved()
    } catch (err) {
      // The server refuses while anybody holds it, and names them. Shown
      // verbatim: "still assigned to Sara and Ali" is the sentence somebody
      // can act on, and this screen has no better one.
      setError(err instanceof Error ? err.message : 'Could not delete this role.')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`Delete “${role.name}”?`}
      onClose={onClose}
      width={440}
      footer={
        <>
          <GhostButton onClick={onClose}>Cancel</GhostButton>
          <DangerButton disabled={busy} onClick={submit}>
            {busy ? <Spinner /> : <Icon.Trash size={14} />}
            Delete role
          </DangerButton>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {error && <ErrorNote>{error}</ErrorNote>}
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.55 }}>
          Nobody holds this role, so nobody loses access. This cannot be undone.
        </p>
      </div>
    </Modal>
  )
}

// ── set helpers ───────────────────────────────────────────────────────────
function toggled(current: Set<string>, value: string): Set<string> {
  const next = new Set(current)
  if (next.has(value)) next.delete(value)
  else next.add(value)
  return next
}

function sameSet(a: Set<string>, b: Set<string>): boolean {
  return a.size === b.size && [...a].every((value) => b.has(value))
}
