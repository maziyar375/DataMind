/**
 * How a configured table turns a result into cells — the whole rule, as pure
 * functions.
 *
 * Split out of `ui.tsx` for the same reason `dashboard-schedule.ts` is split
 * out of `dashboard.tsx`: no React, no DOM, so it can be read and exercised on
 * its own (`npm run test:format`). The failure modes here are quiet ones — a
 * column the query added silently missing from the tile, a sort that puts
 * nulls first, a format that turns a value into `NaN` — and none of them throw.
 *
 * **Hiding a column hides it, it does not withhold it.** Everything here runs
 * in the browser on rows it already has. Anything that must not reach the
 * owner's browser belongs to the connection's disclosure policy or to the SQL.
 */

export interface ResultTableColumnConfig {
  name: string
  hidden?: boolean
  /** null keeps the column's own name; '' is a deliberate blank heading. */
  label?: string | null
  align?: 'auto' | 'left' | 'right' | 'center'
  format?: 'auto' | 'integer' | 'decimal' | 'percent' | 'text'
}

export interface ResultTableConfig {
  columns?: ResultTableColumnConfig[]
  sort_column?: string | null
  sort_direction?: 'asc' | 'desc'
}

export type CellFormat = NonNullable<ResultTableColumnConfig['format']>

export interface TableColumns {
  columns: { name: string; db_type?: string; semantic_type: string }[]
}

export interface ResolvedColumn {
  /** Where the value sits in each row — a config reorders columns, not rows. */
  index: number
  name: string
  heading: string
  numeric: boolean
  align: 'left' | 'right' | 'center'
  format: CellFormat
}

/**
 * The column list the table actually draws.
 *
 * A configured column the result no longer has is dropped, and a result column
 * the config never mentioned is **appended, visible** — a query that gains a
 * column must not silently lose it from the tile. With no config this returns
 * the result's own columns in their own order, which is why the chat
 * transcript renders exactly as it did before any of this existed.
 */
export function resolveColumns(
  spec: TableColumns,
  config?: ResultTableConfig | null,
): ResolvedColumn[] {
  const index = new Map(spec.columns.map((column, i) => [column.name, i]))
  const resolve = (i: number, entry?: ResultTableColumnConfig): ResolvedColumn => {
    const column = spec.columns[i]
    const numeric = column.semantic_type === 'quantitative'
    const align =
      entry?.align && entry.align !== 'auto' ? entry.align : numeric ? 'right' : 'left'
    return {
      index: i,
      name: column.name,
      // An empty label is a deliberate blank heading, so only null/undefined
      // falls back to the column's own name.
      heading: entry?.label ?? column.name,
      numeric,
      align,
      format: entry?.format ?? 'auto',
    }
  }

  if (!config?.columns?.length) return spec.columns.map((_column, i) => resolve(i))

  const named = new Set<string>()
  const ordered: ResolvedColumn[] = []
  for (const entry of config.columns) {
    const i = index.get(entry.name)
    if (i === undefined) continue
    named.add(entry.name)
    if (entry.hidden) continue
    ordered.push(resolve(i, entry))
  }
  spec.columns.forEach((column, i) => {
    if (!named.has(column.name)) ordered.push(resolve(i))
  })
  return ordered
}

/**
 * The rows in the configured order.
 *
 * Nulls sort last in **both** directions: "no value" is not the smallest
 * value, and a descending sort whose first screen is all blanks is useless.
 * The array is copied — the caller's result is shared with the chart and the
 * cache, and sorting it in place would reorder both.
 */
export function sortRows(
  rows: unknown[][],
  spec: TableColumns,
  config?: ResultTableConfig | null,
): unknown[][] {
  const name = config?.sort_column
  if (!name) return rows
  const at = spec.columns.findIndex((column) => column.name === name)
  if (at < 0) return rows
  const sign = config?.sort_direction === 'desc' ? -1 : 1

  return [...rows].sort((left, right) => {
    const a = left[at]
    const b = right[at]
    const aNull = a === null || a === undefined
    const bNull = b === null || b === undefined
    if (aNull || bNull) return aNull && bNull ? 0 : aNull ? 1 : -1
    if (typeof a === 'number' && typeof b === 'number') return (a - b) * sign
    return String(a).localeCompare(String(b), undefined, { numeric: true }) * sign
  })
}

/**
 * One cell.
 *
 * `auto` is what the table did before any of this was configurable, and every
 * caller without a config still gets exactly it. The named number formats
 * apply only to values that *are* numbers — asking for `decimal` on a text
 * column shows the text rather than `NaN`, because a formatting choice should
 * never destroy a value.
 */
export function formatCell(value: unknown, format: CellFormat = 'auto'): string {
  if (value === null || value === undefined) return '—'
  if (format === 'text') return String(value)

  const numeric =
    typeof value === 'number'
      ? value
      : format !== 'auto' && value !== '' && Number.isFinite(Number(value))
        ? Number(value)
        : null

  if (numeric === null) return String(value)

  switch (format) {
    case 'integer':
      return Math.round(numeric).toLocaleString()
    case 'decimal':
      return numeric.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    // A fraction, the way a database stores a rate: 0.42 reads as 42%.
    case 'percent':
      return `${(numeric * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
    default:
      return Number.isInteger(numeric)
        ? numeric.toLocaleString()
        : numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
}
