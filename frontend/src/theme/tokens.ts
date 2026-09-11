/**
 * Design tokens, taken verbatim from the design concept.
 *
 * These oklch values are the source of truth for the product's visual
 * identity — they are not re-derived or "improved" here. Every colour in the
 * app reads from a CSS variable defined below, so a token change propagates
 * everywhere and nothing hardcodes a hex.
 *
 * Each theme is in two halves. Everything down to `--red-border` is the
 * palette: what colour a thing is, and it is the part that is settled — the
 * light theme's hue choices in particular are the answer to a measured
 * contrast problem and must not be nudged by eye. Everything after the
 * `material` divider is how a surface is *lit*: which things float and how
 * far, where a top edge catches light, what a filled accent control casts,
 * and the one gradient that means a language model is involved. The split is
 * the useful one because the two halves fail differently — a wrong colour is
 * wrong everywhere at once, while a wrong shadow is only wrong on the things
 * that wear it.
 */

export type ThemeName = 'dark' | 'light'

export const THEMES: Record<ThemeName, Record<string, string>> = {
  dark: {
    bg: 'oklch(0.16 0.01 250)',
    'sidebar-bg': 'oklch(0.14 0.01 250)',
    panel: 'oklch(0.2 0.012 250)',
    'panel-hover': 'oklch(0.24 0.014 250)',
    'panel-alt': 'oklch(0.25 0.014 250)',
    border: 'oklch(0.28 0.014 250)',
    'border-strong': 'oklch(0.35 0.014 250)',
    'input-bg': 'oklch(0.21 0.012 250)',
    'code-bg': 'oklch(0.13 0.01 250)',
    'code-text': 'oklch(0.82 0.02 160)',
    text: 'oklch(0.93 0.01 250)',
    'text-strong': 'oklch(0.95 0.01 250)',
    text2: 'oklch(0.85 0.01 250)',
    'text-dim': 'oklch(0.65 0.015 250)',
    'text-faint': 'oklch(0.55 0.015 250)',
    // How a per-record identity hue is *rendered* (`glyphTint` in ui.tsx). The
    // hue itself belongs to the record — a Postgres source is hue 250 in both
    // themes, or the colour stops being a legend — but the lightness it is
    // mixed at belongs to the ground it sits on. These are the values every
    // glyph used to hardcode, so dark is unchanged.
    'glyph-tint-l': '0.7',
    'glyph-tint-c': '0.16',
    'glyph-ink-l': '0.65',
    'glyph-ink-c': '0.17',
    accent: 'oklch(0.7 0.15 250)',
    'accent-bg': 'oklch(0.7 0.15 250 / 0.14)',
    'accent-border': 'oklch(0.7 0.15 250 / 0.3)',
    'on-accent': 'oklch(0.14 0.01 250)',
    green: 'oklch(0.75 0.15 160)',
    'green-bg': 'oklch(0.75 0.15 160 / 0.12)',
    'green-border': 'oklch(0.75 0.15 160 / 0.35)',
    amber: 'oklch(0.8 0.15 80)',
    'amber-bg': 'oklch(0.8 0.15 80 / 0.1)',
    'amber-border': 'oklch(0.8 0.15 80 / 0.35)',
    red: 'oklch(0.68 0.19 25)',
    'red-bg': 'oklch(0.68 0.19 25 / 0.1)',
    'red-border': 'oklch(0.68 0.19 25 / 0.35)',

    // ── material ────────────────────────────────────────────────────────
    // Everything below is *how a surface is lit*, not what colour it is. The
    // hues above stayed exactly as they were; these say which of them float,
    // which catch a top edge, and which one means "a model did this".

    // The second accent, and the only reason it exists: this product has two
    // kinds of coloured thing, and until now they wore the same blue. An
    // accent tint on a nav row means *here*; an accent tint on the model
    // picker means *a language model is involved*. `--ai` pairs the accent
    // with a violet so the second kind reads as its own family, and the rule
    // that keeps it meaningful is that it goes nowhere else: the model
    // picker, the thinking trail, the sparkle badge, the providers page. A
    // gradient used on the fourth thing is a decoration, not a signal.
    'accent-2': 'oklch(0.72 0.16 296)',
    ai: 'linear-gradient(135deg, oklch(0.7 0.15 250), oklch(0.72 0.16 296))',
    'ai-soft':
      'linear-gradient(135deg, oklch(0.7 0.15 250 / 0.16), oklch(0.72 0.16 296 / 0.16))',
    'ai-border': 'oklch(0.71 0.15 272 / 0.4)',

    // The far stop of a filled control, so a primary button is lit from its
    // top edge rather than being one flat slab of accent.
    'accent-deep': 'oklch(0.63 0.16 256)',

    // The top-edge highlight every raised surface carries — the one detail
    // that separates "a box with a border" from "an object with a light on
    // it". Applied as an inset box-shadow, never as a border, so it cannot
    // change an element's box.
    sheen: 'oklch(1 0 0 / 0.09)',
    'sheen-strong': 'oklch(1 0 0 / 0.16)',

    // The inverse of the sheen, and the other half of one idea: a surface is
    // either a **sheet** laid on the page or a **well** cut into it. A sheet
    // catches light along its top edge (`--sheen`); a well is in shadow along
    // its top edge, because the lip above it is between the light and the
    // floor. Fields, the SQL box, code blocks and the tracks that hold a
    // segmented control's pill are wells; cards, bands, chips and the rail
    // are sheets. Kept shallow on purpose — a deep one is a 2008 text field.
    well: 'inset 0 1px 2px 0 oklch(0 0 0 / 0.24)',

    // Three heights, and only things that actually float wear them: menus,
    // modals, drawers, notices, a dashboard tile being dragged. A static
    // card in a column is not floating and gets none of these — a page where
    // everything is raised is a page where nothing is.
    'elev-1': '0 1px 2px oklch(0 0 0 / 0.3), 0 2px 6px -2px oklch(0 0 0 / 0.36)',
    'elev-2': '0 4px 10px -3px oklch(0 0 0 / 0.4), 0 14px 32px -12px oklch(0 0 0 / 0.55)',
    'elev-3': '0 10px 24px -8px oklch(0 0 0 / 0.45), 0 36px 80px -28px oklch(0 0 0 / 0.7)',

    // A filled accent control casts an accent-tinted shadow, because a light
    // that colour would. Both stops carry a real offset and blur: a
    // zero-offset ring of colour is a halo, and a halo is decoration.
    'accent-cast':
      '0 2px 6px -2px oklch(0.7 0.15 250 / 0.45), 0 10px 22px -10px oklch(0.7 0.15 250 / 0.55)',

    // The ambient field behind the whole shell (`.rm-app`). Two very low
    // alphas: strong enough that the ground is lit rather than flat, weak
    // enough that a table sitting on it is unchanged.
    'field-a': 'oklch(0.7 0.15 250 / 0.1)',
    'field-b': 'oklch(0.72 0.16 296 / 0.07)',
  },
  // Light theme reads as warm "paper" rather than cold clinical white: the
  // neutrals sit at a warm hue (~80) with a whisper of chroma, so surfaces feel
  // inviting instead of soulless. The app shell also layers a soft multi-hue
  // wash (see `.rm-app` in styles.css) that only shows through open areas —
  // cards and tables keep solid backgrounds, so legibility is untouched.
  //
  // Accent is a warm plum/orchid (hue ~315) drawn from the logo, not the cold
  // blue: it's the "professional-warm" choice — a jewel tone that stays warm
  // against the paper and, unlike an amber/terracotta accent, doesn't collide
  // with the warning-amber or error-red semantics. Amber lives on as a
  // secondary warm highlight (the welcome glow + sparkle badge), giving a
  // deliberate plum-primary / amber-highlight pairing. Dark theme is unchanged.
  light: {
    bg: 'oklch(0.975 0.009 83)',
    'sidebar-bg': 'oklch(0.96 0.011 80)',
    panel: 'oklch(0.995 0.004 85)',
    'panel-hover': 'oklch(0.95 0.012 80)',
    'panel-alt': 'oklch(0.962 0.011 80)',
    border: 'oklch(0.89 0.012 78)',
    'border-strong': 'oklch(0.8 0.014 75)',
    'input-bg': 'oklch(0.998 0.003 85)',
    'code-bg': 'oklch(0.962 0.012 80)',
    'code-text': 'oklch(0.42 0.1 162)',
    text: 'oklch(0.24 0.013 70)',
    'text-strong': 'oklch(0.15 0.014 68)',
    text2: 'oklch(0.34 0.012 72)',
    'text-dim': 'oklch(0.5 0.013 74)',
    'text-faint': 'oklch(0.62 0.013 76)',
    // Ink on paper, not a sticker on it. A glyph mixed at the dark theme's
    // 0.65 lightness sits at roughly 3:1 against a 0.96 ground — technically
    // legible, visibly washed out, and cold enough beside the warm neutrals to
    // read as a stray pastel. Dropping the ink to 0.46 gives it the weight of
    // the text it labels; the tint behind it darkens to match, so the badge
    // still reads as one object.
    'glyph-tint-l': '0.62',
    'glyph-tint-c': '0.15',
    'glyph-ink-l': '0.46',
    'glyph-ink-c': '0.16',
    accent: 'oklch(0.52 0.19 315)',
    'accent-bg': 'oklch(0.52 0.19 315 / 0.1)',
    'accent-border': 'oklch(0.52 0.19 315 / 0.35)',
    'on-accent': 'oklch(0.99 0.004 320)',
    green: 'oklch(0.54 0.15 155)',
    'green-bg': 'oklch(0.54 0.15 155 / 0.12)',
    'green-border': 'oklch(0.54 0.15 155 / 0.35)',
    amber: 'oklch(0.62 0.16 66)',
    'amber-bg': 'oklch(0.62 0.16 66 / 0.13)',
    'amber-border': 'oklch(0.62 0.16 66 / 0.35)',
    red: 'oklch(0.55 0.2 25)',
    'red-bg': 'oklch(0.55 0.2 25 / 0.12)',
    'red-border': 'oklch(0.55 0.2 25 / 0.35)',

    // ── material ────────────────────────────────────────────────────────
    // The same six jobs as dark, answered for paper. Two of them invert
    // outright, and that is the whole difference: on paper a shadow is a
    // *grey* cast rather than a black one — pure black over a warm ground
    // goes muddy — and the top-edge highlight is near-white at high alpha
    // rather than white at low, because it has to read against a surface
    // that is already bright.

    // The AI pair follows the light theme's own accent round to a
    // blue-violet instead of dark's blue-to-violet, so the gradient keeps
    // its plum identity and still travels far enough to read as two colours.
    'accent-2': 'oklch(0.48 0.2 282)',
    ai: 'linear-gradient(135deg, oklch(0.52 0.19 315), oklch(0.48 0.2 282))',
    'ai-soft':
      'linear-gradient(135deg, oklch(0.52 0.19 315 / 0.12), oklch(0.48 0.2 282 / 0.12))',
    'ai-border': 'oklch(0.5 0.19 298 / 0.4)',

    'accent-deep': 'oklch(0.45 0.2 320)',

    sheen: 'oklch(1 0 0 / 0.7)',
    'sheen-strong': 'oklch(1 0 0 / 0.92)',

    // Shallower still than dark's. On paper the ground under a field is
    // already close to white, so the same shadow that reads as depth on a
    // dark surface reads as a smudge here.
    well: 'inset 0 1px 2px 0 oklch(0.3 0.02 70 / 0.07)',

    // Cast from the neutrals' own warm hue at low alpha. A shadow that is
    // grey where the page is warm reads as dirt on the paper.
    'elev-1': '0 1px 2px oklch(0.3 0.02 70 / 0.05), 0 2px 6px -2px oklch(0.3 0.02 70 / 0.08)',
    'elev-2': '0 4px 10px -3px oklch(0.3 0.02 70 / 0.07), 0 14px 32px -12px oklch(0.3 0.02 70 / 0.13)',
    'elev-3': '0 10px 24px -8px oklch(0.3 0.02 70 / 0.09), 0 36px 80px -28px oklch(0.3 0.02 70 / 0.18)',

    'accent-cast':
      '0 2px 6px -2px oklch(0.52 0.19 315 / 0.3), 0 10px 22px -10px oklch(0.52 0.19 315 / 0.4)',

    // Dark lights its ground from nothing; paper already carries the warm
    // multi-hue wash on `.rm-app`, so these are the two that *drift* over it
    // and are correspondingly fainter still.
    'field-a': 'oklch(0.52 0.19 315 / 0.07)',
    'field-b': 'oklch(0.48 0.2 282 / 0.05)',
  },
}

export function applyTheme(name: ThemeName): void {
  const root = document.documentElement
  for (const [key, value] of Object.entries(THEMES[name])) {
    root.style.setProperty(`--${key}`, value)
  }
  root.style.colorScheme = name
  root.setAttribute('data-theme', name)
}

/** The pipeline steps, in the order the backend runs them. */
export const NODE_META: Record<string, { label: string; detail: string }> = {
  route: { label: 'Route', detail: 'Classifying question type…' },
  match: { label: 'Match', detail: 'Looking for a saved question…' },
  clarify: { label: 'Clarify', detail: 'Checking for ambiguity…' },
  retrieve: { label: 'Retrieve', detail: 'Searching schema for relevant tables…' },
  describe: { label: 'Describe', detail: 'Answering from the schema and semantic layer…' },
  generate: { label: 'Generate SQL', detail: 'Drafting query…' },
  validate: { label: 'Validate', detail: 'Checking against schema with SQLGlot…' },
  execute: { label: 'Execute', detail: 'Running on read-only connection…' },
  inspect: { label: 'Inspect', detail: 'Checking the result for known traps…' },
  present: { label: 'Present', detail: 'Writing the summary…' },
  chart: { label: 'Chart', detail: 'Choosing the best chart for the result…' },
}

/**
 * The database engines DataMind can connect to.
 *
 * `port` is the engine's standard listener, applied when switching type so
 * the form does not keep a port that belongs to a different engine.
 * `databaseLabel` differs because the field does not mean the same thing
 * everywhere: Oracle reaches a database through a listener *service*, not a
 * catalogue name.
 */
export const DATABASE_TYPES: {
  value: string
  label: string
  port: number
  databaseLabel: string
  databaseHint: string
  schemaHint: string
}[] = [
  {
    value: 'postgres',
    label: 'PostgreSQL',
    port: 5432,
    databaseLabel: 'Database',
    databaseHint: '',
    schemaHint: 'Blank means every schema. Usually "public".',
  },
  {
    value: 'mysql',
    label: 'MySQL',
    port: 3306,
    databaseLabel: 'Database',
    databaseHint: '',
    schemaHint: 'MySQL has no separate schema; blank uses the database above.',
  },
  {
    value: 'mssql',
    label: 'SQL Server',
    port: 1433,
    databaseLabel: 'Database',
    databaseHint: '',
    schemaHint: 'Blank means "dbo".',
  },
  {
    value: 'oracle',
    label: 'Oracle',
    port: 1521,
    databaseLabel: 'Service name',
    databaseHint: 'The listener service, e.g. FREEPDB1 or ORCLPDB1 — not a catalogue.',
    schemaHint: 'In Oracle a schema is a user. Blank uses the connecting user.',
  },
]

// Also the source of the provider picker's options — the page maps over these
// keys — so removing an entry removes the choice.
export const PROVIDER_URLS: Record<string, string> = {
  'OpenAI-compatible': 'https://api.openai.com/v1',
  Anthropic: 'https://api.anthropic.com',
}
