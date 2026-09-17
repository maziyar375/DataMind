/**
 * *Needs attention*, grouped and worded — never decided.
 *
 * `npm run test:attention` — DOM-free and framework-free, like
 * `semantic-changes.ts` and for the same reason: the server's
 * `app/semantic/attention.py` decides what needs a person, from the binder,
 * the snapshots, the version history and the answers, and this file only says
 * it. A second copy of any rule here would be a second answer to "does this
 * table need me?", and the two would drift.
 *
 * What it does decide is shape. The server returns a flat list, most urgent
 * first; the editor shows one line for the draft, **one row per table** with
 * each of its reasons under it (a table that the schema broke *and* whose
 * columns moved is one thing to open, not two), and every undescribed table in
 * a single row, because twenty-one lines saying "no description" would bury
 * the one line that says a metric is broken.
 */

export type AttentionReason =
  | 'DRAFT_OLD'
  | 'INVALID'
  | 'COLUMNS_CHANGED'
  | 'METRIC_IGNORED'
  | 'UNREVIEWED_RELIED_ON'
  | 'UNDESCRIBED'

/** The fields this reads — `SemanticAttentionItem` from the API. */
export interface AttentionItemLike {
  reason: string
  table: string
  item: string
  detail: Record<string, unknown>
}

export type AttentionTone = 'red' | 'amber' | 'neutral'

/** One reason, as a sentence with a glyph — never colour alone. */
export interface AttentionLine {
  reason: AttentionReason
  glyph: string
  tone: AttentionTone
  text: string
  /** A second, quieter sentence: the binder's own words for what broke. */
  note?: string
}

/** Everything that needs a person about one table. */
export interface AttentionRow {
  table: string
  tone: AttentionTone
  lines: AttentionLine[]
  /** What the binder refused on this table, when it refused anything. */
  broken?: { metrics: number; columns: number }
  /** Its table gained columns since it was described — *Fill the gaps* can
   *  describe them without touching what is written. */
  fillable: boolean
}

export interface AttentionView {
  draft: AttentionLine | null
  rows: AttentionRow[]
  undescribed: { table: string; columns: number }[]
  /** The draft, each table with a row, and each undescribed table. */
  count: number
  /** The tables whose cards the filter shows. */
  tables: Set<string>
  tone: AttentionTone
}

const GLYPH: Record<AttentionReason, string> = {
  DRAFT_OLD: '◐',
  INVALID: '✕',
  COLUMNS_CHANGED: '△',
  METRIC_IGNORED: '◆',
  UNREVIEWED_RELIED_ON: '◌',
  UNDESCRIBED: '○',
}

const TONE: Record<AttentionReason, AttentionTone> = {
  DRAFT_OLD: 'amber',
  INVALID: 'red',
  COLUMNS_CHANGED: 'amber',
  METRIC_IGNORED: 'amber',
  UNREVIEWED_RELIED_ON: 'amber',
  UNDESCRIBED: 'neutral',
}

export function attentionView(items: AttentionItemLike[]): AttentionView {
  let draft: AttentionLine | null = null
  const rows = new Map<string, AttentionRow>()
  const undescribed: { table: string; columns: number }[] = []

  for (const item of items) {
    const reason = item.reason as AttentionReason
    if (!(reason in GLYPH)) continue
    if (reason === 'DRAFT_OLD') {
      draft = attentionLine(item)
      continue
    }
    if (reason === 'UNDESCRIBED') {
      undescribed.push({ table: item.table, columns: num(item.detail.columns) })
      continue
    }
    const key = item.table.toLowerCase()
    let row = rows.get(key)
    if (!row) {
      row = { table: key, tone: 'neutral', lines: [], fillable: false }
      rows.set(key, row)
    }
    row.lines.push(attentionLine(item))
    if (reason === 'INVALID') {
      row.broken = { metrics: num(item.detail.metrics), columns: num(item.detail.columns) }
    }
    row.tone = worst(row.tone, TONE[reason])
    if (reason === 'COLUMNS_CHANGED' && names(item.detail.added).length > 0) row.fillable = true
  }

  const list = [...rows.values()]
  let tone: AttentionTone = 'neutral'
  if (draft) tone = worst(tone, draft.tone)
  for (const row of list) tone = worst(tone, row.tone)
  return {
    draft,
    rows: list,
    undescribed,
    count: (draft ? 1 : 0) + list.length + undescribed.length,
    tables: new Set(list.map((row) => row.table)),
    tone,
  }
}

export function attentionLine(item: AttentionItemLike): AttentionLine {
  const reason = item.reason as AttentionReason
  const d = item.detail
  const base = { reason, glyph: GLYPH[reason] ?? '·', tone: TONE[reason] ?? 'neutral' }
  switch (reason) {
    case 'DRAFT_OLD': {
      const days = num(d.days)
      return {
        ...base,
        text: `This draft has not been touched in ${days} ${plural(days, 'day')}, and no answer reads it until it is published.`,
      }
    }
    case 'INVALID': {
      const issue = typeof d.issue === 'string' && d.issue ? d.issue : undefined
      if (d.entity === true) {
        return {
          ...base,
          text: 'Its table is not in the schema any more, so none of it reaches the model.',
          note: issue,
        }
      }
      const parts = [
        counted(num(d.metrics), 'metric'),
        counted(num(d.columns), 'column'),
      ].filter(Boolean)
      const total = num(d.metrics) + num(d.columns)
      return {
        ...base,
        text: `${joinAnd(parts)} no longer ${total === 1 ? 'matches' : 'match'} the schema, and ${total === 1 ? 'is' : 'are'} kept out of the prompt.`,
        note: issue,
      }
    }
    case 'COLUMNS_CHANGED': {
      const pieces = [
        moved(names(d.added), 'added'),
        moved(names(d.removed), 'removed'),
        moved(names(d.retyped), 'changed type'),
      ].filter(Boolean)
      const version = num(d.version)
      return {
        ...base,
        text: `Its columns changed after v${version} described it: ${pieces.join('; ')}.`,
      }
    }
    case 'METRIC_IGNORED': {
      const ignored = num(d.ignored)
      const used = num(d.used)
      return {
        ...base,
        text: `${ignored} ${plural(ignored, 'answer')} in the last ${num(d.days)} days left part of the “${item.item}” definition out, and ${used === 0 ? 'none' : used} used it.`,
      }
    }
    case 'UNREVIEWED_RELIED_ON': {
      const answers = num(d.answers)
      return {
        ...base,
        text: `A model wrote this and nobody has reviewed it, yet ${answers} Grounded ${plural(answers, 'answer')} in the last ${num(d.days)} days stood on it.`,
      }
    }
    case 'UNDESCRIBED':
      return { ...base, text: undescribedWords(1) }
    default:
      return { reason, glyph: '·', tone: 'neutral', text: '' }
  }
}

/** Which part of a table's card a row opens on: the part its most urgent
 *  reason is about. A broken metric is on Metrics, a column that moved is on
 *  Columns, and unreviewed text is the Meaning a reviewer signs off. */
export function attentionSection(
  row: Pick<AttentionRow, 'lines'>,
  broken?: { metrics: number; columns: number },
): 'meaning' | 'columns' | 'metrics' {
  const first = row.lines[0]?.reason
  if (first === 'INVALID') {
    if (broken && broken.metrics > 0) return 'metrics'
    if (broken && broken.columns > 0) return 'columns'
    return 'meaning'
  }
  if (first === 'COLUMNS_CHANGED') return 'columns'
  if (first === 'METRIC_IGNORED') return 'metrics'
  return 'meaning'
}

/** The undescribed row's sentence. */
export function undescribedWords(count: number): string {
  return count === 1
    ? '1 table has no description, so answers about it read the bare schema.'
    : `${count} tables have no description, so answers about them read the bare schema.`
}

/** "a, b and c" — or "a, b, c and 4 more" past `max`. */
export function listNames(values: string[], max = 3): string {
  if (values.length <= max) return joinAnd(values)
  return `${values.slice(0, max).join(', ')} and ${values.length - max} more`
}

function moved(values: string[], verb: string): string {
  return values.length ? `${values.length} ${verb} (${listNames(values)})` : ''
}

function joinAnd(parts: string[]): string {
  if (parts.length <= 1) return parts.join('')
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

function counted(n: number, noun: string): string {
  return n > 0 ? `${n} ${plural(n, noun)}` : ''
}

function plural(n: number, noun: string): string {
  return n === 1 ? noun : `${noun}s`
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function names(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function worst(a: AttentionTone, b: AttentionTone): AttentionTone {
  const rank = { neutral: 0, amber: 1, red: 2 }
  return rank[b] > rank[a] ? b : a
}
