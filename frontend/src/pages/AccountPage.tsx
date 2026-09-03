/**
 * Your own account: the name the rail shows, and the password you sign in with.
 *
 * This screen exists because every route under `/users` is admin-only. A
 * member invited with a one-time password an administrator generated — and
 * can still read — had no way to change it, so every invited account stayed
 * on that password indefinitely. Two self-scoped routes (`PATCH /auth/me`,
 * `PUT /auth/me/password`) are the way out, and this is their surface.
 *
 * What is *not* here is as deliberate as what is. Email, role and status are
 * an administrator's to set: they are the account's identity in a workspace
 * rather than its owner's preferences, and the schema behind `PATCH /auth/me`
 * cannot express them at all — a member cannot promote themselves by editing
 * a payload, because there is no field to edit. They are shown, read-only,
 * because "what am I here?" is a fair question to ask of an account screen.
 *
 * The address is `/settings`, free since routing moved the model providers to
 * `/providers`, where they always belonged. It is reached from the user block
 * in the rail — the place the name being changed is already displayed.
 */
import { useCallback, useState } from 'react'
import { auth } from '../api/client'
import type { User } from '../api/types'
import { useUnsavedWork } from '../shell'
import {
  Chip, ErrorNote, Field, PageHeader, PrimaryButton, Spinner, TextInput,
} from '../components/ui'
import { FieldRow, Section, StatusLine, UnsavedNote } from '../components/settings'

/** The floor the API enforces, stated here so the form can say it first. */
const MIN_PASSWORD = 8

export default function AccountPage({
  user, onUserChange,
}: {
  user: User
  /** The rail shows this name; a rename that only reached the server would
   *  leave the sidebar disagreeing with the form that just saved it. */
  onUserChange: (user: User) => void
}) {
  return (
    <div className="rm-index rm-page-pad" style={{ flex: 1, overflowY: 'auto' }}>
      <PageHeader
        title="Your account"
        subtitle="The name people see, and the password you sign in with."
      />
      <div
        style={{
          maxWidth: 720,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        <ProfileCard user={user} onUserChange={onUserChange} />
        <PasswordCard />
      </div>
    </div>
  )
}

/**
 * A labelled value you cannot change, drawn as a value rather than as a
 * disabled control.
 *
 * `<TextInput disabled />` was the first version and it was worse: this app's
 * inputs carry their colours inline, so a disabled one keeps the border, the
 * panel fill and the text colour of an editable field and reads as a box you
 * are allowed to type in. A field that looks live and refuses the keyboard is
 * a bug report waiting to be filed. No box, no border — a stated fact.
 */
function Stated({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        minHeight: 36,
        fontSize: 13,
        color: 'var(--text2)',
      }}
    >
      {children}
    </div>
  )
}

function ProfileCard({
  user, onUserChange,
}: {
  user: User
  onUserChange: (user: User) => void
}) {
  const [name, setName] = useState(user.display_name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const trimmed = name.trim()
  const changed = trimmed !== user.display_name
  const releaseUnsaved = useUnsavedWork(
    'account-profile',
    changed ? 'Your new display name has not been saved.' : null,
  )

  const save = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await auth.updateProfile(trimmed)
      // Before anything renders the new name: this form is the registrant,
      // and it is about to go clean by having its saved value change under it.
      releaseUnsaved()
      onUserChange(updated)
      setName(updated.display_name)
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save your name.')
    } finally {
      setSaving(false)
    }
  }, [trimmed, onUserChange, releaseUnsaved])

  return (
    <Section
      title="Profile"
      description="Your display name is what the sidebar and every audit entry show."
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      {saved && !changed && <StatusLine ok>Saved.</StatusLine>}

      <Field label="Display name">
        <TextInput
          value={name}
          maxLength={200}
          onChange={(event) => {
            setName(event.target.value)
            setSaved(false)
          }}
        />
      </Field>

      <FieldRow>
        <Field
          label="Email"
          hint="Your sign-in address. An administrator changes this."
        >
          <Stated>{user.email}</Stated>
        </Field>
        <Field label="Role" hint="Set by an administrator.">
          <Stated>
            <Chip tone={user.role === 'ADMIN' ? 'accent' : 'neutral'}>
              {user.role === 'ADMIN' ? 'Administrator' : 'Member'}
            </Chip>
          </Stated>
        </Field>
      </FieldRow>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <PrimaryButton
          onClick={save}
          disabled={saving || !changed || trimmed.length === 0}
          title={
            trimmed.length === 0
              ? 'A display name cannot be blank.'
              : changed
                ? undefined
                : 'No changes to save.'
          }
        >
          {saving && <Spinner />}
          Save name
        </PrimaryButton>
        {changed && <UnsavedNote />}
      </div>
    </Section>
  )
}

/**
 * The password form.
 *
 * `current` is what makes this route safe to expose to a member: proof of
 * possession, so a borrowed screen cannot lock the owner out of their own
 * account. The confirmation field is checked here rather than at the API,
 * because "you typed two different things" is a statement about this form and
 * the server has no way to know it was meant to be two attempts at one value.
 */
function PasswordCard() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const tooShort = next.length > 0 && next.length < MIN_PASSWORD
  const mismatch = confirm.length > 0 && confirm !== next
  const ready =
    current.length > 0 && next.length >= MIN_PASSWORD && confirm === next

  const releaseUnsaved = useUnsavedWork(
    'account-password',
    current || next || confirm
      ? 'You have started changing your password but have not saved it.'
      : null,
  )

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await auth.changePassword(current, next)
      releaseUnsaved()
      setCurrent('')
      setNext('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change your password.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section
      title="Password"
      description="Changing it signs out every other device. This one stays signed in."
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      {done && (
        <StatusLine ok>
          Your password was changed. Every other session has been signed out.
        </StatusLine>
      )}

      <Field label="Current password">
        <TextInput
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(event) => {
            setCurrent(event.target.value)
            setDone(false)
          }}
        />
      </Field>

      <FieldRow>
        <Field
          label="New password"
          hint={`At least ${MIN_PASSWORD} characters.`}
          status={
            tooShort ? <Chip tone="amber">Too short</Chip> : undefined
          }
        >
          <TextInput
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => {
              setNext(event.target.value)
              setDone(false)
            }}
          />
        </Field>
        <Field
          label="Repeat new password"
          status={mismatch ? <Chip tone="amber">Does not match</Chip> : undefined}
        >
          <TextInput
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => {
              setConfirm(event.target.value)
              setDone(false)
            }}
          />
        </Field>
      </FieldRow>

      <PrimaryButton
        onClick={save}
        disabled={saving || !ready}
        style={{ alignSelf: 'flex-start' }}
      >
        {saving && <Spinner />}
        Change password
      </PrimaryButton>
    </Section>
  )
}
