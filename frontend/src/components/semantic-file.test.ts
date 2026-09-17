/**
 * Reading a semantic layer file, and saying what an import did.
 *
 * `npm run test:layerfile` — Node runs this file directly, the arrangement
 * `dashboard-document.test.ts` uses. The cases that matter are the quiet ones:
 * the wrong file accepted, and an import with unresolved tables worded as clean.
 */
import {
  fileContents, importSummary, layerFileName, parseLayerFile, valueMeaningColumns,
} from './semantic-file.ts'

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

const file = (extra: Record<string, unknown> = {}) => JSON.stringify({
  format: 'datamind.semantic_layer',
  format_version: 1,
  source: { connection: 'Aurora Coffee', engine: 'postgres', version: 3 },
  document: {
    entities: [
      { table: 'public.orders', columns: [{ value_meanings: { C: 'cancelled' } }, {}], metrics: [{}, {}] },
      { table: 'public.stores', columns: [], metrics: [{}] },
    ],
    glossary: [{}],
  },
  ...extra,
})

// ── reading a file ────────────────────────────────────────────────────────
check('a layer export is read', parseLayerFile(file()).ok, true)
check('not JSON', parseLayerFile('{nope').ok === false && (parseLayerFile('{nope') as { error: string }).error.includes('not JSON'), true)
check('a dashboard export is not a layer export',
      (parseLayerFile(file({ format: 'datamind.dashboard' })) as { error: string }).error,
      'That file is not a semantic layer export.')
check('a newer format version says to update',
      (parseLayerFile(file({ format_version: 2 })) as { error: string }).error.includes('Update DataMind'), true)
check('no version is refused',
      (parseLayerFile(file({ format_version: undefined })) as { ok: boolean }).ok, false)
check('no document is refused',
      (parseLayerFile(file({ document: 'x' })) as { error: string }).error,
      'That export holds no semantic layer document.')
check('tables that are not a list are refused',
      (parseLayerFile(file({ document: { entities: 'orders' } })) as { ok: boolean }).ok, false)

// ── what is in it ─────────────────────────────────────────────────────────
const parsed = parseLayerFile(file())
check('the preview counts tables, metrics, terms and value-meaning columns',
      parsed.ok ? fileContents(parsed.file) : null,
      { tables: 2, metrics: 3, terms: 1, valueMeaningColumns: 1 })
check('the export checkbox counts columns with meanings, not meanings',
      valueMeaningColumns([{ columns: [{ value_meanings: { a: '1', b: '2' } }, { value_meanings: {} }, {}] }]), 1)

// ── naming a file ─────────────────────────────────────────────────────────
const day = new Date(2026, 8, 16)
check('a file is named for its connection, version and day',
      layerFileName('Aurora Coffee', 12, day), 'aurora-coffee-semantic-layer-v12-2026-09-16.json')
check('a Persian name keeps its letters', layerFileName('فروش ۱۴۰۵', 1, day), 'فروش-۱۴۰۵-semantic-layer-v1-2026-09-16.json')
check('an empty name still names the file', layerFileName('  ', null, day), 'connection-semantic-layer-2026-09-16.json')

// ── what an import did ────────────────────────────────────────────────────
check('a clean import says what was written',
      importSummary({ entities: 13, unresolved: 0, metrics: 34, invalid_metrics: 0, value_meanings_included: false, value_meaning_columns: 0 }),
      { tone: 'ok', lines: [{ text: '13 tables and 34 metrics written to your draft.', warn: false }] })
check('unresolved tables lead, and warn',
      importSummary({ entities: 3, unresolved: 1, metrics: 2, invalid_metrics: 1, value_meanings_included: true, value_meaning_columns: 2 }),
      {
        tone: 'warn',
        lines: [
          { text: '1 table is not in this schema — kept and flagged, and left out of the prompt.', warn: true },
          { text: '1 metric does not check out here and is flagged.', warn: true },
          { text: '3 tables and 2 metrics written to your draft.', warn: false },
          { text: '2 columns carry value meanings — codes drawn from the source\'s data.', warn: false },
        ],
      })

console.log(failures === 0 ? '\nall layer file checks passed' : `\n${failures} failed`)
// A throw rather than `process.exit`: the same non-zero exit for npm, and no
// `@types/node` for a file the app's own tsconfig type-checks.
if (failures > 0) throw new Error(`${failures} test(s) failed`)
