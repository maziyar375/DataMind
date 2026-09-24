/**
 * A connection's deep budget — the five numbers on its Policy tab, with no DOM.
 *
 * `GET`/`PUT /connections/{id}/deep-budget` (docs/plans/deep-analysis-mode.md
 * Phase 8). The server is the boundary: it refuses a number above the
 * installation's ceiling and a deadline too short to finish a step. This
 * module says the same thing *before* Save, in the same words, so a person is
 * told beside the field rather than by a 422 after it — and it never decides
 * anything the server would not.
 */

import type { DeepBudget, DeepLimits } from '../api/types.ts'

export type { DeepBudget, DeepLimits }

export type DeepField = keyof DeepLimits

/** The five, in the order the server tells them, with the words a form needs. */
export const DEEP_FIELDS: { key: DeepField; label: string; hint: string }[] = [
  { key: 'max_steps', label: 'Steps', hint: 'Sub-questions in one plan.' },
  { key: 'max_queries', label: 'Queries', hint: 'Statements run, repairs included.' },
  { key: 'max_rows_total', label: 'Rows', hint: 'Rows read across every step.' },
  { key: 'max_prompt_tokens', label: 'Prompt tokens', hint: 'Sent to the model, all calls.' },
  { key: 'deadline_seconds', label: 'Time (seconds)', hint: 'Then it answers from what it has.' },
]

const WORDS: Record<string, string> = {
  max_steps: 'steps',
  max_queries: 'queries',
  max_rows_total: 'rows',
  max_prompt_tokens: 'prompt tokens',
  deadline_seconds: 'time',
}

/**
 * What is wrong with each field, keyed by field; empty when it may be saved.
 *
 * A zero is **not** a problem — it is how an administrator switches deep
 * analysis off on one connection, and the server refuses a run on it rather
 * than starting a smaller one. `refusal` says so separately.
 */
export function budgetProblems(
  draft: Record<DeepField, string | number>,
  ceiling: DeepLimits,
): Partial<Record<DeepField, string>> {
  const out: Partial<Record<DeepField, string>> = {}
  for (const { key } of DEEP_FIELDS) {
    const raw = String(draft[key]).trim()
    if (!/^\d+$/.test(raw)) {
      out[key] = 'A whole number, 0 or more.'
      continue
    }
    const value = Number(raw)
    if (value > ceiling[key]) {
      out[key] = `At most ${ceiling[key].toLocaleString('en-US')} on this installation.`
    } else if (key === 'deadline_seconds' && value > 0 && value < 60) {
      out[key] = '0, or at least 60 — a shorter one cannot finish a step.'
    }
  }
  return out
}

/** The draft as numbers, once `budgetProblems` is empty. */
export function toLimits(draft: Record<DeepField, string | number>): DeepLimits {
  return Object.fromEntries(
    DEEP_FIELDS.map(({ key }) => [key, Number(String(draft[key]).trim())]),
  ) as unknown as DeepLimits
}

/** Whether the draft differs from what is stored — the Save button's question. */
export function budgetChanged(
  draft: Record<DeepField, string | number>,
  stored: DeepLimits,
): boolean {
  return DEEP_FIELDS.some(({ key }) => String(draft[key]).trim() !== String(stored[key]))
}

/**
 * The first bound at zero, in the server's order — which is the one a refused
 * question will name. Null when a deep run could start.
 */
export function refusal(limits: DeepLimits): DeepField | null {
  for (const { key } of DEEP_FIELDS) if (limits[key] <= 0) return key
  return null
}

/** One sentence under the section: what a deep question here will meet. */
export function budgetSentence(budget: DeepBudget, draft?: DeepLimits): string {
  const limits = draft ?? budget.effective
  const off = draft ? refusal(draft) : (budget.refused as DeepField | null)
  if (off === ('unreadable' as DeepField)) {
    return 'The stored budget cannot be read, so deep analysis is refused here until it is saved again.'
  }
  if (off) {
    return `Deep analysis is off on this data source: it allows no ${WORDS[off] ?? off}. A deep question is refused, not answered smaller.`
  }
  const minutes = Math.round((limits.deadline_seconds / 60) * 10) / 10
  const what = `Up to ${limits.max_steps} steps and ${limits.max_queries} queries, within ${minutes} minute${minutes === 1 ? '' : 's'}.`
  return budget.is_default && !draft
    ? `${what} These are the installation's limits; nothing narrower is set here.`
    : what
}
