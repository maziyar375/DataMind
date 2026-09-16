/**
 * What scoring a semantic layer draft says, and whether it says it about the
 * same thing the published score does.
 *
 * `npm run test:score` — DOM-free, like `semantic-changes.ts`, because the
 * failure here is quiet: a delta printed between two runs that used different
 * prompts or models is a number that looks like evidence and is not.
 *
 * The publish dialog offers *Score this draft*, which queues a benchmark run
 * that reads the draft (`semantic_source = DRAFT`, pinned to the layer's
 * revision). This module reads that run beside the newest run of the published
 * layer on the same set and returns one of five states. **A delta is given only
 * when the two are comparable** — same prompt version, same provider and model,
 * both with held-out questions scored — and otherwise the reason there is none.
 * The set is the same by construction: both runs come from one set's rows.
 *
 * Advisory, never a gate: a benchmark is only as representative as its curator
 * made it, and a gate would block publishing the fix for a metric that is
 * wrong today.
 */

/** The fields of a benchmark run this reads — `BenchmarkRun` from the API. */
export interface RunLike {
  status: string
  prompt_version: string
  model_snapshot: Record<string, unknown>
  held_out_total: number
  held_out_matched: number
  semantic_source?: string
  semantic_revision?: number | null
  error_message?: string
}

export interface SetLike {
  /** Published runs, newest first. */
  runs: RunLike[]
  draft_run: RunLike | null
}

export type DraftScore =
  /** No run of this draft. `earlier` when the last one scored an older draft. */
  | { state: 'none'; earlier: boolean }
  | { state: 'running' }
  | { state: 'failed'; message: string }
  | {
      state: 'scored'
      /** Held-out accuracy as a whole percentage, or `null` with none scored. */
      draft: number | null
      published: number | null
      /** Percentage points, draft minus published; `null` unless comparable. */
      delta: number | null
      /** Why there is no delta. `''` when there is one. */
      reason: string
    }

const ACTIVE = new Set(['QUEUED', 'RUNNING'])

/** A whole percentage, or `null` for an empty denominator — never 0%. */
export function percent(matched: number, total: number): number | null {
  return total > 0 ? Math.round((100 * matched) / total) : null
}

/** "openai/gpt-4o-mini", or `''` when the snapshot names nothing. */
export function modelName(snapshot: Record<string, unknown> | null | undefined): string {
  const model = typeof snapshot?.model === 'string' ? snapshot.model : ''
  const provider = typeof snapshot?.provider === 'string' ? snapshot.provider : ''
  return model ? (provider ? `${provider}/${model}` : model) : ''
}

export function draftScore(set: SetLike, revision: number): DraftScore {
  const run = set.draft_run
  if (!run) return { state: 'none', earlier: false }
  if (run.semantic_revision !== revision) return { state: 'none', earlier: true }
  if (ACTIVE.has(run.status)) return { state: 'running' }
  if (run.status !== 'SUCCEEDED') {
    return { state: 'failed', message: run.error_message || 'The run did not finish.' }
  }

  const draft = percent(run.held_out_matched, run.held_out_total)
  const baseline = set.runs.find((r) => r.status === 'SUCCEEDED') ?? null
  const published = baseline ? percent(baseline.held_out_matched, baseline.held_out_total) : null
  const scored = (delta: number | null, reason: string): DraftScore => ({
    state: 'scored', draft, published, delta, reason,
  })

  if (!baseline) {
    return scored(null, 'There is no finished run of the published layer on this set to compare with.')
  }
  if (baseline.prompt_version !== run.prompt_version) {
    return scored(
      null,
      `The published run used prompts ${baseline.prompt_version || 'of an unknown version'} and this one ${run.prompt_version}, so they are not the same measurement.`,
    )
  }
  const was = modelName(baseline.model_snapshot)
  const now = modelName(run.model_snapshot)
  if (was !== now) {
    return scored(
      null,
      `The published run was answered by ${was || 'an unrecorded model'} and this one by ${now || 'an unrecorded model'}.`,
    )
  }
  if (draft === null || published === null) {
    return scored(null, 'One of the two runs scored no held-out question, so there is no accuracy to compare.')
  }
  return scored(draft - published, '')
}

/** "+4 points", "−3 points", "no change" — the sign is a glyph, not a colour. */
export function deltaWords(delta: number): string {
  if (delta === 0) return 'no change'
  const size = Math.abs(delta)
  return `${delta > 0 ? '+' : '−'}${size} ${size === 1 ? 'point' : 'points'}`
}
