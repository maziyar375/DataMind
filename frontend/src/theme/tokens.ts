/**
 * Design tokens, taken verbatim from the design concept.
 *
 * These oklch values are the source of truth for the product's visual
 * identity — they are not re-derived or "improved" here. Every colour in the
 * app reads from a CSS variable defined below, so a token change propagates
 * everywhere and nothing hardcodes a hex.
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
    accent: 'oklch(0.7 0.15 250)',
    'accent-bg': 'oklch(0.7 0.15 250 / 0.14)',
    'accent-border': 'oklch(0.7 0.15 250 / 0.3)',
    'on-accent': 'oklch(0.14 0.01 250)',
    green: 'oklch(0.75 0.15 160)',
    'green-bg': 'oklch(0.75 0.15 160 / 0.12)',
    amber: 'oklch(0.8 0.15 80)',
    'amber-bg': 'oklch(0.8 0.15 80 / 0.1)',
    'amber-border': 'oklch(0.8 0.15 80 / 0.35)',
    red: 'oklch(0.68 0.19 25)',
    'red-bg': 'oklch(0.68 0.19 25 / 0.1)',
    'red-border': 'oklch(0.68 0.19 25 / 0.35)',
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
    accent: 'oklch(0.52 0.19 315)',
    'accent-bg': 'oklch(0.52 0.19 315 / 0.1)',
    'accent-border': 'oklch(0.52 0.19 315 / 0.35)',
    'on-accent': 'oklch(0.99 0.004 320)',
    green: 'oklch(0.54 0.15 155)',
    'green-bg': 'oklch(0.54 0.15 155 / 0.12)',
    amber: 'oklch(0.62 0.16 66)',
    'amber-bg': 'oklch(0.62 0.16 66 / 0.13)',
    'amber-border': 'oklch(0.62 0.16 66 / 0.35)',
    red: 'oklch(0.55 0.2 25)',
    'red-bg': 'oklch(0.55 0.2 25 / 0.12)',
    'red-border': 'oklch(0.55 0.2 25 / 0.35)',
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
  clarify: { label: 'Clarify', detail: 'Checking for ambiguity…' },
  retrieve: { label: 'Retrieve', detail: 'Searching schema for relevant tables…' },
  generate: { label: 'Generate SQL', detail: 'Drafting query…' },
  validate: { label: 'Validate', detail: 'Checking against schema with SQLGlot…' },
  execute: { label: 'Execute', detail: 'Running on read-only connection…' },
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

export const PROVIDER_URLS: Record<string, string> = {
  'OpenAI-compatible': 'https://api.openai.com/v1',
  Anthropic: 'https://api.anthropic.com',
  Ollama: 'http://localhost:11434/v1',
  Custom: 'https://your-endpoint.example.com/v1',
}
