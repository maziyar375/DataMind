/**
 * Reading a semantic layer file before it is sent, and naming one on the way out.
 *
 * DOM-free and dependency-free, the arrangement `dashboard-document.ts` uses, and
 * for its reason: **every way this can be wrong is quiet.** A reader that accepts
 * the wrong file shows a confident preview of nothing, and a report that words
 * an import as clean when tables did not resolve sends someone to publish a
 * layer half of which describes nothing. `npm run test:layerfile`.
 *
 * The backend checks all of it again (`app/services/semantic_transfer.py`) and
 * trusts none of this. This exists so the import dialog can say what is in the
 * file — where it came from, how much, whether it carries values from the data —
 * before anything is written.
 */

/** The marker a file must carry, and the highest version this build reads. */
export const LAYER_FORMAT = 'datamind.semantic_layer'
export const LAYER_VERSION = 1

/** The fields of a layer file this reads — `SemanticLayerFile` from the API. */
export interface LayerFileLike {
  format: string
  format_version: number
  exported_at?: string | null
  source?: { connection?: string; engine?: string; version?: number | null }
  value_meanings_included?: boolean
  document: {
    business_context?: string
    entities?: {
      table?: string
      columns?: { value_meanings?: Record<string, string> }[]
      metrics?: unknown[]
    }[]
    glossary?: unknown[]
  }
}

export type ParsedLayerFile =
  | { ok: true; file: LayerFileLike; raw: unknown }
  | { ok: false; error: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * The text of a chosen file, as a layer file or as a sentence.
 *
 * In the order a reader loses confidence: is it JSON, is it *this* kind of file,
 * a version this build reads, and does it hold a document at all.
 */
export function parseLayerFile(text: string): ParsedLayerFile {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return { ok: false, error: 'That file is not JSON, so it is not a semantic layer export.' }
  }
  if (!isRecord(raw) || raw.format !== LAYER_FORMAT) {
    return { ok: false, error: 'That file is not a semantic layer export.' }
  }
  const version = raw.format_version
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 1) {
    return { ok: false, error: 'That export does not say which format version it is.' }
  }
  if (version > LAYER_VERSION) {
    return {
      ok: false,
      error: `That export is in format version ${version}; this installation reads version `
        + `${LAYER_VERSION}. Update DataMind, then import it again.`,
    }
  }
  if (!isRecord(raw.document)) {
    return { ok: false, error: 'That export holds no semantic layer document.' }
  }
  const entities = raw.document.entities
  if (entities !== undefined && !Array.isArray(entities)) {
    return { ok: false, error: 'That export’s tables are not a list.' }
  }
  return { ok: true, file: raw as unknown as LayerFileLike, raw }
}

/** What a file holds, counted — the preview before an import. */
export function fileContents(file: LayerFileLike): {
  tables: number
  metrics: number
  terms: number
  valueMeaningColumns: number
} {
  const entities = file.document.entities ?? []
  return {
    tables: entities.length,
    metrics: entities.reduce((n, e) => n + (e.metrics?.length ?? 0), 0),
    terms: file.document.glossary?.length ?? 0,
    valueMeaningColumns: entities.reduce(
      (n, e) => n + (e.columns ?? []).filter((c) => c.value_meanings && Object.keys(c.value_meanings).length > 0).length,
      0,
    ),
  }
}

/** How many columns carry value meanings — what the export checkbox counts. */
export function valueMeaningColumns(
  entities: { columns: { value_meanings?: Record<string, string> }[] }[],
): number {
  return entities.reduce(
    (n, e) => n + e.columns.filter((c) => c.value_meanings && Object.keys(c.value_meanings).length > 0).length,
    0,
  )
}

/** `aurora-coffee-semantic-layer-v12-2026-09-16.json`. */
export function layerFileName(connectionName: string, version: number | null, at: Date = new Date()): string {
  const slug = connectionName
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
  return `${slug || 'connection'}-semantic-layer${version ? `-v${version}` : ''}-${stamp}.json`
}

/** The fields of an import report this reads — `SemanticImportReport`. */
export interface ImportReportLike {
  entities: number
  unresolved: number
  metrics: number
  invalid_metrics: number
  value_meanings_included: boolean
  value_meaning_columns: number
}

/**
 * What an import resolved to, as sentences — problems first.
 *
 * A table this schema does not have came in *flagged*, and says so: publishing
 * it describes nothing, and a report that led with "13 tables imported" would
 * bury that under the good news.
 */
export function importSummary(
  report: ImportReportLike,
): { tone: 'ok' | 'warn'; lines: { text: string; warn: boolean }[] } {
  const lines: { text: string; warn: boolean }[] = []
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
  if (report.unresolved > 0) {
    lines.push({
      text: `${plural(report.unresolved, 'table is', 'tables are')} not in this schema — kept and flagged, and left out of the prompt.`,
      warn: true,
    })
  }
  if (report.invalid_metrics > 0) {
    lines.push({
      text: `${plural(report.invalid_metrics, 'metric does', 'metrics do')} not check out here and ${report.invalid_metrics === 1 ? 'is' : 'are'} flagged.`,
      warn: true,
    })
  }
  lines.push({
    text: `${plural(report.entities, 'table', 'tables')} and ${plural(report.metrics, 'metric', 'metrics')} written to your draft.`,
    warn: false,
  })
  if (report.value_meanings_included) {
    lines.push({
      text: `${plural(report.value_meaning_columns, 'column carries', 'columns carry')} value meanings — codes drawn from the source's data.`,
      warn: false,
    })
  }
  return { tone: lines.some((l) => l.warn) ? 'warn' : 'ok', lines }
}
