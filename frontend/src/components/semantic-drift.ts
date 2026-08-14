/**
 * Why every row in the semantic layer turned red at once.
 *
 * `npm run test:drift` — DOM-free and framework-free, the arrangement
 * `report-readiness.ts` uses and for the same reason: the answer is worth
 * getting right, and the failure mode is a user staring at forty invalid
 * entities with no idea which single thing to change.
 *
 * The layer flags entities against the *current* snapshot, so a table that has
 * been dropped is one red row and that reads correctly. But a whole layer can
 * be invalidated by one edit to the connection, and then the per-row flags are
 * technically right and completely unhelpful — forty rows each saying "not in
 * the current schema snapshot" when the sentence a person needs is "you are
 * pointed at a different schema than the one this was written against".
 *
 * **Oracle is why this exists.** There a schema *is* a database user, and the
 * allowlist defaults to the connecting user's own schema — so changing the
 * connection's username re-keys every qualified name in the snapshot at once
 * (`HR.EMPLOYEES` becomes `SCOTT.EMPLOYEES`), and every entity in the layer
 * misses. The same shape is reachable on the other engines by editing the
 * schema allowlist or renaming a schema, so the detection is engine-neutral
 * and only the explanation names Oracle.
 *
 * The test is deliberately all-or-nothing: a re-key invalidates *everything*,
 * and anything short of that is ordinary drift the "schema has been re-synced"
 * note already covers. A message this specific has to be right or it teaches
 * the user to ignore the next one.
 */

/** The entity fields this reads — a subset of `SemanticEntity`. */
export interface DriftEntity {
  table: string
  valid: boolean
}

/** The snapshot fields this reads — a subset of `SemanticTableFact`. */
export interface DriftTable {
  table: string
}

export interface RekeyDrift {
  /** How many entities the current snapshot no longer contains. */
  missing: number
  /** The schema prefixes the layer is keyed to, sorted. */
  was: string[]
  /** The schema prefixes the snapshot now carries, sorted. */
  now: string[]
}

function schemaOf(qualified: string): string {
  const at = qualified.lastIndexOf('.')
  return at <= 0 ? '' : qualified.slice(0, at).toLowerCase()
}

function schemasOf(names: string[]): string[] {
  const out = new Set<string>()
  for (const name of names) {
    const schema = schemaOf(name)
    if (schema) out.add(schema)
  }
  return [...out].sort()
}

/**
 * `null` unless the layer as a whole is keyed to schemas the snapshot no
 * longer has — every entity invalid, and not one shared schema name between
 * the two. A layer with a single valid entity is not re-keyed, and neither is
 * one whose schemas still overlap: both of those are drift, not a redirect.
 */
export function rekeyDrift(
  entities: DriftEntity[],
  tables: DriftTable[],
): RekeyDrift | null {
  if (entities.length === 0 || tables.length === 0) return null
  if (entities.some((entity) => entity.valid)) return null

  const was = schemasOf(entities.map((entity) => entity.table))
  const now = schemasOf(tables.map((table) => table.table))
  if (was.length === 0 || now.length === 0) return null
  if (was.some((schema) => now.includes(schema))) return null

  return { missing: entities.length, was, now }
}

function list(names: string[]): string {
  if (names.length <= 2) return names.join(' and ')
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/**
 * The sentence to show instead of the forty red rows. `dialect` is the
 * snapshot's, not the connection's display name — on Oracle the cause is
 * nearly always the connection's username, and saying so is the whole point of
 * the message.
 */
export function explainRekey(drift: RekeyDrift, dialect: string): string {
  const head =
    `None of this layer's ${drift.missing} tables are in the current schema ` +
    `snapshot: it was written against ${list(drift.was)}, and the last sync ` +
    `returned ${list(drift.now)}.`
  const cause =
    dialect === 'oracle'
      ? ' On Oracle a schema is a database user, and this connection reads the' +
        ' schema it connects as — so this is usually a changed username on the' +
        ' connection rather than dropped tables.'
      : " It usually means the connection's schema allowlist changed, or the" +
        ' schemas were renamed.'
  const fix =
    ` Point the connection back at ${list(drift.was)} and re-sync to get the` +
    ' layer back, or regenerate it against the new schema — nothing is deleted' +
    ' either way, and flagged entries never reach the model.'
  return head + cause + fix
}
