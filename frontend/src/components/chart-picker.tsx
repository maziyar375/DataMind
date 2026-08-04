/**
 * Choosing a chart type, in both places it is chosen.
 *
 * A grid of small drawings rather than a `<select>` of nine words. The names
 * are not self-describing at the moment of choosing — "combo" and "heatmap"
 * mean nothing until you have seen one — so the control shows the shape and
 * keeps the word beside it.
 *
 * **The point of this component is the disabling, not the icons.** Until now a
 * type the result could not carry was offered like any other, accepted, and
 * then quietly replaced by the backend with a note saying the pick "does not
 * fit this result" — the user was told after the fact, in the past tense, with
 * no way to know it in advance. Here the backend answers "can this result be a
 * heatmap, and if not why" for every type at once, and an option that cannot
 * work is greyed with its reason on hover. Offer-then-demote becomes
 * cannot-offer, which is the same information delivered before the click
 * instead of after it.
 *
 * The verdicts come from `chart_options` on the backend, where `supported` is
 * computed by asking the real planner — so this grid cannot drift from what the
 * compiler would actually draw.
 */
import type { ChartOption } from '../api/types'

type Glyph = (props: { size?: number }) => JSX.Element

const box = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none' as const,
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  style: { flexShrink: 0 },
})

/**
 * One drawing per type, each the smallest thing that reads as that chart.
 * Deliberately literal — a picker icon is not the place for a metaphor.
 */
const GLYPHS: Record<string, Glyph> = {
  auto: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M12 4l1.6 3.9L17.5 9.5l-3.9 1.6L12 15l-1.6-3.9L6.5 9.5l3.9-1.6L12 4z" />
      <path d="M18 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z" />
    </svg>
  ),
  bar: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <path d="M7 20V11M12 20V5M17 20v-6" />
    </svg>
  ),
  line: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <path d="M5 16l4-5 3.5 3L19 6" />
    </svg>
  ),
  area: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <path d="M5 17l4-5 3.5 3L19 8v9H5z" fill="currentColor" fillOpacity="0.18" />
    </svg>
  ),
  combo: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <path d="M7 20v-5M12 20v-8M17 20v-4" />
      <path d="M5.5 10l5.5-4 6.5 2" />
    </svg>
  ),
  scatter: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <circle cx="8" cy="15" r="1.5" />
      <circle cx="13" cy="9" r="1.5" />
      <circle cx="17.5" cy="13" r="1.5" />
      <circle cx="10" cy="6.5" r="1.5" />
    </svg>
  ),
  pie: ({ size = 20 }) => (
    <svg {...box(size)}>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 12V4M12 12l7 3.5" />
    </svg>
  ),
  heatmap: ({ size = 20 }) => (
    <svg {...box(size)}>
      <rect x="4" y="4" width="6" height="6" rx="1" />
      <rect x="14" y="4" width="6" height="6" rx="1" fill="currentColor" fillOpacity="0.35" />
      <rect x="4" y="14" width="6" height="6" rx="1" fill="currentColor" fillOpacity="0.6" />
      <rect x="14" y="14" width="6" height="6" rx="1" fill="currentColor" fillOpacity="0.15" />
    </svg>
  ),
  histogram: ({ size = 20 }) => (
    <svg {...box(size)}>
      <path d="M4 20h16" />
      <path d="M6 20v-3M9.5 20v-7M13 20V6M16.5 20v-9M20 20v-4" />
    </svg>
  ),
}

/**
 * One glyph on its own, for naming the chart a reader is already looking at.
 * Shares the grid's drawings so the button and the tile beside it cannot end
 * up depicting the same type differently.
 */
export function ChartGlyph({ type, size = 14 }: { type: string; size?: number }) {
  const Glyph = GLYPHS[type]
  return Glyph ? <Glyph size={size} /> : null
}

/** The order the grid runs in — most-reached-for first, not alphabetical. */
const TYPES: { value: string; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'bar', label: 'Bar' },
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'combo', label: 'Bar + line' },
  { value: 'scatter', label: 'Scatter' },
  { value: 'pie', label: 'Pie' },
  { value: 'heatmap', label: 'Heatmap' },
  { value: 'histogram', label: 'Histogram' },
]

export function ChartTypePicker({
  value, options, onChange, includeAuto = true, columns = 5,
}: {
  value: string
  /**
   * Per-type verdicts. An **empty list means "no opinion"** and leaves every
   * type enabled — the preview may simply not have run yet, and a grid that
   * greys everything out because a request is in flight is worse than one that
   * lets the save path answer, which is what happened before this existed.
   */
  options: ChartOption[]
  onChange: (chartType: string) => void
  /** Chat has no "let the platform decide" state: it already did. */
  includeAuto?: boolean
  columns?: number
}) {
  const verdicts = new Map(options.map((o) => [o.chart_type, o]))
  const items = includeAuto ? TYPES : TYPES.filter((t) => t.value !== 'auto')

  return (
    <div
      role="radiogroup"
      aria-label="Chart type"
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        gap: 6,
      }}
    >
      {items.map((item) => {
        // Auto is never judged: it means "decide from whatever comes back",
        // and what comes back is not this preview.
        const verdict = item.value === 'auto' ? undefined : verdicts.get(item.value)
        const disabled = verdict !== undefined && !verdict.supported
        const selected = value === item.value
        const Glyph = GLYPHS[item.value]
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            // The reason is the whole point of disabling rather than hiding:
            // "not available" teaches nothing, "45 categories, a pie reads 6"
            // teaches the reader something about their own data.
            title={disabled ? verdict?.reason ?? undefined : item.label}
            onClick={() => onChange(item.value)}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 4,
              padding: '8px 4px 6px',
              borderRadius: 8,
              border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}`,
              background: selected ? 'var(--accent-bg)' : 'var(--input-bg)',
              color: selected ? 'var(--accent)' : 'var(--text2)',
              cursor: disabled ? 'not-allowed' : 'pointer',
              // Greyed, not hidden. A type that vanishes leaves the reader
              // wondering whether the product has it at all.
              opacity: disabled ? 0.38 : 1,
              transition: 'border-color .12s ease, background .12s ease',
            }}
          >
            {Glyph ? <Glyph /> : null}
            <span style={{ fontSize: 10.5, lineHeight: 1.2, textAlign: 'center' }}>
              {item.label}
            </span>
          </button>
        )
      })}
    </div>
  )
}
