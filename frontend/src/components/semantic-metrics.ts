/**
 * Every measure a database defines, read as one list instead of forty.
 *
 * `npm run test:metrics` — DOM-free and framework-free, the arrangement
 * `semantic-drift.ts` uses and for the same reason: the answer decides what a
 * curator sees, and getting it wrong is worse than not showing it.
 *
 * **Why a list exists at all when the document is a tree.** A metric is stored
 * on the entity it measures, and that is correct — an aggregate needs a grain,
 * a set of columns and a validator that can resolve them, which is exactly
 * what an entity carries. Every product that has this feature anchors the
 * definition somewhere: a dataset in Superset, a home table in Power BI, a
 * source in a Databricks metric view.
 *
 * But *browsing* per table is a different question from *defining* per table,
 * and the tree answers only the second. Two things go missing in it:
 *
 * * **What this database measures.** It is the question a new analyst asks
 *   first, and the tree makes it a walk through forty expandable cards.
 * * **Whether a name means one thing.** `revenue` on `orders` and `revenue` on
 *   `invoices` are each perfectly valid, sit two screens apart, and are
 *   invisible to each other. The server refuses both once it validates
 *   (`_refuse_ambiguous_metrics`); this file is how the same collision is
 *   visible while it is still being typed.
 *
 * `required_joins` is the tell that the tree was never the whole truth: a
 * metric can already reach through joins into other tables, so the entity it
 * hangs off is partly a filing decision. The list is the other reading of the
 * same document — no second copy, no new storage, no migration.
 */

/** The metric fields this reads — a subset of `SemanticMetric`. */
export interface MetricLike {
  name: string
  label?: string
  description?: string
  expression: string
  filters?: string[]
  required_joins?: string[]
  unit?: string
  valid?: boolean
  issue?: string
}

/** The entity fields this reads — a subset of `SemanticEntity`. */
export interface MetricHost {
  table: string
  label?: string
  exclude?: boolean
  metrics: MetricLike[]
}

/** One metric, and where it is defined. */
export interface MetricRow extends MetricLike {
  /** The qualified table the definition hangs off. */
  table: string
  /** Its position in that entity's own `metrics`, so an edit can be routed
   *  back to the exact element rather than matched by name — which is the one
   *  field a duplicate makes unreliable. */
  index: number
  /** The entity is excluded from the prompt, so this measures nothing today.
   *  Shown rather than hidden: a metric that silently does nothing is worse
   *  than one labelled as set aside. */
  excluded: boolean
  /** Another *live* entity claims this name. Computed here so a collision is
   *  visible while it is typed, not only after the next save. */
  ambiguous: boolean
}

/** Case- and whitespace-insensitive, because that is how a collision reads to
 *  a person. `Revenue` and `revenue ` are the same claim on the same word. */
function key(name: string): string {
  return name.trim().toLowerCase()
}

/**
 * Names claimed by more than one live entity.
 *
 * Excluded entities are not counted: they are out of the prompt entirely, so a
 * name one of them uses is not contested — setting a staging copy aside must
 * not take the real metric down with it. A name repeated *within* one entity
 * counts, because that is a collision too and a worse one.
 */
export function ambiguousNames(entities: MetricHost[]): Set<string> {
  const seen = new Map<string, number>()
  for (const entity of entities) {
    if (entity.exclude) continue
    for (const metric of entity.metrics) {
      const name = key(metric.name)
      if (name) seen.set(name, (seen.get(name) ?? 0) + 1)
    }
  }
  return new Set([...seen].filter(([, n]) => n > 1).map(([name]) => name))
}

/**
 * Every metric in the document, in the order a reader wants them.
 *
 * By name, then by table — so the two `revenue` definitions land next to each
 * other rather than forty rows apart, which is the whole point of reading the
 * document this way. Unnamed drafts sort last: a blank row is a metric
 * somebody is still writing, not the first thing to look at.
 */
export function collectMetrics(entities: MetricHost[]): MetricRow[] {
  const ambiguous = ambiguousNames(entities)
  const rows: MetricRow[] = []
  for (const entity of entities) {
    entity.metrics.forEach((metric, index) => {
      rows.push({
        ...metric,
        table: entity.table,
        index,
        excluded: !!entity.exclude,
        ambiguous: !entity.exclude && ambiguous.has(key(metric.name)),
      })
    })
  }
  return rows.sort((a, b) => {
    const an = key(a.name)
    const bn = key(b.name)
    if (!an !== !bn) return an ? -1 : 1
    return an === bn ? a.table.localeCompare(b.table) : an.localeCompare(bn)
  })
}

/** Case-insensitive search over what a metric actually says. */
export function matchesMetric(row: MetricRow, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return [row.name, row.label ?? '', row.description ?? '', row.expression, row.table]
    .join(' ')
    .toLowerCase()
    .includes(needle)
}

/**
 * The one line the panel shows while it is closed.
 *
 * A count and the first few names, in the shape the glossary already uses —
 * and the problems first when there are any, because a summary that reads
 * "12 metrics" over two broken ones is a summary that hid the news.
 */
export function metricSummary(rows: MetricRow[]): string {
  if (rows.length === 0) return 'No metrics yet'
  const broken = rows.filter((r) => r.valid === false || r.ambiguous).length
  const named = rows.map((r) => r.name.trim()).filter(Boolean)
  const head = `${rows.length} ${rows.length === 1 ? 'metric' : 'metrics'}`
  if (broken > 0) {
    return `${head} — ${broken} need${broken === 1 ? 's' : ''} attention`
  }
  return named.length === 0
    ? head
    : `${head} — ${named.slice(0, 6).join(', ')}${named.length > 6 ? '…' : ''}`
}
