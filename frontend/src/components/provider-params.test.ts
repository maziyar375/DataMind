/**
 * The generated parameter form, and the three ways it could quietly lie.
 *
 * `npm run test:params` — Node runs this file directly, the arrangement the
 * other logic suites here use.
 *
 * What is being defended:
 *
 * * **blank is absent**, so a request never carries a `seed` of nothing;
 * * **round-trip**, so opening a saved configuration and pressing Save does
 *   not rewrite what somebody stored;
 * * **a field in error contributes nothing**, so a refused save is refused
 *   whole rather than half-applied.
 */
import {
  addable, collectParams, configuredCount, draftsFrom, fieldKind, formatParam,
  parameterVerdict, parseParam, sameParams, selectOptions, shown,
} from './provider-params.ts'
import type { ParamSpec } from './provider-params.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`,
  )
}

const TOP_P: ParamSpec = {
  name: 'top_p', kind: 'number', summary: '', example: '0.9',
  minimum: 0, maximum: 1,
}
const SEED: ParamSpec = { name: 'seed', kind: 'integer', summary: '', example: '42' }
const STORE: ParamSpec = { name: 'store', kind: 'boolean', summary: '', example: 'false' }
const TIER: ParamSpec = {
  name: 'service_tier', kind: 'string', summary: '', example: 'auto',
  choices: ['auto', 'default', 'flex'],
}
const USER: ParamSpec = { name: 'user', kind: 'string', summary: '', example: 'u' }
const STOP: ParamSpec = {
  name: 'stop', kind: 'string_list', summary: '', example: '["\\n\\n"]',
}
const THINKING: ParamSpec = {
  name: 'thinking', kind: 'object', summary: '', example: '{}',
  object_keys: ['type', 'budget_tokens'],
}
const EXTRA: ParamSpec = { name: 'extra_body', kind: 'object', summary: '', example: '{}' }

// ── blank is absent ──────────────────────────────────────────────────────
check('a blank field sets nothing', parseParam(TOP_P, ''), { ok: true })
check('whitespace is blank too', parseParam(SEED, '   '), { ok: true })
check(
  'a field added but not yet filled in is left out of the map entirely',
  collectParams([TOP_P, SEED], { top_p: '0.9', seed: '' }),
  { params: { top_p: 0.9 }, errors: {} },
)
check(
  'nothing on the form is an empty map, not a map of nulls',
  collectParams([TOP_P, SEED, STORE], {}),
  { params: {}, errors: {} },
)
check('unset is reachable on a picker', selectOptions(STORE)[0], {
  value: '', label: 'Not set',
})

// ── each kind, and its bounds ────────────────────────────────────────────
check('a number in range', parseParam(TOP_P, '0.9'), { ok: true, value: 0.9 })
check('above the maximum', parseParam(TOP_P, '1.5'), {
  ok: false, error: 'Must be at most 1.',
})
check('below the minimum', parseParam(TOP_P, '-1'), {
  ok: false, error: 'Must be at least 0.',
})
check('not a number at all', parseParam(TOP_P, 'high'), {
  ok: false, error: 'Must be a number.',
})
check('an integer parameter refuses a fraction', parseParam(SEED, '1.5'), {
  ok: false, error: 'Must be a whole number.',
})
check('a boolean', parseParam(STORE, 'true'), { ok: true, value: true })
check('a documented value', parseParam(TIER, 'flex'), { ok: true, value: 'flex' })
check('a value the provider does not document', parseParam(TIER, 'express'), {
  ok: false, error: 'Must be one of: auto, default, flex.',
})

// ── the two shapes a text input cannot express ───────────────────────────
check(
  'one stop sequence may be typed plainly',
  parseParam(STOP, 'END'),
  { ok: true, value: ['END'] },
)
check(
  'several are JSON',
  parseParam(STOP, '["a", "b"]'),
  { ok: true, value: ['a', 'b'] },
)
check('a broken list says so', parseParam(STOP, '["a"'), {
  ok: false, error: 'Not valid JSON. A list, or one plain value.',
})
check(
  'an object parameter takes only the documented keys',
  parseParam(THINKING, '{"budget": 2048}'),
  { ok: false, error: 'Takes only type, budget_tokens; got budget.' },
)
check(
  'the documented keys pass',
  parseParam(THINKING, '{"type": "enabled", "budget_tokens": 2048}'),
  { ok: true, value: { type: 'enabled', budget_tokens: 2048 } },
)
check(
  'a passthrough object is not second-guessed',
  parseParam(EXTRA, '{"top_k": 40, "anything": true}'),
  { ok: true, value: { top_k: 40, anything: true } },
)
check('an array is not an object', parseParam(EXTRA, '[1]'), {
  ok: false, error: 'Must be a JSON object.',
})
check('an empty object is a cleared field, not a value', parseParam(EXTRA, '{}'), {
  ok: false, error: 'Empty — clear the field instead.',
})

// ── round-trip ───────────────────────────────────────────────────────────
for (const [spec, value] of [
  [TOP_P, 0.9], [SEED, 42], [STORE, false], [TIER, 'auto'], [USER, 'team'],
  [STOP, ['END']], [STOP, ['a', 'b']],
  [THINKING, { type: 'enabled', budget_tokens: 2048 }],
  [EXTRA, { top_k: 40 }],
] as [ParamSpec, unknown][]) {
  check(
    `${spec.name} survives a round-trip through the form`,
    parseParam(spec, formatParam(spec, value)),
    { ok: true, value },
  )
}
check(
  'a single stop sequence that looks like JSON keeps its brackets',
  parseParam(STOP, formatParam(STOP, ['[literal]'])),
  { ok: true, value: ['[literal]'] },
)
// The case that made the form report unsaved changes on a row nobody had
// touched: a stop sequence of newlines rendered as bare text is a textarea
// that looks empty, and an empty field means *unset*. Shown as JSON it
// survives, which is why `formatParam` never writes the bare form.
check(
  'a whitespace stop sequence is visible rather than blank',
  formatParam(STOP, ['\n\n\n']),
  '["\\n\\n\\n"]',
)
check(
  'and it round-trips instead of disappearing',
  parseParam(STOP, formatParam(STOP, ['\n\n\n'])),
  { ok: true, value: ['\n\n\n'] },
)

// ── hydrating from a stored configuration ────────────────────────────────
check(
  'a stored configuration puts a row on the form only for what it set',
  draftsFrom([TOP_P, SEED, STOP], { top_p: 0.9, stop: ['END'] }),
  { top_p: '0.9', stop: '["END"]' },
)
check(
  'a configuration with nothing stored renders no rows at all',
  draftsFrom([TOP_P, SEED], undefined),
  {},
)
check(
  'a parameter the catalog no longer describes is dropped rather than shown',
  draftsFrom([TOP_P], { top_p: 0.9, retired_param: 1 }),
  { top_p: '0.9' },
)
check('the count is what is set', configuredCount({ a: '1', b: '', c: '  ' }), 1)

// ── the picker ───────────────────────────────────────────────────────────
// Fourteen always-visible inputs for OpenAI-compatible was too many, and
// nearly all of them blank nearly always. The form now renders what is on it
// and offers the rest.
check(
  'the picker offers what is not on the form, in catalog order',
  addable([TOP_P, SEED, STORE], { seed: '7' }).map((s) => s.name),
  ['top_p', 'store'],
)
check(
  'and offers nothing once everything is added',
  addable([TOP_P], { top_p: '' }).map((s) => s.name),
  [],
)
check(
  'the rows follow the catalog, not the order they were added',
  shown([TOP_P, SEED, STORE], { store: '', top_p: '0.9' }).map((s) => s.name),
  ['top_p', 'store'],
)
check(
  'a draft key the catalog does not describe never reaches the request',
  collectParams([TOP_P], { top_p: '0.9', smuggled: 'x' }),
  { params: { top_p: 0.9 }, errors: {} },
)

// ── a field in error contributes nothing ─────────────────────────────────
check(
  'one bad field does not take its neighbours down, and does not sneak through',
  collectParams([TOP_P, SEED], { top_p: '5', seed: '7' }),
  { params: { seed: 7 }, errors: { top_p: 'Must be at most 1.' } },
)

// ── comparing what is on the form with what is stored ────────────────────
// The form builds its map in catalog order; Postgres reads a jsonb back by
// key length then bytewise. Comparing the two as plain strings reported every
// freshly-loaded configuration as edited.
check(
  'key order is not a change',
  sameParams({ top_p: 0.5, seed: 7 }, { seed: 7, top_p: 0.5 }),
  true,
)
check(
  'nor is it a change nested inside a value',
  sameParams({ thinking: { type: 'enabled', budget_tokens: 1 } },
             { thinking: { budget_tokens: 1, type: 'enabled' } }),
  true,
)
check('an absent map and an empty one are the same', sameParams(undefined, {}), true)
check('a different value is a change', sameParams({ seed: 7 }, { seed: 8 }), false)
check('an added parameter is a change', sameParams({ seed: 7 }, {}), false)
check('list order *is* a change', sameParams({ stop: ['a', 'b'] }, { stop: ['b', 'a'] }), false)

// ── what a probe reports back ────────────────────────────────────────────
check('nothing configured says nothing', parameterVerdict('gpt-4o-mini', {}, []), '')
check(
  'everything accepted',
  parameterVerdict('gpt-4o-mini', { seed: 7 }, []),
  '1 parameter sent to gpt-4o-mini.',
)
check(
  'a dropped parameter is named, and so is the model that dropped it',
  parameterVerdict('gpt-4o-mini', { seed: 7 }, ['reasoning_effort']),
  '1 of 2 parameters sent — reasoning_effort is not a parameter gpt-4o-mini '
  + 'accepts, so it is dropped.',
)
check(
  'two dropped read as two',
  parameterVerdict('m', { seed: 7 }, ['a', 'b']),
  '1 of 3 parameters sent — a, b are not parameters m accepts, so they are dropped.',
)

// ── the form picks its own inputs from the spec ──────────────────────────
check('a bounded number is a number input', fieldKind(TOP_P), 'number')
check('a documented value list is a picker', fieldKind(TIER), 'select')
check('a boolean is a picker', fieldKind(STORE), 'select')
check('an object is a textarea', fieldKind(THINKING), 'textarea')
check('a list is a textarea', fieldKind(STOP), 'textarea')
check('plain text is a text input', fieldKind(USER), 'text')

console.log(failures === 0 ? '\nall provider-param checks passed' : `\n${failures} failed`)
// A thrown error is the non-zero exit; `process` would need Node's types.
if (failures > 0) throw new Error(`${failures} provider-param checks failed`)
