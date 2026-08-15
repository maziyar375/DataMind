/**
 * About: the two people who build this application.
 *
 * It is about them and nothing else. Every other screen in the product is
 * already an argument for the product — this one is a page of names, faces,
 * and two ways to reach each of them, and anything else added here (what the
 * tool does, why it was built, what it refuses to do) turns a colophon into a
 * sales page and buries the four facts someone actually came for.
 *
 * It is reachable from two places, because the question is asked from both
 * sides of the sign-in wall: the rail's footer group when signed in, and the
 * login screen — the only public surface there is — when not. `onBack` is
 * what tells the two apart. Given, this is the standalone route and needs a
 * way home; absent, the rail is already on screen and a second back
 * affordance would be furniture.
 *
 * The portraits are read from `public/team/<slug>.<ext>` — any of the four
 * extensions below — and fall back to an initial in a tinted circle when
 * there is no such file, the same bargain `Logo` makes with `/brand.png`. A
 * broken-image glyph beside a person's name is worse than their initial.
 *
 * The look is in `styles.css` under "creators" and is written twice, once per
 * theme, for reasons stated there. Only two things need saying here: the page
 * brings its own ambient instead of borrowing the login screen's, so it is
 * one page in both places; and the card highlight follows the pointer through
 * a CSS variable this file writes directly to the DOM — see `trackPointer`.
 */
import { useState } from 'react'
import { GlyphBadge, Icon, identityHue, initialOf } from '../components/ui'

type Person = {
  name: string
  /** The basename of the portrait in `public/team/`, extension aside. */
  slug: string
  email: string
  linkedin: string
}

const TEAM: Person[] = [
  {
    name: 'Maziyar Azami',
    slug: 'maziyar',
    email: 'maziyar.azami.b@gmail.com',
    linkedin: 'https://www.linkedin.com/in/maziyar-azami-aab545246',
  },
  {
    name: 'Bardia Azami',
    slug: 'bardia',
    email: 'Bard.azami@gmail.com',
    linkedin: 'https://www.linkedin.com/in/bardia-azami-a24579258',
  },
]

/**
 * Tried in order until one decodes, so dropping a portrait into
 * `public/team/` never means also editing this file to match its extension.
 *
 * A missing static asset is not a 404 here — the dev server and the SPA
 * fallback both answer an unknown path with `index.html` — so the signal that
 * a candidate is wrong is the decode failing, not the request. Which is the
 * same `onError` either way, and why this is a list rather than a fetch.
 */
const PHOTO_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']

export default function AboutPage({ onBack }: { onBack?: () => void }) {
  const standalone = onBack != null
  return (
    <div
      className="rm-about-page"
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
          <h1>Meet the creators</h1>
          <p>The two people who built this application.</p>
        </header>

        <div className="rm-about-team">
          {TEAM.map((person, index) => (
            <PersonCard
              key={person.email}
              person={person}
              // The heading rises first and the cards follow it in order, so
              // the page assembles rather than appearing. `both` on the
              // animation holds the opening frame until each card's turn.
              delay={0.08 + index * 0.09}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

/**
 * Where the pointer is, in the card's own coordinates.
 *
 * Written straight onto the element as custom properties rather than held in
 * state: this fires on every pointer move, and a re-render per frame to move
 * a background gradient is the kind of thing that makes a page feel worse the
 * more it is decorated. React never learns about it, and never needs to — the
 * value is read by CSS and by nothing else.
 */
function trackPointer(event: React.PointerEvent<HTMLElement>) {
  const card = event.currentTarget
  const box = card.getBoundingClientRect()
  card.style.setProperty('--mx', `${event.clientX - box.left}px`)
  card.style.setProperty('--my', `${event.clientY - box.top}px`)
}

function PersonCard({ person, delay }: { person: Person; delay: number }) {
  return (
    <article
      className="rm-about-card"
      onPointerMove={trackPointer}
      style={{ animationDelay: `${delay}s` }}
    >
      <Portrait name={person.name} slug={person.slug} />
      <div className="rm-about-name">{person.name}</div>
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

function Portrait({ name, slug }: { name: string; slug: string }) {
  const [attempt, setAttempt] = useState(0)
  const extension = PHOTO_EXTENSIONS[attempt]
  return (
    <div className="rm-about-portrait">
      {extension == null ? (
        // Sized and rounded to match the photo exactly, so a missing file
        // changes what the card shows and not how it is laid out.
        <GlyphBadge hue={identityHue(name)} size={128} radius={64}>
          {initialOf(name)}
        </GlyphBadge>
      ) : (
        <img
          // Keyed on the candidate so a failed one is unmounted rather than
          // re-pointed: without this the browser keeps the broken element's
          // error state and only the first extension is ever tried.
          key={extension}
          src={`/team/${slug}.${extension}`}
          alt={name}
          width={128}
          height={128}
          onError={() => setAttempt((current) => current + 1)}
          className="rm-about-photo"
        />
      )}
    </div>
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
