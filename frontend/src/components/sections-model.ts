/**
 * The Sections screen's arithmetic — DOM-free, and tested (`npm run test:sections`).
 *
 * A section is a name, a sentence and a list of tables
 * (`docs/plans/retrieval-sections.md`). The screen edits a *draft* of the whole
 * set — sections partition the tables, so a move between two of them is one
 * edit — and saves it whole. Everything here fails quietly by construction:
 *
 * - a table that lands in two sections, or in none and is not shown as
 *   Unassigned, is a screen that lies about the database;
 * - a badge that sizes a section differently from `retrieve` is a warning
 *   that says "fits" over a section that will not;
 * - a name the server will refuse is a Save that fails after the edit.
 *
 * So the rules mirror the server's (`app/pipeline/sections.py`) and must move
 * with them.
 */

/** One section as the screen edits it. `key` is the draft's own handle —
 * stable across renames, and present before the server has assigned an id. */
export interface SectionDraft {
  key: string
  id: string | null
  name: string
  description: string
  tables: string[]
}

export type Fit = 'FITS' | 'TOO_LARGE' | 'EMPTY'

/** Where a table can be dropped: a section's key, or the bucket. */
export const UNASSIGNED = 'Unassigned'
export const UNASSIGNED_KEY = '__unassigned__'
export const MAX_NAME_CHARS = 60
export const MAX_DESCRIPTION_CHARS = 1000
export const MAX_SECTIONS = 60
const RESERVED = new Set([UNASSIGNED.toLowerCase(), 'none'])

interface SectionLike {
  id: string | null
  name: string
  description: string
  tables: string[]
}

let counter = 0
/** A draft handle nobody else holds. */
export function freshKey(): string {
  counter += 1
  return `s${counter}-${Date.now().toString(36)}`
}

export function toDrafts(sections: SectionLike[]): SectionDraft[] {
  return sections.map((s) => ({
    key: s.id ?? freshKey(),
    id: s.id,
    name: s.name,
    description: s.description,
    tables: [...s.tables],
  }))
}

/** The request body `PUT …/sections` takes — order is position. */
export function toWrite(drafts: SectionDraft[]): {
  id?: string
  name: string
  description: string
  tables: string[]
}[] {
  return drafts.map((d) => ({
    ...(d.id ? { id: d.id } : {}),
    name: d.name.trim(),
    description: d.description.trim(),
    tables: d.tables,
  }))
}

/** Whether two drafts would save the same set. Whitespace a save trims is not
 * an edit, and neither is a draft handle. */
export function sameSet(a: SectionDraft[], b: SectionDraft[]): boolean {
  return JSON.stringify(toWrite(a)) === JSON.stringify(toWrite(b))
}

/**
 * Every table in no section, in catalog (snapshot) order.
 *
 * Derived, never stored: the bucket is whatever the sections leave, so a
 * table cannot be in a section *and* in the bucket, or in neither.
 */
export function unassignedOf(drafts: SectionDraft[], catalog: string[]): string[] {
  const placed = new Set(drafts.flatMap((d) => d.tables))
  return catalog.filter((t) => !placed.has(t))
}

/**
 * Move `table` to the section `to` (a draft key) or to the bucket.
 *
 * Out of wherever it was, first: a table belongs to one section. Appended at
 * the end of its new home, so what was just moved is where the eye goes.
 * Returns a new array; drafts that did not change keep their identity.
 */
export function moveTable(drafts: SectionDraft[], table: string, to: string): SectionDraft[] {
  return drafts.map((d) => {
    const had = d.tables.includes(table)
    if (d.key === to) {
      return had ? d : { ...d, tables: [...d.tables, table] }
    }
    return had ? { ...d, tables: d.tables.filter((t) => t !== table) } : d
  })
}

/** Which section holds `table`, or null for the bucket. */
export function homeOf(drafts: SectionDraft[], table: string): SectionDraft | null {
  return drafts.find((d) => d.tables.includes(table)) ?? null
}

/** `table_chars` summed over the members the snapshot still has — the same
 * figure `retrieve` decides with, as the server reports it per table. */
export function sizeOf(tables: string[], weights: Map<string, number>): number {
  return tables.reduce((sum, t) => sum + (weights.get(t) ?? 0), 0)
}

export function fitOf(tables: string[], weights: Map<string, number>, budget: number): Fit {
  const present = tables.filter((t) => weights.has(t))
  if (present.length === 0) return 'EMPTY'
  return sizeOf(present, weights) <= budget ? 'FITS' : 'TOO_LARGE'
}

/** "9.2k", "190k", "640" — the size in the badge, in characters of schema. */
export function formatChars(chars: number): string {
  if (chars < 1000) return String(chars)
  if (chars < 100_000) {
    const k = Math.round(chars / 100) / 10
    return `${Number.isInteger(k) ? k.toFixed(0) : k.toFixed(1)}k`
  }
  return `${Math.round(chars / 1000)}k`
}

/** Why one section's name would be refused, or null. */
export function nameProblem(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed) return 'A section needs a name.'
  if (trimmed.length > MAX_NAME_CHARS) return `At most ${MAX_NAME_CHARS} characters.`
  if (/[,\n\r]/.test(trimmed)) return 'No commas or line breaks — the model replies with this name.'
  if (RESERVED.has(trimmed.toLowerCase())) return `“${trimmed}” is reserved.`
  return null
}

/**
 * Everything that would make the server refuse this set, keyed by draft key
 * (`''` for a problem with the set as a whole). Empty when it would save.
 */
export function problems(drafts: SectionDraft[]): Map<string, string> {
  const out = new Map<string, string>()
  if (drafts.length > MAX_SECTIONS) {
    out.set('', `A connection can have at most ${MAX_SECTIONS} sections.`)
  }
  const seen = new Map<string, string>()
  for (const d of drafts) {
    const problem = nameProblem(d.name)
    if (problem) {
      out.set(d.key, problem)
      continue
    }
    const lower = d.name.trim().toLowerCase()
    if (seen.has(lower)) {
      out.set(d.key, 'Another section already has this name.')
    } else {
      seen.set(lower, d.key)
    }
    if (d.description.trim().length > MAX_DESCRIPTION_CHARS) {
      out.set(d.key, `The description is longer than ${MAX_DESCRIPTION_CHARS} characters.`)
    }
  }
  return out
}

/** A name for a new section that no section has yet. */
export function newName(drafts: SectionDraft[], base = 'New section'): string {
  const taken = new Set(drafts.map((d) => d.name.trim().toLowerCase()))
  if (!taken.has(base.toLowerCase())) return base
  let n = 2
  while (taken.has(`${base} ${n}`.toLowerCase())) n += 1
  return `${base} ${n}`
}

/**
 * Replace one section with the division the server proposed for its tables.
 *
 * The first proposed part keeps the original's id and description, so a split
 * reads as the section getting smaller plus some new ones rather than as a
 * delete and several creates; tables the proposal could not place stay in
 * the original. Names are made unique against the rest of the set.
 */
export function applySplit(
  drafts: SectionDraft[],
  key: string,
  parts: { name: string; description: string; tables: string[] }[],
  leftover: string[],
): SectionDraft[] {
  const index = drafts.findIndex((d) => d.key === key)
  if (index < 0 || parts.length === 0) return drafts
  const original = drafts[index]
  const others = drafts.filter((d) => d.key !== key)
  const made: SectionDraft[] = []
  const keepHere = leftover.length > 0
  parts.forEach((part, i) => {
    const pool = [...others, ...made]
    const reuse = !keepHere && i === 0
    made.push({
      key: reuse ? original.key : freshKey(),
      id: reuse ? original.id : null,
      name: reuse ? original.name : newName(pool, part.name),
      description: reuse && original.description ? original.description : part.description,
      tables: part.tables,
    })
  })
  const head = keepHere ? [{ ...original, tables: leftover }] : []
  return [...drafts.slice(0, index), ...head, ...made, ...drafts.slice(index + 1)]
}

/** Move a section up or down one place. Position is the order on screen. */
export function reorder(drafts: SectionDraft[], key: string, delta: -1 | 1): SectionDraft[] {
  const from = drafts.findIndex((d) => d.key === key)
  const to = from + delta
  if (from < 0 || to < 0 || to >= drafts.length) return drafts
  const next = [...drafts]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}


// ── re-proposing over a saved set ─────────────────────────────────────────
/**
 * What adopting a fresh proposal would change, against what is on screen.
 *
 * A re-proposal after a sync is the only way back to a complete division once
 * the schema has moved, and it is also the one action here that can throw
 * away somebody's curation — the descriptions above all, which are what the
 * router actually reads. So it is shown as a diff and applied only if asked:
 * *"it never applies itself"* (`docs/plans/retrieval-sections.md` §1.4).
 */
export interface ProposalDiff {
  /** Sections the proposal has and this set does not, with their size. */
  added: { name: string; tables: number }[]
  /** Sections this set has and the proposal does not. Their tables are in
   * `moved`, so nothing disappears silently. */
  removed: { name: string; tables: number }[]
  /** Every table that would change home, in catalog order. */
  moved: { table: string; from: string; to: string }[]
  /** How many tables would stay exactly where they are. */
  unchanged: number
}

function homes(sets: SectionLike[]): Map<string, string> {
  const out = new Map<string, string>()
  for (const section of sets) {
    for (const table of section.tables) out.set(table, section.name)
  }
  return out
}

export function diffProposal(
  current: SectionLike[],
  proposed: SectionLike[],
  catalog: string[],
): ProposalDiff {
  const before = homes(current)
  const after = homes(proposed)
  const names = new Set(current.map((s) => s.name.trim().toLowerCase()))
  const proposedNames = new Set(proposed.map((s) => s.name.trim().toLowerCase()))

  const moved: ProposalDiff['moved'] = []
  let unchanged = 0
  // The catalog is every table the snapshot has, in its order. A member of a
  // section that the schema no longer has is in neither map and is not part
  // of this comparison: it is drift, and a proposal has nothing to say about
  // a table it cannot see.
  for (const table of catalog) {
    const from = before.get(table) ?? UNASSIGNED
    const to = after.get(table) ?? UNASSIGNED
    if (from === to) unchanged += 1
    else moved.push({ table, from, to })
  }

  return {
    added: proposed
      .filter((s) => !names.has(s.name.trim().toLowerCase()))
      .map((s) => ({ name: s.name, tables: s.tables.length })),
    removed: current
      .filter((s) => !proposedNames.has(s.name.trim().toLowerCase()))
      .map((s) => ({ name: s.name, tables: s.tables.length })),
    moved,
    unchanged,
  }
}

/** Whether a proposal would change anything at all. */
export function sameDivision(diff: ProposalDiff): boolean {
  return diff.added.length === 0 && diff.removed.length === 0 && diff.moved.length === 0
}

/**
 * The proposal, as drafts, keeping what the current set can lend it.
 *
 * A section the proposal names the same thing keeps its **id** — so adopting
 * one is an edit to that row rather than a delete and a create, and its
 * `created_at` survives — and keeps a **description somebody wrote**, because
 * the generated sentence is the one thing a curator's is always better than.
 */
export function adoptProposal(
  current: SectionDraft[],
  proposed: SectionLike[],
): SectionDraft[] {
  const byName = new Map(current.map((d) => [d.name.trim().toLowerCase(), d]))
  return proposed.map((section) => {
    const existing = byName.get(section.name.trim().toLowerCase())
    return {
      key: existing?.key ?? freshKey(),
      id: existing?.id ?? null,
      name: section.name,
      description: existing?.description.trim() || section.description,
      tables: [...section.tables],
    }
  })
}
