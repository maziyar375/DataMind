/**
 * A semantic layer's change list, grouped, ordered and put into words.
 *
 * `npm run test:changes` — DOM-free and framework-free, the arrangement
 * `semantic-metrics.ts` uses: what a curator reads about *why a number moved*
 * is decided here, and a wrong sentence is worse than none.
 *
 * **This file never compares two documents.** The server's differ
 * (`app/semantic/diff.py`) is the only one, and it hands over typed changes
 * keyed by entry — kind, entity, item, whether it changes numbers, and the
 * before and after of the fields that moved. Two differs, one in each
 * language, would be two functions that must agree forever
 * (`docs/plans/semantic-layer-model.md` D5). This file only groups, orders and
 * words what it is given, and says less rather than guess when it is given
 * less (a history row carries the kind and keys, not the values).
 */

/** The fields of one change this reads — `SemanticChange` from the API. */
export interface ChangeLike {
  kind: string
  entity_key: string
  item_key: string
  affects_sql: boolean
  fields?: string[]
  before?: Record<string, unknown>
  after?: Record<string, unknown>
}

/** A run of text, either prose or a name/expression set in code type. */
export interface Segment {
  text: string
  code?: boolean
}

export interface ChangeLine {
  kind: string
  entityKey: string
  itemKey: string
  affectsSql: boolean
  segments: Segment[]
}

export interface ChangeGroup {
  /** Stable React key: `doc`, `glossary`, or the entity key. */
  key: string
  /** `''` for the document and glossary groups. */
  entityKey: string
  title: string
  /** Set in code type: a table name is an identifier, a heading is not. */
  titleIsCode: boolean
  affectsSql: boolean
  lines: ChangeLine[]
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

const FIELD_WORDS: Record<string, string> = {
  label: 'business name',
  description: 'description',
  grain: 'grain',
  role: 'kind',
  default_time_column: 'date column',
  synonyms: 'also called',
  unit: 'unit',
  format: 'format',
  additive: 'rolls up',
  fiscal_year_start_month: 'fiscal year start',
  week_starts_on: 'week start',
  timezone: 'time zone',
  relative_windows: 'relative windows',
  notes: 'notes',
  meaning: 'meaning',
  maps_to: 'maps to',
  business_context: 'description of this database',
  default_exclusions: 'rows left out unless asked for',
}

const code = (text: string): Segment => ({ text, code: true })
const prose = (text: string): Segment => ({ text })

/** A line's words as one string — for tests, titles and screen readers. */
export function plain(segments: Segment[]): string {
  return segments.map((s) => s.text).join('')
}

function quoted(value: unknown, field = ''): string {
  if (field === 'fiscal_year_start_month' && typeof value === 'number') {
    return MONTHS[value - 1] ?? String(value)
  }
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'nothing'
  if (value === '' || value === null || value === undefined) return 'nothing'
  return `“${String(value)}”`
}

/** "grain changed from “a” to “b”", "grain set to “b”", "grain cleared". */
function fieldMoves(change: ChangeLike): string {
  const before = change.before ?? {}
  const after = change.after ?? {}
  return (change.fields ?? [])
    .map((field) => {
      const word = FIELD_WORDS[field] ?? field.replaceAll('_', ' ')
      const was = before[field]
      const now = after[field]
      const empty = (v: unknown) =>
        v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0)
      if (empty(was)) return `${word} set to ${quoted(now, field)}`
      if (empty(now)) return `${word} cleared`
      return `${word} changed from ${quoted(was, field)} to ${quoted(now, field)}`
    })
    .join('; ')
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : []
}

function listDelta(before: string[], after: string[]): { added: string[]; removed: string[] } {
  const was = new Set(before)
  const now = new Set(after)
  return {
    added: after.filter((v) => !was.has(v)),
    removed: before.filter((v) => !now.has(v)),
  }
}

/** `a`, `b` and `c`, as segments. */
function codeList(items: string[]): Segment[] {
  const out: Segment[] = []
  items.forEach((item, i) => {
    if (i > 0) out.push(prose(i === items.length - 1 ? ' and ' : ', '))
    out.push(code(item))
  })
  return out
}

function sentenceCase(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text
}

/**
 * One change, as a sentence meant to sit under its group's heading.
 *
 * The table is the group's title, so a line names the column or metric it is
 * about and not the table again. Where the change carries no detail — a
 * history row — the sentence says what kind of thing changed and stops.
 */
export function describeChange(change: ChangeLike): Segment[] {
  const item = change.item_key
  const before = change.before ?? {}
  const after = change.after ?? {}
  const detail = (change.fields ?? []).length > 0
  switch (change.kind) {
    case 'context_changed':
      return [prose('The description of this database changed')]
    case 'exclusions_changed':
      return [prose(detail
        ? sentenceCase(fieldMoves(change))
        : 'The rows left out unless asked for changed')]
    case 'time_changed':
      return [prose(detail ? `Time conventions: ${fieldMoves(change)}` : 'Time conventions changed')]
    case 'entity_added': {
      const label = String(after.label ?? '')
      return [prose('Added to the layer'), ...(label ? [prose(` as “${label}”`)] : [])]
    }
    case 'entity_removed':
      return [prose('Removed from the layer')]
    case 'entity_excluded':
      return [prose('Hidden from the model')]
    case 'entity_included':
      return [prose('Sent to the model again')]
    case 'entity_described':
      return [prose(detail ? sentenceCase(fieldMoves(change)) : 'Described differently')]
    case 'column_added':
      return [prose('Added column '), code(item)]
    case 'column_removed':
      return [prose('Removed column '), code(item)]
    case 'column_described':
      return [prose('Column '), code(item), prose(detail ? `: ${fieldMoves(change)}` : ' described differently')]
    case 'value_meanings_changed': {
      const was = (before.value_meanings ?? {}) as Record<string, string>
      const now = (after.value_meanings ?? {}) as Record<string, string>
      const added = Object.keys(now).filter((k) => was[k] !== now[k])
      const removed = Object.keys(was).filter((k) => !(k in now))
      if (!detail || (added.length === 0 && removed.length === 0)) {
        return [prose('Column '), code(item), prose(': value meanings changed')]
      }
      const parts: Segment[] = [prose('Column '), code(item), prose(': ')]
      if (added.length) {
        parts.push(prose(added.map((k) => `${k} = ${now[k]}`).join(', ')))
      }
      if (removed.length) {
        parts.push(prose(`${added.length ? '; ' : ''}no longer explains ${removed.join(', ')}`))
      }
      return parts
    }
    case 'metric_added': {
      const expression = String(after.expression ?? '')
      return expression
        ? [prose('Added metric '), code(item), prose(' = '), code(expression)]
        : [prose('Added metric '), code(item)]
    }
    case 'metric_removed':
      return [prose('Removed metric '), code(item)]
    case 'metric_expression_changed': {
      const now = String(after.expression ?? '')
      const was = String(before.expression ?? '')
      if (!now) return [code(item), prose(': expression changed')]
      return [code(item), prose(' is now '), code(now), ...(was ? [prose(' (was '), code(was), prose(')')] : [])]
    }
    case 'metric_filters_changed': {
      if (!detail) return [code(item), prose(': filters changed')]
      const { added, removed } = listDelta(asList(before.filters), asList(after.filters))
      const parts: Segment[] = [code(item)]
      if (added.length) parts.push(prose(' now also filters on '), ...codeList(added))
      if (added.length && removed.length) parts.push(prose(', and'))
      if (removed.length) parts.push(prose(' no longer filters on '), ...codeList(removed))
      return parts
    }
    case 'metric_joins_changed': {
      if (!detail) return [code(item), prose(': required joins changed')]
      const { added, removed } = listDelta(asList(before.required_joins), asList(after.required_joins))
      const parts: Segment[] = [code(item)]
      if (added.length) parts.push(prose(' now needs '), ...codeList(added))
      if (added.length && removed.length) parts.push(prose(', and'))
      if (removed.length) parts.push(prose(' no longer needs '), ...codeList(removed))
      return parts
    }
    case 'metric_described':
      return [prose('Metric '), code(item), prose(detail ? `: ${fieldMoves(change)}` : ' described differently')]
    case 'glossary_added':
      return [prose('Added '), prose(`“${item}”`)]
    case 'glossary_removed':
      return [prose('Removed '), prose(`“${item}”`)]
    case 'glossary_changed':
      return [prose(`“${item}”: ${detail ? fieldMoves(change) : 'changed'}`)]
    case 'reviewed_changed': {
      const reviewed = after.reviewed
      const verb = reviewed === false ? 'No longer marked reviewed' : 'Marked reviewed'
      return item ? [code(item), prose(`: ${verb.toLowerCase()}`)] : [prose(verb)]
    }
    default:
      return [prose(change.kind.replaceAll('_', ' '))]
  }
}

function groupOf(change: ChangeLike): { key: string; title: string; titleIsCode: boolean } {
  if (change.kind.startsWith('glossary_')) {
    return { key: 'glossary', title: 'Business terms', titleIsCode: false }
  }
  if (!change.entity_key) return { key: 'doc', title: 'This database', titleIsCode: false }
  return { key: change.entity_key, title: change.entity_key, titleIsCode: true }
}

/**
 * Changes grouped by what they are about, the ones that change numbers first.
 *
 * Within a group, a number-changing line comes before a wording one — except
 * that a table being added or removed leads its group, because every other
 * line under it is read in its light. Across groups, any group holding a
 * number-changing line comes first. Otherwise the server's order is kept,
 * which is stable.
 *
 * **Adding or removing a table folds its columns into one line.** The server
 * reports every column of a new table so each one's history starts where it
 * did, and forty "Added column" lines under one "Added to the layer" would
 * bury the metrics beside them. Metrics are never folded: they change numbers.
 */
export function groupChanges(changes: ChangeLike[]): ChangeGroup[] {
  const groups = new Map<string, ChangeGroup>()
  const folded = new Map<string, number>()

  const wholeTable = new Map<string, 'entity_added' | 'entity_removed'>()
  for (const change of changes) {
    if (change.kind === 'entity_added' || change.kind === 'entity_removed') {
      wholeTable.set(change.entity_key, change.kind)
    }
  }

  for (const change of changes) {
    const whole = wholeTable.get(change.entity_key)
    if (
      (whole === 'entity_added' && change.kind === 'column_added') ||
      (whole === 'entity_removed' && change.kind === 'column_removed')
    ) {
      folded.set(change.entity_key, (folded.get(change.entity_key) ?? 0) + 1)
      continue
    }
    const { key, title, titleIsCode } = groupOf(change)
    let group = groups.get(key)
    if (!group) {
      group = { key, entityKey: change.entity_key, title, titleIsCode, affectsSql: false, lines: [] }
      groups.set(key, group)
    }
    group.affectsSql ||= change.affects_sql
    group.lines.push({
      kind: change.kind,
      entityKey: change.entity_key,
      itemKey: change.item_key,
      affectsSql: change.affects_sql,
      segments: describeChange(change),
    })
  }

  for (const [entity, count] of folded) {
    const line = groups.get(entity)?.lines.find(
      (l) => l.kind === 'entity_added' || l.kind === 'entity_removed',
    )
    if (line) line.segments.push(prose(`, with ${count} ${count === 1 ? 'column' : 'columns'}`))
  }

  const ordered = [...groups.values()]
  const rank = (line: ChangeLine) =>
    line.kind === 'entity_added' || line.kind === 'entity_removed' ? 0 : line.affectsSql ? 1 : 2
  for (const group of ordered) {
    // `sort` is stable, so equal ranks keep the server's order.
    group.lines = [...group.lines].sort((a, b) => rank(a) - rank(b))
  }
  return [...ordered.filter((g) => g.affectsSql), ...ordered.filter((g) => !g.affectsSql)]
}

/**
 * One entry's history rows, gathered by the version they landed in.
 *
 * The server returns a row per change, and the version a table was added in
 * holds a row for every column added with it — twelve rows under one "v1"
 * that `groupChanges` would fold into a single line. Newest version first, in
 * the order the rows arrived.
 */
export function byVersion<T extends ChangeLike & { version: number }>(
  rows: T[],
): { version: number; rows: T[] }[] {
  const out: { version: number; rows: T[] }[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    if (last && last.version === row.version) last.rows.push(row)
    else out.push({ version: row.version, rows: [row] })
  }
  return out
}

/** How a version came to be, as words — `''` when a person saved it. */
export function originWords(origin: Record<string, unknown> | null | undefined): string {
  if (!origin) return ''
  if (origin.migrated) return 'recorded at migration'
  if (origin.deleted) return 'deleted'
  if (typeof origin.restored_from === 'number') return `restored from v${origin.restored_from}`
  if (origin.imported) return 'imported'
  if (Array.isArray(origin.generated_job_ids) && origin.generated_job_ids.length) {
    return 'generated'
  }
  return ''
}

/** Who and how, for a status line or a history row.
 *
 *  A version is a document somebody **published** — since drafts, nothing
 *  else writes one — so the person named is the publisher, and a generated or
 *  restored version says so before naming them. */
export function authorship(
  origin: Record<string, unknown> | null | undefined,
  author: string,
): string {
  const how = originWords(origin)
  if (how === 'recorded at migration') return how
  if (how === 'deleted') return author ? `deleted by ${author}` : 'deleted'
  if (how) return author ? `${how}, published by ${author}` : how
  return author ? `published by ${author}` : 'published'
}

/** "3 changes" — the count a history row carries. */
export function changeCount(counts: Record<string, number>): string {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  return `${total} ${total === 1 ? 'change' : 'changes'}`
}

/** "3 unpublished changes" / "No unpublished changes" — the draft's chip. */
export function unpublishedWords(count: number): string {
  if (count === 0) return 'No unpublished changes'
  return `${count} unpublished ${count === 1 ? 'change' : 'changes'}`
}

/** The first line of a note, for a list row; the rest is on the version. */
export function firstLine(note: string): string {
  return (note ?? '').split('\n').map((l) => l.trim()).find(Boolean) ?? ''
}

/** The history route, optionally filtered to one entry. */
export function historyPath(
  connectionId: string,
  entry?: { entity?: string; item?: string; version?: number },
): string {
  const base = `/sources/${connectionId}/semantic/history`
  if (entry?.version !== undefined) return `${base}/${entry.version}`
  const params = new URLSearchParams()
  if (entry?.entity) params.set('entity', entry.entity)
  if (entry?.item) params.set('item', entry.item)
  const query = params.toString()
  return query ? `${base}?${query}` : base
}
