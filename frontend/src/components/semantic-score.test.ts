/**
 * A draft's score, and when it may be compared with the published one.
 *
 * `npm run test:score` — Node runs this file directly, the arrangement
 * `semantic-changes.test.ts` uses. The case that matters most is the one that
 * prints nothing: two runs that are not the same measurement get a reason, not
 * a delta.
 */
import { deltaWords, draftScore, modelName, percent } from './semantic-score.ts'
import type { RunLike } from './semantic-score.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n      expected ${JSON.stringify(expected)}\n      got      ${JSON.stringify(actual)}`,
  )
}

const GPT = { provider: 'openai', model: 'gpt-4o-mini' }

function run(extra: Partial<RunLike> = {}): RunLike {
  return {
    status: 'SUCCEEDED', prompt_version: 'v10', model_snapshot: GPT,
    held_out_total: 25, held_out_matched: 15,
    semantic_source: 'PUBLISHED', semantic_revision: null, ...extra,
  }
}

const draft = (extra: Partial<RunLike> = {}) =>
  run({ semantic_source: 'DRAFT', semantic_revision: 7, held_out_matched: 16, ...extra })

// ── percentages ───────────────────────────────────────────────────────────
check('a percentage is whole', percent(2, 3), 67)
check('an empty denominator is no percentage, never 0%', percent(0, 0), null)
check('a model is named with its provider', modelName(GPT), 'openai/gpt-4o-mini')
check('and an empty snapshot names nothing', modelName({}), '')

// ── the states ────────────────────────────────────────────────────────────
check('no draft run at all',
      draftScore({ runs: [run()], draft_run: null }, 7), { state: 'none', earlier: false })
check('a run of an earlier draft is not this draft\'s score',
      draftScore({ runs: [run()], draft_run: draft({ semantic_revision: 6 }) }, 7),
      { state: 'none', earlier: true })
check('a queued run is running',
      draftScore({ runs: [run()], draft_run: draft({ status: 'QUEUED' }) }, 7).state, 'running')
check('a failed run carries its message',
      draftScore({
        runs: [run()],
        draft_run: draft({ status: 'FAILED', error_message: 'The draft changed after this run was queued.' }),
      }, 7),
      { state: 'failed', message: 'The draft changed after this run was queued.' })

// ── comparable ────────────────────────────────────────────────────────────
check('same prompts, same model: a delta in points',
      draftScore({ runs: [run()], draft_run: draft({ held_out_matched: 16 }) }, 7),
      { state: 'scored', draft: 64, published: 60, delta: 4, reason: '' })
check('the published side is the newest run that finished',
      draftScore({
        runs: [run({ status: 'FAILED' }), run({ held_out_matched: 10 })],
        draft_run: draft({ held_out_matched: 10 }),
      }, 7),
      { state: 'scored', draft: 40, published: 40, delta: 0, reason: '' })

// ── not comparable: a reason, and no delta ────────────────────────────────
const different = (published: RunLike[], scoredDraft: RunLike) => {
  const score = draftScore({ runs: published, draft_run: scoredDraft }, 7)
  return score.state === 'scored' ? { delta: score.delta, hasReason: score.reason.length > 0 } : score
}
check('different prompt versions are not compared',
      different([run({ prompt_version: 'v9' })], draft()), { delta: null, hasReason: true })
check('different models are not compared',
      different([run({ model_snapshot: { provider: 'deepseek', model: 'v4-flash' } })], draft()),
      { delta: null, hasReason: true })
check('no published run to compare with',
      different([], draft()), { delta: null, hasReason: true })
check('a run with no held-out question has no accuracy to compare',
      different([run({ held_out_total: 0, held_out_matched: 0 })], draft()),
      { delta: null, hasReason: true })
check('the reason names both prompt versions',
      (draftScore({ runs: [run({ prompt_version: 'v9' })], draft_run: draft() }, 7) as { reason: string }).reason
        .includes('v9 and this one v10'),
      true)

// ── words ─────────────────────────────────────────────────────────────────
check('a gain', deltaWords(4), '+4 points')
check('a loss uses a real minus sign', deltaWords(-1), '−1 point')
check('nothing moved', deltaWords(0), 'no change')

// A throw rather than `process.exit`: the same non-zero exit for npm, and no
// `@types/node` for a file the app's own tsconfig type-checks.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
