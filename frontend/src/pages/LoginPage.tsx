/**
 * Login: the only screen an unauthenticated visitor can reach.
 *
 * A centred card over `AuthScene` — two dotted wave sheets in the bottom
 * corners that part around the pointer, and the product's own instruments
 * (charts, a result table, a database) drifting down both margins. The scene
 * is decorative and says so: every layer is `aria-hidden`, none of it takes a
 * pointer event, and the form works identically with the canvas dead.
 *
 * Five things worth keeping:
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
 *  - **The tagline rotates, and can be stopped.** Three sentences, one at a
 *    time, each true of the product. Clicking a mark stops the rotation for
 *    good — auto-advancing prose with no way to hold it still is a WCAG 2.2.2
 *    failure, and someone who reached for the control wants to read.
 *  - **The mark's float, glow and drop shadow are load-bearing.** They are the
 *    one piece of this screen that predates the redesign and they survived it
 *    intact; `.rm-auth-logo` in `styles.css` still owns all three.
 */
import { useEffect, useState } from 'react'
import { ApiError, auth } from '../api/client'
import type { User } from '../api/types'
import AuthScene from '../components/auth-scene'
import { ErrorNote, Icon, Logo, Spinner, TextInput } from '../components/ui'

/**
 * What the product does, in three sentences that are each true.
 *
 * Drawn from the README's own claims rather than written fresh: this is the
 * first page anyone sees, and a promise made here has to hold on the inside.
 */
const TAGLINES = [
  'Ask in plain language — get an answer, a table, and auditable SQL.',
  'Every query is checked before it runs, and you decide what reaches the model.',
  'PostgreSQL, MySQL, SQL Server or Oracle — the question is the same.',
]
const ROTATE_MS = 6500

export default function LoginPage({
  onSignedIn, onAbout,
}: {
  onSignedIn: (user: User) => void
  /** The signed-out way to About — this screen is the only one there is. */
  onAbout?: () => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [line, setLine] = useState(0)
  /* Set the moment a mark is clicked, and never unset: the visitor has taken
     the wheel and the page does not take it back. */
  const [held, setHeld] = useState(false)

  useEffect(() => {
    if (held) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const id = setInterval(() => setLine((i) => (i + 1) % TAGLINES.length), ROTATE_MS)
    return () => clearInterval(id)
  }, [held])

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
    <div className="rm-auth">
      <AuthScene />

      <div className="rm-enter rm-auth-col">
        <div className="rm-auth-head">
          {/* The crown holds still so the rings do; the logo inside it floats. */}
          <div className="rm-auth-crown">
            <OrbitRings />
            <div className="rm-auth-logo">
              <Logo size={180} />
            </div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <h1
              style={{
                margin: 0,
                fontSize: 'clamp(27px, 3.2vw, 34px)',
                fontWeight: 700,
                letterSpacing: '-0.028em',
                lineHeight: 1.15,
                color: 'var(--text-strong)',
                textWrap: 'balance',
              }}
            >
              Welcome to DataMind
            </h1>
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

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label htmlFor="email" style={labelStyle}>Email</label>
            <div className="rm-auth-field">
              <span className="rm-auth-field-icon"><Icon.Mail size={15} /></span>
              <TextInput
                id="email"
                type="email"
                autoComplete="username"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                style={fieldStyle}
              />
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label htmlFor="password" style={labelStyle}>Password</label>
            <div className="rm-auth-field">
              <span className="rm-auth-field-icon"><Icon.Lock size={15} /></span>
              <TextInput
                id="password"
                type={reveal ? 'text' : 'password'}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{ ...fieldStyle, paddingRight: 40 }}
              />
              {/* Not a toggle switch: it says what pressing it will do, which is
                  why the label and the glyph both flip with the state. */}
              <button
                type="button"
                className="rm-auth-reveal"
                onClick={() => setReveal((on) => !on)}
                aria-pressed={reveal}
                aria-label={reveal ? 'Hide password' : 'Show password'}
                title={reveal ? 'Hide password' : 'Show password'}
              >
                {reveal ? <Icon.EyeOff size={15} /> : <Icon.Eye size={15} />}
              </button>
            </div>
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
              border: 'none',
              borderRadius: 11,
              padding: 13,
              cursor: 'pointer',
            }}
          >
            {busy && <Spinner />}
            {busy ? 'Signing in' : 'Sign in'}
            {!busy && <Icon.Chevron size={15} stroke="currentColor" />}
          </button>
        </form>

        {/* The tagline and the one link off this screen share a block: the
            sentence says what the product does, and "Who made it?" is the
            question it provokes. A separate footer for one link would weigh
            more than the link does. */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 14,
            textAlign: 'center',
            fontSize: 12.5,
            color: 'var(--auth-ink-3)',
            letterSpacing: '0.01em',
          }}
        >
          <div className="rm-auth-taglines" style={{ lineHeight: 1.55, maxWidth: 340 }}>
            {TAGLINES.map((text, i) => (
              <span
                key={i}
                className={`rm-auth-tagline${i === line ? ' is-on' : ''}`}
                aria-hidden={i !== line}
              >
                {text}
              </span>
            ))}
          </div>

          <div className="rm-auth-marks">
            {TAGLINES.map((text, i) => (
              <button
                key={i}
                type="button"
                className={`rm-auth-mark${i === line ? ' is-on' : ''}`}
                onClick={() => { setLine(i); setHeld(true) }}
                aria-current={i === line}
                aria-label={text}
              />
            ))}
          </div>

          {onAbout && (
            <button type="button" onClick={onAbout} className="rm-auth-about">
              Meet the creators
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  color: 'var(--auth-ink-3)',
  fontWeight: 500,
}

/**
 * Taller than the app's default input, and with the room for the leading glyph
 * declared *here* rather than in `styles.css`: `TextInput` spreads this object
 * onto the element's `style`, and an inline padding wins over any class rule —
 * which is how the mail glyph ended up printed on top of the placeholder.
 */
const fieldStyle: React.CSSProperties = {
  borderRadius: 10,
  padding: '11px 13px 11px 40px',
  fontSize: 14,
}

/**
 * The rings the mark sits at the centre of — and only the rings.
 *
 * Centred by being a child of `.rm-auth-crown` rather than by a magic top
 * offset, so it stays on the mark at every viewport, and its mask dissolves it
 * below the mark's own centre line so no ring is drawn across the heading.
 *
 * **The dots that travel these rings are not here.** They live in the canvas in
 * `auth-scene.tsx`, which measures this element for their centre, radius and
 * weight. They were `<circle>`s inside a turning copy of this SVG, and a
 * turning child inside a masked parent re-rasterises that whole layer every
 * frame — it more than halved the frame rate to move eight dots. Concentric
 * rings look identical turned, so holding them still costs nothing at all.
 */
function OrbitRings() {
  return (
    <svg className="rm-auth-orbit" viewBox="0 0 900 900" aria-hidden="true">
      <circle className="rm-orbit-ring" cx="450" cy="450" r="182" />
      <circle className="rm-orbit-dash" cx="450" cy="450" r="260" />
      <circle className="rm-orbit-ring is-far" cx="450" cy="450" r="324" />
    </svg>
  )
}
