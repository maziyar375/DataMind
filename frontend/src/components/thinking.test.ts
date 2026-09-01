/**
 * The thinking indicator's arithmetic.
 *
 * `npm run test:thinking` — Node runs this directly (type stripping, no
 * bundler, no test framework), the same bargain the other suites make.
 *
 * What is being protected: a reader looking at a run that has produced no text
 * yet must be able to tell it apart from a run that has died. Every case below
 * is a way that could stop being true — a clock that reads zero, a label that
 * runs backwards, a buffer that grows without a ceiling, or reasoning leaking
 * into the answer's own state.
 */
import {
  REASONING_TAIL_CHARS, absorbThought, endThought, thoughtTime,
} from './thinking.ts'
import type { ThinkingState } from './thinking.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}\n        want ${JSON.stringify(expected)}`,
  )
}

// ── the clock ──────────────────────────────────────────────────────────────
check('a fresh thought reads as zero, not as blank', thoughtTime(0), '0s')
check('sub-second still shows a number', thoughtTime(400), '0s')
check('seconds round to the nearest', thoughtTime(1600), '2s')
check('under a minute stays in seconds', thoughtTime(47_000), '47s')

// The seven-minute `describe` this was built for. Minutes matter here: "420s"
// is a number a reader has to convert before it means anything.
check('a minute becomes minutes', thoughtTime(60_000), '1m 00s')
check('seconds are padded so the width does not jump', thoughtTime(64_000), '1m 04s')
check('the real case reads as time', thoughtTime(420_818), '7m 01s')

// A negative can only come from clock skew between the two measurements, and
// "-3s" on screen would be worse than the wrong number.
check('a negative elapsed cannot be rendered', thoughtTime(-5000), '0s')

// ── accumulating ───────────────────────────────────────────────────────────
check(
  'the first delta starts the state',
  absorbThought(null, 'The user wants ', 120),
  { text: 'The user wants ', ms: 120, done: false } satisfies ThinkingState,
)

check(
  'later deltas append in order',
  absorbThought(
    { text: 'The user wants ', ms: 120, done: false },
    'an orientation.',
    900,
  ),
  { text: 'The user wants an orientation.', ms: 900, done: false },
)

// The server measures from the first thought to the latest one and is the
// authority; the panel's own clock only fills the gaps between events.
check(
  'the server\'s elapsed replaces the previous one',
  absorbThought({ text: 'a', ms: 5000, done: false }, 'b', 400).ms,
  400,
)
check(
  'an event with no elapsed keeps the last known one',
  absorbThought({ text: 'a', ms: 5000, done: false }, 'b', undefined).ms,
  5000,
)

// ── the ceiling ────────────────────────────────────────────────────────────
{
  const long = 'x'.repeat(REASONING_TAIL_CHARS + 500)
  const state = absorbThought(null, long, 1000)
  check('a long thought is capped', state.text.length, REASONING_TAIL_CHARS)
}
{
  // The head goes, not the tail: what the reader is watching is the newest
  // line, and dropping that would freeze the panel while the model kept going.
  const prev = { text: 'HEAD' + 'x'.repeat(REASONING_TAIL_CHARS), ms: 1, done: false }
  const state = absorbThought(prev, 'TAIL', 2)
  check('the newest text is what survives', state.text.endsWith('TAIL'), true)
  check('and the oldest is what goes', state.text.startsWith('HEAD'), false)
  check('the cap holds across appends', state.text.length, REASONING_TAIL_CHARS)
}

// ── ending ─────────────────────────────────────────────────────────────────
check(
  'the answer starting stops the clock and keeps the text',
  endThought({ text: 'mulling', ms: 47_000, done: false }),
  { text: 'mulling', ms: 47_000, done: true },
)
check(
  'a model that never thought has nothing to end',
  endThought(null),
  null,
)
check(
  'ending twice is the same as ending once',
  endThought(endThought({ text: 'mulling', ms: 47_000, done: false })),
  { text: 'mulling', ms: 47_000, done: true },
)

// A thought that has ended is still a thought: a late delta on the same run
// would reopen it, which is correct — the model went back to thinking.
check(
  'a delta after the end reopens it',
  absorbThought({ text: 'a', ms: 10, done: true }, 'b', 20).done,
  false,
)

console.log(failures === 0 ? '\nall passed' : `\n${failures} failed`)
if (failures > 0) throw new Error(`${failures} thinking check(s) failed`)
