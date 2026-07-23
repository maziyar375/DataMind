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
  light: {
    bg: 'oklch(0.98 0.005 250)',
    'sidebar-bg': 'oklch(0.96 0.006 250)',
    panel: 'oklch(1 0 0)',
    'panel-hover': 'oklch(0.94 0.006 250)',
    'panel-alt': 'oklch(0.95 0.006 250)',
    border: 'oklch(0.88 0.006 250)',
    'border-strong': 'oklch(0.8 0.008 250)',
    'input-bg': 'oklch(1 0 0)',
    'code-bg': 'oklch(0.96 0.006 250)',
    'code-text': 'oklch(0.4 0.07 160)',
    text: 'oklch(0.22 0.01 250)',
    'text-strong': 'oklch(0.12 0.01 250)',
    text2: 'oklch(0.32 0.01 250)',
    'text-dim': 'oklch(0.48 0.012 250)',
    'text-faint': 'oklch(0.6 0.012 250)',
    accent: 'oklch(0.55 0.16 250)',
    'accent-bg': 'oklch(0.55 0.16 250 / 0.1)',
    'accent-border': 'oklch(0.55 0.16 250 / 0.35)',
    'on-accent': 'oklch(0.98 0.005 250)',
    green: 'oklch(0.55 0.15 160)',
    'green-bg': 'oklch(0.55 0.15 160 / 0.12)',
    amber: 'oklch(0.6 0.16 80)',
    'amber-bg': 'oklch(0.6 0.16 80 / 0.12)',
    'amber-border': 'oklch(0.6 0.16 80 / 0.35)',
    red: 'oklch(0.55 0.19 25)',
    'red-bg': 'oklch(0.55 0.19 25 / 0.12)',
    'red-border': 'oklch(0.55 0.19 25 / 0.35)',
  },
}

export function applyTheme(name: ThemeName): void {
  const root = document.documentElement
  for (const [key, value] of Object.entries(THEMES[name])) {
    root.style.setProperty(`--${key}`, value)
  }
  root.style.colorScheme = name
}

/** The pipeline steps, in the order the backend runs them. */
export const NODE_META: Record<string, { label: string; detail: string }> = {
  route: { label: 'Route', detail: 'Classifying question type…' },
  clarify: { label: 'Clarify', detail: 'Checking for ambiguity…' },
  retrieve: { label: 'Retrieve', detail: 'Searching schema for relevant tables…' },
  generate: { label: 'Generate SQL', detail: 'Drafting query…' },
  validate: { label: 'Validate', detail: 'Checking against schema with SQLGlot…' },
  execute: { label: 'Execute', detail: 'Running on read-only connection…' },
  present: { label: 'Present', detail: 'Writing summary and chart spec…' },
}

export const PROVIDER_URLS: Record<string, string> = {
  'OpenAI-compatible': 'https://api.openai.com/v1',
  Anthropic: 'https://api.anthropic.com',
  Ollama: 'http://localhost:11434/v1',
  Custom: 'https://your-endpoint.example.com/v1',
}
