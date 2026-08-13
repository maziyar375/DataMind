/**
 * Reading a dashboard file, before anything is sent.
 *
 * DOM-free and dependency-free, like `dashboard-schedule.ts` and
 * `table-format.ts`, and for the same reason: **every way this can be wrong is
 * quiet.** A parser that accepts the wrong file shows a confident preview of
 * nothing; an auto-match that guesses wrong points a tile at a database the
 * user never chose, and the tile will happily draw a number from it. Neither
 * mistake announces itself, so both get checked — `npm run test:document`.
 *
 * The backend validates all of this again and does not trust a byte of it. This
 * exists so the import dialog can say what is in the file *before* it is sent,
 * which is the only way the connection mapping can be asked for at all.
 */
import type { Connection, DashboardDocument, DashboardDocumentConnection } from '../api/types'

/** The marker a file must carry, and the highest version this build reads. */
export const DOCUMENT_FORMAT = 'datamind.dashboard'
export const DOCUMENT_VERSION = 1

export type ParseResult =
  | { ok: true; document: DashboardDocument }
  | { ok: false; error: string }

/**
 * Turn the text of a dropped file into a document, or into a sentence.
 *
 * Checked in the order a reader loses confidence: is it JSON, is it *this*
 * kind of JSON, is it a version we read, and only then is it shaped right. A
 * file from a later release is "written by a newer version", never a complaint
 * about a field that release invented.
 */
export function parseDocument(text: string): ParseResult {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return { ok: false, error: 'That file is not JSON, so it is not a dashboard export.' }
  }

  if (!isRecord(raw)) {
    return { ok: false, error: 'That file is not a dashboard export.' }
  }
  if (raw.format !== DOCUMENT_FORMAT) {
    return { ok: false, error: 'That file is not a dashboard export.' }
  }

  const version = raw.version
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 1) {
    return { ok: false, error: 'That export does not say which format version it is.' }
  }
  if (version > DOCUMENT_VERSION) {
    return {
      ok: false,
      error: `That export is in format version ${version}; this installation reads version `
        + `${DOCUMENT_VERSION}. Update DataMind, then import it again.`,
    }
  }

  const dashboard = raw.dashboard
  if (!isRecord(dashboard) || typeof dashboard.name !== 'string' || !dashboard.name.trim()) {
    return { ok: false, error: 'That export names no dashboard.' }
  }
  if (!Array.isArray(raw.tiles) || !Array.isArray(raw.connections)) {
    return { ok: false, error: 'That export is missing its tiles or its connections.' }
  }

  return { ok: true, document: raw as unknown as DashboardDocument }
}

/**
 * Which of this user's connections each `ref` in the file probably means.
 *
 * **Exact name, or nothing.** Connection names are unique per owner, so a name
 * that matches is the same database under the same label — the case that makes
 * re-importing your own export one click. Everything softer than that was
 * considered and refused: matching on engine alone would silently point a
 * revenue tile at whichever Postgres happened to be first in the list, and a
 * wrong number under the right title is worse than a picker the user has to
 * fill in. An unmatched ref comes back as `''`, which the dialog renders as a
 * question rather than an answer.
 */
export function matchConnections(
  refs: DashboardDocumentConnection[],
  connections: Connection[],
): Record<string, string> {
  const byName = new Map<string, string>()
  for (const connection of connections) {
    byName.set(connection.name.trim().toLowerCase(), connection.id)
  }

  const mapping: Record<string, string> = {}
  for (const ref of refs) {
    mapping[ref.ref] = byName.get(ref.name.trim().toLowerCase()) ?? ''
  }
  return mapping
}

/**
 * Whether the chosen connection runs a different engine from the one the SQL
 * was written against.
 *
 * A warning, never a block: the same query often runs on both, and the user may
 * know something the picker does not. But a dialect change is the likeliest
 * reason a dozen tiles are about to be refused, and saying so here costs one
 * line where finding out costs a round trip and twelve error messages.
 */
export function engineMismatches(
  refs: DashboardDocumentConnection[],
  mapping: Record<string, string>,
  connections: Connection[],
): { ref: DashboardDocumentConnection; chosen: Connection }[] {
  const byId = new Map(connections.map((connection) => [connection.id, connection]))
  const found: { ref: DashboardDocumentConnection; chosen: Connection }[] = []
  for (const ref of refs) {
    const chosen = byId.get(mapping[ref.ref] ?? '')
    if (!chosen || !ref.database_type) continue
    if (chosen.database_type.toLowerCase() !== ref.database_type.toLowerCase()) {
      found.push({ ref, chosen })
    }
  }
  return found
}

/** Refs the file needs and the user has not answered for yet. */
export function unmappedRefs(
  document: DashboardDocument,
  mapping: Record<string, string>,
): DashboardDocumentConnection[] {
  const needed = new Set(
    document.tiles
      .filter((tile) => tile.tile_type !== 'TEXT' && tile.connection_ref)
      .map((tile) => tile.connection_ref as string),
  )
  return document.connections.filter((ref) => needed.has(ref.ref) && !mapping[ref.ref])
}

/**
 * Tiles that name no database at all — a TEXT tile, which needs none, and a
 * tile whose connection was deleted before the export, which the mapping above
 * cannot rescue because the file never said what it pointed at.
 */
export function orphanTiles(document: DashboardDocument): number {
  return document.tiles.filter(
    (tile) => tile.tile_type !== 'TEXT' && !tile.connection_ref,
  ).length
}

/**
 * What the saved file is called.
 *
 * Named after the dashboard and dated, because the folder it lands in already
 * holds four other exports and "dashboard.json" is indistinguishable from all
 * of them. Non-filename characters go to hyphens rather than being dropped, so
 * two dashboards whose names differ only in punctuation do not collide.
 */
export function exportFileName(name: string, at: Date = new Date()): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  const stamp = [
    at.getFullYear(),
    String(at.getMonth() + 1).padStart(2, '0'),
    String(at.getDate()).padStart(2, '0'),
  ].join('-')
  return `${slug || 'dashboard'}-${stamp}.json`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
