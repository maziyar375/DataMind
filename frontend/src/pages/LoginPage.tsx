/**
 * Login: the only screen an unauthenticated visitor can reach.
 *
 * A single centred card over the `.rm-auth` ambient in `styles.css` — the
 * brand's neon orbs and the grid motif, written once there and echoed by the
 * Creators page rather than borrowed from this file.
 *
 * Three things worth keeping:
 *
 *  - **Email is the identifier, not a username.** One concept, and it maps
 *    cleanly onto an OIDC `email` claim the day this grows a second sign-in
 *    method; carrying both would guarantee a migration later.
 *  - **"Wrong password" and "the API is down" are different sentences.** An
 *    `ApiError` carries the server's own message and is rendered verbatim;
 *    anything else means the request never arrived, and says so. Collapsing
 *    the two sends someone hunting for a typo in a password while the backend
 *    is not running.
 *  - **About is reachable from here.** It is the one page that exists on both
 *    sides of the sign-in wall, and this is the only public surface there is,
 *    so `onAbout` is what the shell passes to make the link appear. Absent, it
 *    does not render.
 */
import { useState } from 'react'
import { ApiError, auth } from '../api/client'
import type { User } from '../api/types'
import { ErrorNote, Icon, Logo, Spinner, TextInput } from '../components/ui'

export default function LoginPage({
  onSignedIn, onAbout,
}: {
  onSignedIn: (user: User) => void
  /** The signed-out way to About — this screen is the only one there is. */
  onAbout?: () => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await auth.login(email.trim(), password))
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Could not reach the server. Check that the API is running.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="rm-auth"
      style={{
        minHeight: '100vh',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        padding: 24,
      }}
    >
      <div
        className="rm-enter"
        style={{
          width: '100%',
          maxWidth: 392,
          display: 'flex',
          flexDirection: 'column',
          gap: 26,
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 22,
          }}
        >
          <div className="rm-auth-logo">
            <Logo size={176} />
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-0.025em' }}>
              Welcome to DataMind
            </div>
            <div
              style={{
                fontSize: 14.5,
                color: 'var(--text-dim)',
                marginTop: 8,
                lineHeight: 1.5,
              }}
            >
              Sign in to your conversational BI workspace
            </div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="rm-auth-card"
          style={{
            padding: 26,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          {error && <ErrorNote>{error}</ErrorNote>}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <label
              htmlFor="email"
              style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 500 }}
            >
              Email
            </label>
            <TextInput
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              style={{ borderRadius: 9, padding: '11px 13px' }}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <label
              htmlFor="password"
              style={{ fontSize: 12, color: 'var(--text-dim)', fontWeight: 500 }}
            >
              Password
            </label>
            <TextInput
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={{ borderRadius: 9, padding: '11px 13px' }}
            />
          </div>

          <button
            type="submit"
            disabled={busy}
            className="rm-auth-submit"
            style={{
              marginTop: 6,
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              fontSize: 14.5,
              fontWeight: 600,
              color: 'var(--on-accent)',
              background:
                'linear-gradient(135deg, color-mix(in oklch, var(--accent) 86%, white), var(--accent))',
              border: 'none',
              borderRadius: 11,
              padding: 13,
              cursor: busy ? 'wait' : 'pointer',
              opacity: busy ? 0.7 : 1,
            }}
          >
            {busy && <Spinner />}
            {busy ? 'Signing in' : 'Sign in'}
            {!busy && <Icon.Chevron size={15} stroke="currentColor" />}
          </button>
        </form>

        {/* The tagline and the one link off this screen share a line: the
            sentence says what the product does, and "Who made it?" is the
            question it provokes. A separate footer block for one link would
            weigh more than the link does. */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 8,
            textAlign: 'center',
            fontSize: 12,
            color: 'var(--text-faint)',
            letterSpacing: '0.01em',
          }}
        >
          <span>Ask in plain language — get an answer, a table, and auditable SQL.</span>
          {onAbout && (
            <button
              type="button"
              onClick={onAbout}
              className="rm-auth-about"
            >
              Meet the creators
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
