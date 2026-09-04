/**
 * The knowledge tab's reasoning, with no DOM in it.
 *
 * `npm run test:template` — Node runs the suite directly (type stripping, no
 * bundler, no test framework, no new dependency), the arrangement
 * `semantic-drift.ts` and `report-readiness.ts` use, and for the same reason:
 * the answers here are worth getting right, and the failure mode is a curator
 * staring at a parameter panel that does not match the SQL under it.
 *
 * Five questions live here, and none of them needs a browser:
 *
 *  - **which section does a row belong in** — needs-you, suggested, or taught;
 *  - **what does the row say on its second line** — what it touches, and why
 *    you are looking at it;
 *  - **how is a `{slot}` shown inside a question** — the one piece of syntax
 *    the curator has to learn, so it is rendered as spans and never as markup;
 *  - **where is a proposal's literal in the SQL** — the highlight has to land
 *    on the right `'EMEA'` when the statement filters on it twice;
 *  - **is this template ready to save** — the disagreements between the
 *    question's braces and the SQL's slots that the server would reject.
 *
 * Nothing here formats a colour or a class name: tone words come back and the
 * component maps them to tokens, so a token rename is one file.
 */

// ── the shapes this reads, kept to what it actually needs ─────────────────
export interface TemplateSlot {
  name: string
  type: string
  comment: string
}

export interface TemplateRow {
  id: string
  question: string
  params: TemplateSlot[]
  referenced_tables: string[]
  status: string
  status_reason: string
  role: string
  hit_count: number
  last_hit_at: string | null
  verified_at: string | null
  /** Phase 4. Optional so every existing caller and test still builds a row
   *  with the fields it had — a healthy template has neither. */
  conflicts_with?: string[]
  conflict_evidence?: unknown
}

export interface ProposalRow {
  name: string
  literal: string
  occurrence: number
  eligible: boolean
  suggested: boolean
  reason: string
}

export type Tone = 'green' | 'amber' | 'red' | 'accent' | 'neutral' | 'faint'

/** `✓` active, `⚠` needs you, `○` not yet a template.
 *
 *  Status is never carried by colour alone — the glyph and the word are what
 *  make the list legible in greyscale, and what make it readable at all to
 *  someone who cannot tell green from amber. */
export type Glyph = '✓' | '⚠' | '○'

export interface StatusView {
  glyph: Glyph
  label: string
  tone: Tone
  /** True when this row belongs in "Needs you". */
  needsYou: boolean
}

/** How many days without a match before a template is worth mentioning.
 *  A maintenance cost with no return — but the treatment is one faint line,
 *  never an accusation. */
export const UNUSED_AFTER_DAYS = 90

const STATUS: Record<string, StatusView> = {
  ACTIVE: { glyph: '✓', label: 'Active', tone: 'green', needsYou: false },
  STALE: { glyph: '⚠', label: 'Stale', tone: 'amber', needsYou: true },
  CONFLICTED: { glyph: '⚠', label: 'Conflicted', tone: 'amber', needsYou: true },
  ARCHIVED: { glyph: '○', label: 'Archived', tone: 'faint', needsYou: false },
}

/**
 * What the leading glyph and the status word say.
 *
 * `drifted` is the list endpoint's read-time verdict: the SQL no longer
 * resolves against the current snapshot. Phase 1 reports it without writing
 * `STALE`, so a row can be `ACTIVE` in the database and stale on the screen —
 * and the screen is the one telling the truth about right now.
 */
export function statusOf(row: TemplateRow, drifted = false): StatusView {
  if (drifted && row.status === 'ACTIVE') return STATUS.STALE
  return STATUS[row.status] ?? STATUS.ACTIVE
}

/** The words for a role, or '' for the default — a chip nobody needs. */
export function roleLabel(role: string): string {
  if (role === 'BENCHMARK_ONLY') return 'Measures accuracy'
  if (role === 'HELD_OUT') return 'Held out'
  return ''
}

// ── the second line of a row ──────────────────────────────────────────────
/**
 * *What it touches* on the left, *why you are looking at it* on the right.
 *
 * The right half is the whole reason three sections read as one list: every
 * row answers the same question in the same place, whether it is broken,
 * suggested, or working.
 */
export function rowSubtitle(
  row: TemplateRow,
  drifted: boolean,
  now: Date = new Date(),
): { left: string; right: string; tone: Tone } {
  const left = row.referenced_tables.join(', ')
  const status = statusOf(row, drifted)

  if (status.needsYou) {
    return {
      left,
      right: row.status_reason || 'This template stopped working when the schema changed.',
      tone: 'amber',
    }
  }
  if (row.status === 'ARCHIVED') {
    return { left, right: 'Archived', tone: 'faint' }
  }
  if (row.hit_count === 0) {
    // Not an accusation: a template nobody has asked for yet is the normal
    // state of a store on its first day.
    return { left, right: 'No matches yet', tone: 'faint' }
  }
  const hits = `${row.hit_count} ${row.hit_count === 1 ? 'hit' : 'hits'}`
  if (isUnused(row, now)) {
    return { left, right: `${hits} · no matches in 90 days`, tone: 'faint' }
  }
  return { left, right: hits, tone: 'neutral' }
}

// ── the score (Phase 6) ───────────────────────────────────────────────────
export interface ScoreRun {
  status: string
  total: number
  scored: number
  held_out_total: number
  held_out_matched: number
  taught_total: number
  taught_matched: number
  finished_at: string | null
  created_at: string
  error_message?: string
}

export interface ScoreView {
  /** The number that goes **first and larger**, as a fraction, or null when
   *  there is none. Null and not zero: a run that scored no held-out question
   *  has no held-out accuracy, and 0% would be the loudest wrong answer. */
  heldOut: number | null
  heldOutCount: number
  /** Second, and smaller. Shown because hiding it would be dishonest, and
   *  shown second because it is the number that goes up for the wrong
   *  reasons. */
  taught: number | null
  taughtCount: number
  /** Oldest → newest, held-out only. The taught number is deliberately not on
   *  the sparkline: one line, one series, and it is the honest one. */
  spark: number[]
  /** Questions the run could not score — a parameter with no values to try, a
   *  stored answer that no longer runs. Surfaced, because an accuracy over a
   *  shrinking denominator is the classic silent lie. */
  unscored: number
  ran: boolean
  running: boolean
  failed: string
}

/**
 * The score strip, from a set's run history. Newest run first, as the API
 * returns it.
 *
 * **Two numbers, and the strip says which to believe.** Genie's Evaluations tab
 * shows one; that is a weakness to improve on, not a design to copy.
 */
export function scoreView(runs: ScoreRun[], heldOutCount = 0): ScoreView {
  const finished = runs.filter((r) => r.status === 'SUCCEEDED')
  const latest = finished[0]
  const running = runs.some((r) => r.status === 'QUEUED' || r.status === 'RUNNING')
  const failed = runs[0]?.status === 'FAILED' ? (runs[0].error_message ?? '') : ''

  if (!latest) {
    return {
      heldOut: null, heldOutCount, taught: null, taughtCount: 0,
      spark: [], unscored: 0, ran: false, running, failed,
    }
  }
  return {
    heldOut: ratio(latest.held_out_matched, latest.held_out_total),
    heldOutCount: latest.held_out_total || heldOutCount,
    taught: ratio(latest.taught_matched, latest.taught_total),
    taughtCount: latest.taught_total,
    // Oldest to newest, so the line reads left to right the way time does.
    spark: finished
      .map((r) => ratio(r.held_out_matched, r.held_out_total))
      .filter((v): v is number => v !== null)
      .reverse(),
    unscored: Math.max(0, latest.total - latest.scored),
    ran: true,
    running,
    failed,
  }
}

function ratio(matched: number, total: number): number | null {
  return total > 0 ? matched / total : null
}

/** A percentage with no decimals, or an em dash. Never `0%` for "no data". */
export function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}

/**
 * A sparkline's points as `0..1` heights, normalised against **the full range
 * 0–100%**, not against the series' own min and max.
 *
 * Self-normalising would turn a set of runs at 71/72/73% into a dramatic climb
 * — which is exactly the misreading a score strip must not invite. Against the
 * fixed scale, a flat store looks flat.
 */
export function sparkHeights(values: number[]): number[] {
  return values.map((v) => Math.max(0, Math.min(1, v)))
}

// ── the conflict's evidence (Phase 4) ─────────────────────────────────────
export interface EvidenceCell {
  columns: string[]
  rows: string[][]
}

export interface ConflictView {
  summary: string
  mine: EvidenceCell
  theirs: EvidenceCell
  /** False when there is nothing to show — an ordinary template, or a
   *  conflict recorded before the evidence column existed. The pane renders
   *  the reason alone rather than an empty table. */
  hasRows: boolean
}

/**
 * The two answers that disagree, in the shape §4.7's pane renders.
 *
 * **The rows are the evidence.** Fabric detects conflicting instructions by
 * reasoning over SQL text and reports a confidence score of one to five;
 * DataMind ran both statements through the guard and compared the result sets,
 * so what goes on the screen is *"481,220 against 512,940"* rather than *"we
 * think these might disagree"*. A conflict a curator cannot see the proof of
 * is one more warning nobody acts on.
 *
 * Defensive about every field: this comes from a JSONB column written by a
 * worker, and a pane that throws on a missing key would take the whole tab
 * down over a template nobody was looking at.
 */
export function conflictEvidence(evidence: unknown): ConflictView {
  const raw = (evidence ?? {}) as Record<string, unknown>
  const mine = cell(raw.left_columns, raw.left_rows)
  const theirs = cell(raw.right_columns, raw.right_rows)
  return {
    summary: typeof raw.summary === 'string' ? raw.summary : '',
    mine,
    theirs,
    hasRows: mine.rows.length > 0 || theirs.rows.length > 0,
  }
}

function cell(columns: unknown, rows: unknown): EvidenceCell {
  return {
    columns: Array.isArray(columns) ? columns.map(String) : [],
    rows: Array.isArray(rows)
      ? rows.filter(Array.isArray).map((r) => (r as unknown[]).map(String))
      : [],
  }
}

/**
 * Which cells differ between two evidence rows, by position.
 *
 * So the pane can mark the number that moved rather than making the reader
 * compare two tables by eye — which is what turns a conflict pane from a
 * report into something a curator acts on in one read. Positional, because the
 * comparator that produced the rows is positional too.
 */
export function differingCells(mine: string[], theirs: string[]): boolean[] {
  const width = Math.max(mine.length, theirs.length)
  return Array.from({ length: width }, (_, i) => mine[i] !== theirs[i])
}

/** A template nobody has matched in ninety days. Information, not a verdict. */
export function isUnused(row: TemplateRow, now: Date = new Date()): boolean {
  if (row.status !== 'ACTIVE' || row.hit_count === 0) return false
  if (!row.last_hit_at) return true
  const days = (now.getTime() - new Date(row.last_hit_at).getTime()) / 86_400_000
  return days >= UNUSED_AFTER_DAYS
}

// ── the question, with its braces shown ───────────────────────────────────
export interface QuestionPart {
  text: string
  slot: boolean
}

/**
 * A question split into plain text and `{slots}`.
 *
 * Read at display time into spans, never into markup — the same rule
 * `chat-format.ts` follows, and for the same reason: a question is user text
 * and the only safe way to style part of it is to render the parts separately.
 * The braces are kept in the output so what the curator typed is what they
 * see; only the tone changes.
 */
export function questionParts(question: string): QuestionPart[] {
  const parts: QuestionPart[] = []
  const pattern = /\{[^{}]*\}/g
  let at = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(question)) !== null) {
    if (match.index > at) parts.push({ text: question.slice(at, match.index), slot: false })
    parts.push({ text: match[0], slot: true })
    at = match.index + match[0].length
  }
  if (at < question.length) parts.push({ text: question.slice(at), slot: false })
  return parts
}

/** The `{names}` a question declares, in order, without duplicates. */
export function questionSlots(question: string): string[] {
  const out: string[] = []
  for (const part of questionParts(question)) {
    if (!part.slot) continue
    const name = part.text.slice(1, -1).trim()
    if (name && !out.includes(name)) out.push(name)
  }
  return out
}

/**
 * The editor's live preview: the pattern with two concrete values in it.
 *
 * Nobody understands `{region}` from the brace; everybody understands
 * *"revenue by month for EMEA in 2026"*. The samples are per slot type
 * because a date example that read `{year}` would teach nothing.
 */
export function previewQuestion(question: string, params: TemplateSlot[]): string {
  let out = question
  for (const param of params) {
    out = out.split(`{${param.name}}`).join(sampleFor(param))
  }
  return out
}

function sampleFor(param: TemplateSlot): string {
  const listed = valuesOf(param.comment)
  if (listed.length > 0) return listed[0]
  if (param.type === 'date' || param.type === 'datetime') return '2026-01-01'
  if (param.type === 'number') return '1000'
  if (param.type === 'boolean') return 'true'
  return param.name.toUpperCase()
}

/** The value list a comment like `one of: EMEA, NA, APAC` declares. */
export function valuesOf(comment: string): string[] {
  const at = comment.indexOf(':')
  if (at < 0) return []
  const tail = comment.slice(at + 1)
  if (!tail.includes(',')) return []
  return tail.split(',').map((v) => v.trim()).filter(Boolean)
}

// ── highlighting a literal inside the SQL ─────────────────────────────────
export interface SqlSpan {
  text: string
  /** The proposal name this span is the literal for, or null. */
  slot: string | null
}

/**
 * The SQL split so each proposed literal can be marked in place.
 *
 * The curator sees *what would change*, not an abstract list — the
 * Teach-Q&A lesson: reinterpret in front of the person before they save.
 *
 * `occurrence` is why this is not a `String.replace`: a statement that filters
 * on `'EMEA'` and also mentions it in a `CASE` has two identical texts and only
 * one of them is the proposal's.
 */
export function markLiterals(sql: string, proposals: ProposalRow[]): SqlSpan[] {
  const marks: { at: number; length: number; slot: string }[] = []
  for (const proposal of proposals) {
    const at = nthIndexOf(sql, proposal.literal, proposal.occurrence)
    if (at >= 0) marks.push({ at, length: proposal.literal.length, slot: proposal.name })
  }
  marks.sort((a, b) => a.at - b.at)

  const spans: SqlSpan[] = []
  let cursor = 0
  for (const mark of marks) {
    if (mark.at < cursor) continue // overlapping marks: the first one wins
    if (mark.at > cursor) spans.push({ text: sql.slice(cursor, mark.at), slot: null })
    spans.push({ text: sql.slice(mark.at, mark.at + mark.length), slot: mark.slot })
    cursor = mark.at + mark.length
  }
  if (cursor < sql.length) spans.push({ text: sql.slice(cursor), slot: null })
  return spans
}

function nthIndexOf(haystack: string, needle: string, n: number): number {
  if (!needle) return -1
  let at = -1
  for (let i = 0; i <= n; i += 1) {
    at = haystack.indexOf(needle, at + 1)
    if (at < 0) return -1
  }
  return at
}

// ── is this ready to save ─────────────────────────────────────────────────
export interface Readiness {
  ready: boolean
  /** One sentence, or '' when there is nothing to say. Shown under the SQL. */
  issue: string
}

/**
 * What the Save button knows before the server answers.
 *
 * Deliberately narrow: this checks only the things the *browser* can be sure
 * of — an empty field, and a disagreement between the question's braces and
 * the ticked parameters. Whether the SQL is legal is never guessed at locally,
 * because a local "looks fine" that the server then rejects is the worst
 * possible interaction.
 */
export function readiness(
  question: string,
  sql: string,
  params: TemplateSlot[],
  valid: boolean | null,
): Readiness {
  if (!question.trim()) return { ready: false, issue: '' }
  if (!sql.trim()) return { ready: false, issue: '' }

  const named = questionSlots(question)
  const missing = params.map((p) => p.name).filter((n) => !named.includes(n))
  if (missing.length > 0) {
    return {
      ready: false,
      issue:
        `The question does not mention ${missing.map((n) => `{${n}}`).join(', ')} — ` +
        'a parameter the question never names can never be filled in.',
    }
  }
  const unused = named.filter((n) => !params.some((p) => p.name === n))
  if (unused.length > 0) {
    return {
      ready: false,
      issue:
        `${unused.map((n) => `{${n}}`).join(', ')} ` +
        `${unused.length === 1 ? 'is' : 'are'} in the question but not in the SQL. ` +
        'Tick the literal it should replace.',
    }
  }
  // `null` is "the server has not answered yet" — a state the button waits in
  // rather than guesses through.
  if (valid !== true) return { ready: false, issue: '' }
  return { ready: true, issue: '' }
}

// ── the sections of the list ──────────────────────────────────────────────
export interface Sections {
  needsYou: TemplateRow[]
  taught: TemplateRow[]
  archived: TemplateRow[]
}

/**
 * The list, top to bottom: **what is broken, what has been taught, what was
 * archived.**
 *
 * The archive is last because it is the least urgent thing on the screen, even
 * though it is the thing the feature is named after. (Phase 3 inserts
 * *Suggested* between the first two, from traffic and from corrected tiles.)
 */
export function sections(rows: TemplateRow[], staleIds: string[]): Sections {
  const stale = new Set(staleIds)
  const out: Sections = { needsYou: [], taught: [], archived: [] }
  for (const row of rows) {
    if (row.status === 'ARCHIVED') out.archived.push(row)
    else if (statusOf(row, stale.has(row.id)).needsYou) out.needsYou.push(row)
    else out.taught.push(row)
  }
  return out
}

/**
 * The count on the tab: **only the number of things needing a human.**
 *
 * Zero work, no number. A badge that always shows a total is decoration; a
 * badge that appears when there is work is a signal.
 */
export function tabCount(rows: TemplateRow[], staleIds: string[]): number | undefined {
  const n = sections(rows, staleIds).needsYou.length
  return n > 0 ? n : undefined
}

/** Case-insensitive search over what a row actually says. */
export function matches(row: TemplateRow, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return (
    row.question.toLowerCase().includes(needle) ||
    row.referenced_tables.some((t) => t.toLowerCase().includes(needle)) ||
    row.params.some((p) => p.name.toLowerCase().includes(needle))
  )
}

/**
 * The same search, over a backlog row.
 *
 * A list is searchable or it is not, and which list happens to be on screen is
 * not a reason for the box to appear and disappear: a connection with nothing
 * taught and thirty questions waiting showed **no** search at all, while the
 * one next to it in the same rail showed one. What each list is searched *by*
 * differs, because a suggestion has no tables and no parameters — it has the
 * question, the reason it is here, and the words nobody recognised.
 */
export function matchesSuggestion(
  row: Pick<SuggestionRow, 'question' | 'reason' | 'words'>,
  query: string,
): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return (
    row.question.toLowerCase().includes(needle) ||
    row.reason.toLowerCase().includes(needle) ||
    row.words.some((w) => w.toLowerCase().includes(needle))
  )
}

/** And over a flag: the question, what the person wrote, and who wrote it. */
export function matchesReview(
  review: { question: string; comment: string; flagged_by: string },
  query: string,
): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return (
    review.question.toLowerCase().includes(needle) ||
    review.comment.toLowerCase().includes(needle) ||
    review.flagged_by.toLowerCase().includes(needle)
  )
}


// ── the backlog and the queue (Phase 3) ───────────────────────────────────
export interface SuggestionRow {
  kind: 'FLAGGED' | 'BACKFILL' | 'TRAFFIC' | 'FAILED' | 'UNKNOWN_WORDS'
  question: string
  count: number
  reason: string
  sql: string
  words: string[]
}

/**
 * How a suggestion presents itself in the list.
 *
 * Every row in every section has the same anatomy — glyph, question, what it
 * touches, why you are looking at it — which is what makes four sections read
 * as one list rather than four widgets. A suggestion is `○`: **not yet
 * knowledge**, and never `⚠`, because nothing here is broken. A backlog that
 * looked like a fault list would train people to dread opening the tab.
 */
export function suggestionView(row: SuggestionRow): {
  glyph: Glyph
  tone: Tone
  action: string
} {
  if (row.kind === 'FLAGGED') {
    // The one suggestion that *is* work somebody is waiting on.
    return { glyph: '⚠', tone: 'amber', action: 'Review' }
  }
  if (row.kind === 'UNKNOWN_WORDS') {
    // It names a word, not a question. "Teach this" would be the wrong verb:
    // the fix is often a synonym in the semantic layer, not a template.
    return { glyph: '○', tone: 'faint', action: '' }
  }
  return { glyph: '○', tone: 'faint', action: 'Teach this' }
}

/** The section a suggestion belongs in. Flags are work; the rest is a queue. */
export function suggestionSection(row: SuggestionRow): 'needsYou' | 'suggested' {
  return row.kind === 'FLAGGED' ? 'needsYou' : 'suggested'
}

/**
 * What a curator has to decide about a correction, as three exclusive options.
 *
 * §1.5's rule made into an interaction: the curator decides whether a
 * correction is *question-shaped* or *definition-shaped*, and the product does
 * not guess. A router that guessed would be wrong often enough to teach people
 * to distrust the whole queue — and the two homes are genuinely different, one
 * being a template and the other the semantic layer.
 */
export const CORRECTION_SHAPES = [
  {
    value: 'template',
    label: 'a question people ask',
    detail: 'save as a template',
  },
  {
    value: 'definition',
    label: 'a definition',
    detail: 'add to the semantic layer',
  },
  { value: 'dismiss', label: 'neither', detail: 'dismiss with a reason' },
] as const

export type CorrectionShape = (typeof CORRECTION_SHAPES)[number]['value']

/** Whether *Save and resolve* may be pressed yet, and why not if not. */
export function resolveReadiness(
  shape: CorrectionShape,
  note: string,
  templateSaved: boolean,
): Readiness {
  if (shape === 'dismiss') {
    // A dismissal with no reason is indistinguishable, from the flagger's
    // side, from being ignored — so the reason is required, not encouraged.
    return note.trim()
      ? { ready: true, issue: '' }
      : {
          ready: false,
          issue: 'Say why — the person who flagged this will see the reason.',
        }
  }
  if (shape === 'template' && !templateSaved) {
    return { ready: false, issue: 'Save the template first.' }
  }
  return { ready: true, issue: '' }
}

// ── the embedding matcher (Phase 7) ──────────────────────────────────────

/** The status shape the store-health strip reads. Mirrors `EmbeddingStatus`
 *  in `api/types.ts`, declared structurally so this file stays DOM-free and
 *  import-free the way the rest of it is. */
export interface EmbeddingState {
  enabled: boolean
  model: string
  dimension: number
  templates: number
  indexed: number
  message: string
  /** Whether the deployment has an embedder at all — one provider, resolved by
   *  the server rather than chosen here. False is the state where the control
   *  has nothing to switch to, and saying so is the difference between an
   *  offer and a button that fails. */
  hasEmbedder: boolean
}

export interface EmbeddingView {
  /** What the control says it is right now. */
  label: string
  /** One honest sentence under it. Never a promise the next question will not
   *  keep — which is the whole reason `indexing` is a separate state. */
  detail: string
  /** `off` is the shipped default and is **not** a warning: `pg_trgm` needs no
   *  provider, no key and no budget, and most connections will stay here. */
  tone: 'off' | 'indexing' | 'on' | 'problem'
}

/** How the store is searched, in a sentence a curator can act on.
 *
 * Four states, and the middle two are the ones a boolean would collapse:
 *
 * * **off** — no model pinned. The lexical matcher answers, which is the
 *   shipped behaviour, so this reads as a choice and not as a fault.
 * * **indexing** — a model is pinned and some questions have no vector yet.
 *   Saying *on* here would promise a behaviour the next question will not
 *   show; the count says exactly how far along it is.
 * * **on** — pinned, and every live question is indexed.
 * * **problem** — the provider said no. Its own sentence is shown, because
 *   *"Anthropic does not offer an embedding endpoint"* is a fix somebody can
 *   act on and *"unavailable"* is not.
 */
export function embeddingView(state: EmbeddingState): EmbeddingView {
  if (state.message) {
    return { label: 'Embedding search', detail: state.message, tone: 'problem' }
  }
  if (!state.enabled) {
    // The off state describes what the *other* mode adds, never what this one
    // lacks — §4.10's rule, and the reason word matching reads as a choice.
    // With no embedder set up, the second sentence changes from an offer to
    // the one action that would make it possible: a button that cannot succeed
    // is worse than a sentence saying why. One action, not a choice between
    // providers — that is the whole difference between this and the model that
    // answers.
    const detail =
      'Questions are matched on the words they share. Embedding search also ' +
      'matches ones that mean the same thing in different words.'
    return {
      label: 'Word matching',
      detail: state.hasEmbedder
        ? detail
        : `${detail} No model provider is set up to embed yet — give one an ` +
          'embedding model in LLM providers.',
      tone: 'off',
    }
  }
  if (state.templates === 0) {
    // Pinned, with nothing taught. Not "indexing" (there is nothing to index
    // and the count would read `0 of 0`) and not "on" either, because nothing
    // is being matched by meaning yet. The honest sentence is that it is ready
    // and waiting for the first question.
    return {
      label: 'Embedding search',
      detail:
        `Ready with ${state.model}. The first question taught here is indexed ` +
        'as it is saved.',
      tone: 'indexing',
    }
  }
  const missing = Math.max(0, state.templates - state.indexed)
  if (missing > 0) {
    return {
      label: 'Embedding search · indexing',
      detail:
        `${state.indexed} of ${state.templates} questions indexed — ` +
        `${missing} still ${missing === 1 ? 'matches' : 'match'} on words alone.`,
      tone: 'indexing',
    }
  }
  return {
    label: 'Embedding search',
    detail:
      `All ${state.templates} ${state.templates === 1 ? 'question' : 'questions'} ` +
      `indexed with ${state.model}. Questions that mean the same thing match ` +
      'even when the words differ.',
    tone: 'on',
  }
}

/** The indexing half of a sweep's summary, or `''` when there was none.
 *
 * Separate from the rest of `sweepSummary` because it is the one half that can
 * report a *failure* without the sweep having failed: the staleness and
 * conflict passes both completed, and the vectors are simply a pass behind.
 * Saying so is the difference between "the index is stale" and "the check
 * broke", and only the first is true.
 */
export function indexSummary(result: {
  indexed: number
  index_current: number
  index_truncated: boolean
  index_error: string
}): string {
  if (result.index_error) {
    return ` The index could not be brought up to date: ${result.index_error}`
  }
  if (!result.indexed) {
    return ''
  }
  const more = result.index_truncated
    ? ' More remain — they are indexed on the next check.'
    : ''
  return (
    ` ${result.indexed} question${result.indexed === 1 ? '' : 's'} re-indexed ` +
    `for embedding search.${more}`
  )
}
