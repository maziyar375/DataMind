/**
 * Reading and writing provider parameters in a form that nobody wrote.
 *
 * The advanced-parameter section of the LLM providers page is **generated**
 * from the catalog the server sends (`llmConfigs.parameters()`), which is what
 * makes adding a parameter one line in
 * `app/domain/value_objects/llm_params.py` and no frontend change at all. That
 * only works if the translation between "what somebody typed" and "the JSON
 * value the provider's API takes" is driven by the spec rather than by a
 * branch per parameter — which is this file.
 *
 * Three rules it exists to keep:
 *
 * * **Blank means unset, and unset means absent.** Never `null`, never `0`,
 *   never `""`. A parameter that is in the map is one the provider will be
 *   sent; a request must not carry a `seed` of nothing.
 * * **The server is the authority, and this is a courtesy.** Every value is
 *   re-validated on save against the same catalog, so the checks here exist to
 *   fail *earlier and next to the field*, not to be the guard. Where the two
 *   disagree the server wins and its sentence is what the page shows.
 * * **Round-trip exactly.** `format(parse(x))` must give a form value that
 *   parses to the same thing, or opening a saved configuration and pressing
 *   Save would silently rewrite it.
 *
 * DOM-free, like the other logic modules here — `npm run test:params`.
 */

/** Mirrors `ParamSpec.as_dict()` in the backend catalog. Structural rather
 *  than imported from `api/types` so this file stays free of that module's
 *  import graph, the way `knowledge-template.ts` does it. */
export interface ParamSpec {
  name: string
  kind: 'number' | 'integer' | 'boolean' | 'string' | 'string_list' | 'object'
  summary: string
  example: string
  minimum?: number
  maximum?: number
  choices?: string[]
  object_keys?: string[]
}

/** Only the parameters that are **present on the form** — added, whether or
 *  not a value has been typed yet. Sparse on purpose: the editor renders one
 *  row per key here, so a configuration that sets nothing renders nothing,
 *  and the fourteen inputs OpenAI-compatible would otherwise always show are
 *  behind an *Add parameter* picker instead. */
export type ParamDrafts = Record<string, string>

export interface ParseOk {
  ok: true
  /** Absent when the field is blank — the parameter is simply not sent. */
  value?: unknown
}

export interface ParseError {
  ok: false
  error: string
}

export type ParseResult = ParseOk | ParseError

/** Whether a parameter of this kind is edited as free text or as a picker.
 *
 *  Read by the form to choose an input, so a new `choices` entry in the
 *  catalog becomes a `<select>` with no code change. */
export function fieldKind(spec: ParamSpec): 'select' | 'number' | 'textarea' | 'text' {
  if (spec.kind === 'boolean' || (spec.choices && spec.choices.length > 0)) {
    return 'select'
  }
  if (spec.kind === 'number' || spec.kind === 'integer') return 'number'
  if (spec.kind === 'object' || spec.kind === 'string_list') return 'textarea'
  return 'text'
}

/** The options a picker offers, blank first. Blank is *unset* and has to be
 *  reachable, or a parameter could be turned on and never off again. */
export function selectOptions(spec: ParamSpec): { value: string; label: string }[] {
  const blank = { value: '', label: 'Not set' }
  if (spec.kind === 'boolean') {
    return [blank, { value: 'true', label: 'true' }, { value: 'false', label: 'false' }]
  }
  return [blank, ...(spec.choices ?? []).map((value) => ({ value, label: value }))]
}

/** One typed value out of one form field. */
export function parseParam(spec: ParamSpec, raw: string): ParseResult {
  const text = raw.trim()
  if (text === '') return { ok: true }

  if (spec.kind === 'boolean') {
    if (text !== 'true' && text !== 'false') {
      return { ok: false, error: 'Must be true or false.' }
    }
    return { ok: true, value: text === 'true' }
  }

  if (spec.kind === 'number' || spec.kind === 'integer') {
    const value = Number(text)
    if (!Number.isFinite(value)) return { ok: false, error: 'Must be a number.' }
    if (spec.kind === 'integer' && !Number.isInteger(value)) {
      return { ok: false, error: 'Must be a whole number.' }
    }
    if (spec.minimum !== undefined && value < spec.minimum) {
      return { ok: false, error: `Must be at least ${spec.minimum}.` }
    }
    if (spec.maximum !== undefined && value > spec.maximum) {
      return { ok: false, error: `Must be at most ${spec.maximum}.` }
    }
    return { ok: true, value }
  }

  if (spec.kind === 'string') {
    if (spec.choices && spec.choices.length > 0 && !spec.choices.includes(text)) {
      return { ok: false, error: `Must be one of: ${spec.choices.join(', ')}.` }
    }
    return { ok: true, value: text }
  }

  if (spec.kind === 'string_list') {
    // A single sequence is the common case and both provider APIs accept a
    // bare string, so `END` is as valid to type as `["END"]`. Only text that
    // *looks* like JSON is parsed as JSON, or a stop sequence containing a
    // bracket would become a syntax error instead of a stop sequence.
    if (!text.startsWith('[')) return { ok: true, value: [text] }
    // Everything below is the JSON form, which is also what `formatParam`
    // writes — see the note there about why a stored value never comes back
    // as bare text.
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      return { ok: false, error: 'Not valid JSON. A list, or one plain value.' }
    }
    if (!Array.isArray(parsed) || !parsed.every((v) => typeof v === 'string')) {
      return { ok: false, error: 'Must be a list of text values.' }
    }
    if (parsed.length === 0) {
      return { ok: false, error: 'Empty — clear the field instead.' }
    }
    return { ok: true, value: parsed }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'Not valid JSON.' }
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { ok: false, error: 'Must be a JSON object.' }
  }
  if (Object.keys(parsed).length === 0) {
    return { ok: false, error: 'Empty — clear the field instead.' }
  }
  if (spec.object_keys && spec.object_keys.length > 0) {
    const unknown = Object.keys(parsed).filter(
      (key) => !spec.object_keys!.includes(key),
    )
    if (unknown.length > 0) {
      return {
        ok: false,
        error: `Takes only ${spec.object_keys.join(', ')}; got ${unknown.join(', ')}.`,
      }
    }
  }
  return { ok: true, value: parsed }
}

/** The form value for a stored parameter — `parseParam`'s inverse. */
export function formatParam(spec: ParamSpec, value: unknown): string {
  if (value === undefined || value === null) return ''
  if (spec.kind === 'boolean') return value ? 'true' : 'false'
  if (spec.kind === 'number' || spec.kind === 'integer') return String(value)
  if (spec.kind === 'string') return String(value)
  if (spec.kind === 'string_list') {
    // **Always** the JSON form, even for a single value that could be written
    // plainly. Typing `END` is still accepted — `parseParam` takes either —
    // but a *stored* value has to come back in a form that survives the trip.
    //
    // The case that decided it is the ordinary one: `stop` is very often
    // `"\n\n\n"`, and rendered as bare text that is a textarea which looks
    // empty and whose content `parseParam` then trims to nothing. The value
    // silently disappeared, and the form reported "Unsaved changes" on a row
    // nobody had touched. JSON shows the escapes, so a whitespace sequence is
    // visible, editable, and identical after a round-trip.
    return JSON.stringify(value)
  }
  return JSON.stringify(value)
}

/** Every stored parameter as form text, keyed by name.
 *
 *  Only the ones actually stored: an unset parameter has no row on the form
 *  until somebody adds it. Parameters the catalog does not describe are
 *  dropped — they cannot be edited, and the provider would refuse them on the
 *  next save anyway. */
export function draftsFrom(
  specs: ParamSpec[],
  stored: Record<string, unknown> | undefined,
): ParamDrafts {
  const drafts: ParamDrafts = {}
  const values = stored ?? {}
  for (const spec of specs) {
    if (spec.name in values) drafts[spec.name] = formatParam(spec, values[spec.name])
  }
  return drafts
}

/** The parameters this provider documents that are not on the form yet — what
 *  the *Add parameter* picker offers, in catalog order so the ones people
 *  reach for first stay first. */
export function addable(specs: ParamSpec[], drafts: ParamDrafts): ParamSpec[] {
  return specs.filter((spec) => !(spec.name in drafts))
}

/** The specs for what is on the form, in catalog order. Ordered by the catalog
 *  rather than by insertion, so a form does not reshuffle as it is edited. */
export function shown(specs: ParamSpec[], drafts: ParamDrafts): ParamSpec[] {
  return specs.filter((spec) => spec.name in drafts)
}

export interface Collected {
  params: Record<string, unknown>
  /** Field name → the sentence under it. Empty when everything parsed. */
  errors: Record<string, string>
}

/** The map to send, plus whatever would not parse.
 *
 *  A field in error contributes **nothing** rather than its last good value:
 *  saving half of what is on the screen is worse than refusing to save. */
export function collectParams(specs: ParamSpec[], drafts: ParamDrafts): Collected {
  const params: Record<string, unknown> = {}
  const errors: Record<string, string> = {}
  // Driven by the catalog, not by the draft keys: a name the provider does not
  // document has no spec to parse against and must not reach the request — the
  // server would refuse the whole save for it, which is a worse way to find out
  // than it simply never being addable.
  for (const spec of shown(specs, drafts)) {
    const result = parseParam(spec, drafts[spec.name] ?? '')
    if (!result.ok) {
      errors[spec.name] = result.error
      continue
    }
    if (result.value !== undefined) params[spec.name] = result.value
  }
  return { params, errors }
}

/** Deterministic JSON, with every object's keys in sorted order.
 *
 *  Needed because the two sides of a comparison come from different places:
 *  the form builds its map in **catalog** order, and the server returns it in
 *  whatever order Postgres reads a `jsonb` back in — which is by key length,
 *  then bytewise. A plain `JSON.stringify` comparison therefore reported a
 *  freshly-loaded configuration as edited, and the page said "Unsaved
 *  changes" over a form nobody had touched. */
function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  const entries = Object.entries(value as Record<string, unknown>).sort(
    ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
  )
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(',')}}`
}

/** Whether two parameter maps say the same thing, whatever order they say it
 *  in. The only honest "has this form been edited?" for a JSON column. */
export function sameParams(
  a: Record<string, unknown> | undefined,
  b: Record<string, unknown> | undefined,
): boolean {
  return canonical(a ?? {}) === canonical(b ?? {})
}

/** How many parameters are set, for a section heading that says so without
 *  making somebody expand it to find out. */
export function configuredCount(drafts: ParamDrafts): number {
  return Object.values(drafts).filter((value) => value.trim() !== '').length
}

/** What a probe found out about the configured parameters, in one sentence.
 *
 *  `dropped` is the interesting half and the reason the endpoint reports it:
 *  an unsupported parameter is dropped **silently** on a real request, so a
 *  configuration can otherwise claim a behaviour it never has. Naming the
 *  model rather than the provider is deliberate — support is per model
 *  (`reasoning_effort` is real on a reasoning model and nothing on a small
 *  one), and "your provider ignores this" would send somebody to the wrong
 *  screen. */
export function parameterVerdict(
  model: string,
  applied: Record<string, unknown> | undefined,
  dropped: string[] | undefined,
): string {
  const kept = Object.keys(applied ?? {}).length
  const lost = dropped ?? []
  if (kept === 0 && lost.length === 0) return ''
  if (lost.length === 0) {
    return `${kept} ${kept === 1 ? 'parameter' : 'parameters'} sent to ${model}.`
  }
  const names = lost.join(', ')
  const verb = lost.length === 1 ? 'is not a parameter' : 'are not parameters'
  return (
    `${kept} of ${kept + lost.length} parameters sent — ${names} ${verb} ` +
    `${model} accepts, so ${lost.length === 1 ? 'it is' : 'they are'} dropped.`
  )
}
