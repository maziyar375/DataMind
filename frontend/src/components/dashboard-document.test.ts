/**
 * The file reader, exercised against files nobody would write on purpose.
 *
 * `npm run test:document` — Node runs this directly (type stripping, no
 * bundler, no test framework, no new dependency), the same bargain
 * `dashboard-schedule.test.ts` makes. The two failure modes being checked are
 * "a confident preview of the wrong file" and "a tile silently pointed at a
 * database the user never chose".
 */
import type { Connection, DashboardDocument, DashboardDocumentConnection } from '../api/types.ts'
import {
  DOCUMENT_FORMAT, DOCUMENT_VERSION, engineMismatches, exportFileName, matchConnections,
  orphanTiles, parseDocument, unmappedRefs,
} from './dashboard-document.ts'

// No `node:assert`, no `process`: keeping Node's types out of this file is what
// lets it sit under `src/` and be type-checked with everything else.
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

const connection = (id: string, name: string, engine = 'postgres'): Connection => ({
  id,
  name,
  database_type: engine,
  host: 'db',
  port: 5432,
  database_name: 'sales',
  username: 'ro',
  ssl_mode: null,
  schema_allowlist: [],
  max_rows: 1000,
  statement_timeout_ms: 30000,
  disclosure_policy: 'SAMPLE',
  semantic_layer_enabled: true,
  clarify_enabled: true,
  include_db_comments: true,
  status: 'READY',
  readonly_confirmed: true,
  server_version: null,
  last_tested_at: null,
  last_synced_at: null,
})

const ref = (
  name: string, engine = 'postgres', id = 'c1',
): DashboardDocumentConnection => ({ ref: id, name, database_type: engine })

const file = (extra: Record<string, unknown> = {}) =>
  JSON.stringify({
    format: DOCUMENT_FORMAT,
    version: DOCUMENT_VERSION,
    dashboard: { name: 'Ops' },
    connections: [ref('sales')],
    tiles: [],
    ...extra,
  })

const documentWith = (tiles: unknown[], connections = [ref('sales')]) =>
  ({
    format: DOCUMENT_FORMAT,
    version: DOCUMENT_VERSION,
    exported_at: '2026-08-13T00:00:00Z',
    dashboard: { name: 'Ops' },
    connections,
    tiles,
  } as unknown as DashboardDocument)

const tile = (overrides: Record<string, unknown> = {}) => ({
  connection_ref: 'c1',
  title: 'Orders',
  tile_type: 'CHART',
  sql: 'SELECT 1',
  ...overrides,
})

// ── reading a file ────────────────────────────────────────────────────────
check(
  'a real export parses',
  parseDocument(file()).ok,
  true,
)
check(
  'a text file is refused before anything is sent',
  parseDocument('not json at all'),
  { ok: false, error: 'That file is not JSON, so it is not a dashboard export.' },
)
check(
  'some other JSON is refused by its missing marker',
  parseDocument('{"name":"Ops","tiles":[]}'),
  { ok: false, error: 'That file is not a dashboard export.' },
)
check(
  'a JSON array is not a document',
  parseDocument('[1,2,3]'),
  { ok: false, error: 'That file is not a dashboard export.' },
)
check(
  'a newer format says to update, not what is wrong with the file',
  parseDocument(file({ version: DOCUMENT_VERSION + 1 })).ok === false
    && (parseDocument(file({ version: DOCUMENT_VERSION + 1 })) as { error: string })
      .error.includes('Update DataMind'),
  true,
)
check(
  'a document with no name is refused',
  parseDocument(file({ dashboard: { name: '  ' } })).ok,
  false,
)
check(
  'a document with no tiles array is refused',
  parseDocument(file({ tiles: undefined })).ok,
  false,
)
// An *empty* dashboard is a real thing to export, and importing one is not an
// error — only a missing array is.
check('an export with no tiles is fine', parseDocument(file({ tiles: [] })).ok, true)

// ── matching connections ──────────────────────────────────────────────────
check(
  'the same name is the same database',
  matchConnections([ref('sales')], [connection('id-1', 'Sales')]),
  { c1: 'id-1' },
)
check(
  'a name nobody has stays unanswered',
  matchConnections([ref('warehouse')], [connection('id-1', 'sales')]),
  { c1: '' },
)
check(
  'a lone connection of the right engine is still not a match',
  matchConnections([ref('warehouse')], [connection('id-1', 'sales', 'postgres')]),
  { c1: '' },
)
check(
  'every ref is answered for, matched or not',
  matchConnections(
    [ref('sales', 'postgres', 'c1'), ref('warehouse', 'mysql', 'c2')],
    [connection('id-1', 'sales')],
  ),
  { c1: 'id-1', c2: '' },
)

// ── what the dialog warns about ───────────────────────────────────────────
check(
  'a different engine under the same tile is reported',
  engineMismatches(
    [ref('sales', 'postgres')],
    { c1: 'id-1' },
    [connection('id-1', 'sakila', 'mysql')],
  ).map((item) => item.chosen.id),
  ['id-1'],
)
check(
  'the same engine is not a warning',
  engineMismatches([ref('sales')], { c1: 'id-1' }, [connection('id-1', 'sales')]),
  [],
)
check(
  'an unanswered ref is not an engine warning as well',
  engineMismatches([ref('sales')], { c1: '' }, [connection('id-1', 'sales')]),
  [],
)

check(
  'a ref only a TEXT tile mentions needs no answer',
  unmappedRefs(documentWith([tile({ tile_type: 'TEXT', connection_ref: null })]), {}),
  [],
)
check(
  'a ref a chart tile needs is asked for',
  unmappedRefs(documentWith([tile()]), {}).map((item) => item.ref),
  ['c1'],
)
check(
  'a ref already answered is not asked for again',
  unmappedRefs(documentWith([tile()]), { c1: 'id-1' }),
  [],
)
check(
  'a tile whose connection was already gone is counted, not mapped',
  orphanTiles(documentWith([tile({ connection_ref: null }), tile()])),
  1,
)

// ── the saved file ────────────────────────────────────────────────────────
check(
  'the file is named after the dashboard and dated',
  exportFileName('Revenue overview', new Date(2026, 7, 13)),
  'revenue-overview-2026-08-13.json',
)
check(
  'punctuation becomes separators rather than disappearing',
  exportFileName('Q3 / EMEA — sales', new Date(2026, 0, 5)),
  'q3-emea-sales-2026-01-05.json',
)
check(
  'a non-Latin name survives instead of becoming an empty slug',
  exportFileName('فروش ماهانه', new Date(2026, 7, 13)),
  'فروش-ماهانه-2026-08-13.json',
)
check(
  'a name of pure punctuation still yields a filename',
  exportFileName('///', new Date(2026, 7, 13)),
  'dashboard-2026-08-13.json',
)

console.log(failures === 0 ? '\nall passed' : `\n${failures} failing`)
if (failures > 0) throw new Error(`${failures} check(s) failed`)
