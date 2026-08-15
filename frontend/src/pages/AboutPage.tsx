/**
 * About: who made this, and what they were trying to make.
 *
 * It is reachable from two places on purpose, because it answers a question
 * asked from two sides of the sign-in wall. A signed-in user asks "who is
 * behind the tool my company's data goes through?" — so it sits in the rail's
 * footer group, beside the theme switch and the account, where the chrome that
 * is *about the product* lives rather than in the list of destinations, which
 * is the work in the order it is done. Someone who has not signed in asks the
 * same question with more at stake and has exactly one screen to ask it from —
 * so the login page links here too, and this page then draws its own
 * signed-out surface (`rm-auth`, the same neon field) instead of a shell it is
 * not inside.
 *
 * `onBack` is what tells the two apart: given, this is the standalone route
 * and needs a way home; absent, the rail is already on screen and a second
 * back affordance would be furniture.
 *
 * The one thing here that is not text: the portraits are read from
 * `/team/*.jpg` and fall back to an initial in a tinted circle if the file is
 * missing — the same bargain `Logo` makes with `/brand.png`. A page that
 * shows a broken-image glyph next to a person's name is worse than one that
 * shows their initial.
 */
import { useState } from 'react'
import { GlyphBadge, Icon, identityHue, initialOf } from '../components/ui'

type Person = {
  name: string
  role: string
  photo: string
  email: string
  linkedin: string
  /** One line, in their own right — not a job description. */
  blurb: string
}

const TEAM: Person[] = [
  {
    name: 'Maziyar Azami',
    role: 'Co-founder',
    photo: '/team/maziyar.jpg',
    email: 'maziyar.azami.b@gmail.com',
    linkedin: 'https://www.linkedin.com/in/maziyar-azami-aab545246',
    blurb:
      'Works on the pipeline and the guard — how a question becomes SQL, and '
      + 'what has to be true before that SQL is allowed to run.',
  },
  {
    name: 'Bardia Azami',
    role: 'Co-founder',
    photo: '/team/bardia.jpg',
    email: 'Bard.azami@gmail.com',
    linkedin: 'https://www.linkedin.com/in/bardia-azami-a24579258',
    blurb:
      'Works on the product and the interface — dashboards, reports, and the '
      + 'step trail that shows an answer being built rather than just arriving.',
  },
]

/** What the product refuses to do, which is the part worth saying out loud. */
const PRINCIPLES: { icon: React.ReactNode; title: string; body: string }[] = [
  {
    icon: <Icon.Shield size={16} />,
    title: 'The model proposes, it never executes',
    body:
      'Every generated statement is parsed and walked against an allowlist '
      + 'before it reaches your database. An unknown construct is a rejection, '
      + 'not a warning.',
  },
  {
    icon: <Icon.Lock size={16} />,
    title: 'Read-only, and proven so',
    body:
      'Queries run in a read-only transaction under a role that is checked for '
      + 'write access by trying — with a statement timeout and a row cap on '
      + 'top of it.',
  },
  {
    icon: <Icon.Doc size={16} />,
    title: 'You can always see the query',
    body:
      'Every answer shows the SQL that produced it and the steps it went '
      + 'through. An answer you cannot audit is a rumour with a chart on it.',
  },
]

export default function AboutPage({ onBack }: { onBack?: () => void }) {
  const standalone = onBack != null
  return (
    <div
      className={standalone ? 'rm-auth' : undefined}
      style={{
        flex: 1,
        minWidth: 0,
        overflowY: 'auto',
        ...(standalone
          ? {
            height: '100vh',
            width: '100%',
            background: 'var(--bg)',
            color: 'var(--text)',
            fontFamily: 'Inter, system-ui, sans-serif',
          }
          : null),
      }}
    >
      <div className="rm-page-pad rm-about">
        {standalone && (
          <button type="button" onClick={onBack} className="rm-about-back">
            <Icon.ArrowLeft size={14} />
            Back to sign in
          </button>
        )}

        <header className="rm-about-hero">
          <h1>Two people, and one question that kept coming back</h1>
          <p>
            The numbers a business runs on already exist — they are sitting in a
            database, one join away from the person who needs them. What is
            missing is not the data. It is the twenty minutes, the ticket, and
            the analyst who has to write the query.
          </p>
          <p>
            DataMind is our answer to that: ask in plain language, and get a
            written answer, a table, a chart, and{' '}
            <strong>the SQL that produced them</strong>. It speaks PostgreSQL,
            MySQL, SQL Server and Oracle, it never writes, and it shows its
            work — because a number you cannot check is not an answer, it is a
            claim.
          </p>
        </header>

        <section aria-labelledby="rm-about-team-h">
          <h2 id="rm-about-team-h" className="rm-about-h2">The team</h2>
          <div className="rm-about-team">
            {TEAM.map((person) => (
              <PersonCard key={person.email} person={person} />
            ))}
          </div>
        </section>

        <section aria-labelledby="rm-about-build-h">
          <h2 id="rm-about-build-h" className="rm-about-h2">How we build it</h2>
          <div className="rm-about-principles">
            {PRINCIPLES.map((principle) => (
              <div key={principle.title} className="rm-about-principle">
                <span className="rm-about-principle-icon" aria-hidden>
                  {principle.icon}
                </span>
                <div>
                  <div className="rm-about-principle-title">{principle.title}</div>
                  <p>{principle.body}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        <footer className="rm-about-foot">
          Built by two people who wanted to stop asking someone else for a
          number.
        </footer>
      </div>
    </div>
  )
}

function PersonCard({ person }: { person: Person }) {
  return (
    <article className="rm-about-card">
      <Portrait name={person.name} src={person.photo} />
      <div className="rm-about-name">{person.name}</div>
      <div className="rm-about-role">{person.role}</div>
      <p className="rm-about-blurb">{person.blurb}</p>
      <div className="rm-about-links">
        {/* The address is the label. A "Contact" button hides the one fact
            someone came to this card for, and cannot be copied by eye. */}
        <a href={`mailto:${person.email}`} className="rm-about-link">
          <Icon.Mail size={13} />
          <span>{person.email}</span>
        </a>
        <a
          href={person.linkedin}
          target="_blank"
          rel="noreferrer noopener"
          className="rm-about-link"
        >
          <LinkedInMark />
          <span>LinkedIn</span>
        </a>
      </div>
    </article>
  )
}

function Portrait({ name, src }: { name: string; src: string }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    // Sized and rounded to match the photo exactly, so a missing file changes
    // what the card shows and not how it is laid out.
    return (
      <div className="rm-about-portrait">
        <GlyphBadge hue={identityHue(name)} size={112} radius={56}>
          {initialOf(name)}
        </GlyphBadge>
      </div>
    )
  }
  return (
    <img
      src={src}
      alt={name}
      width={112}
      height={112}
      loading="lazy"
      onError={() => setFailed(true)}
      className="rm-about-portrait rm-about-photo"
    />
  )
}

/**
 * LinkedIn's mark, drawn locally rather than added to `Icon`.
 *
 * Everything in that set is a 24px stroked outline on one grid, and this is a
 * filled logotype belonging to someone else — putting it in the same object
 * would invite the next brand mark in after it, and the set would stop being
 * a system. `currentColor` so it inherits the link's colour and hover like the
 * stroked icon beside it.
 */
function LinkedInMark({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
      style={{ flexShrink: 0 }}
    >
      <path d="M4.98 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM3 9.75h4v10.75H3V9.75zm6.5 0h3.83v1.47h.05a4.2 4.2 0 0 1 3.78-2.08c4.04 0 4.79 2.66 4.79 6.12v5.24h-4v-4.65c0-1.11-.02-2.54-1.55-2.54-1.55 0-1.79 1.21-1.79 2.46v4.73h-4V9.75z" />
    </svg>
  )
}
