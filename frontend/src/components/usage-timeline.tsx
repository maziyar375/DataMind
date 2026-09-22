/**
 * Tokens over time: one stacked column per bucket, input under output, from
 * the window's start to its end — which, for a period that ends now, is now.
 *
 * **The input segment is subdivided, never added to.** A provider reports
 * cached tokens as the part of the prompt it did not have to read again, so
 * the two cache counts live *inside* `input`; stacking them on top would draw
 * a column taller than the tokens it stands for. `stackSegments` does that
 * arithmetic and is tested; a column's height is identical to what it was
 * before caching was measured at all.
 *
 * Drawn as plain SVG rather than through `VegaChart`, and the reason is the
 * axis. The usage response is sparse and its buckets are variable-width (five
 * minutes to a day, chosen by the server), and this chart has to lay every
 * bucket of the window including the empty ones, mark the one in progress,
 * and answer a hover or an arrow key with the bucket's exact figures. Every
 * number it draws comes from `usage-chart.ts`, which is tested; what is here
 * is geometry and events.
 *
 * Mark specs are the chart system's: columns at most 24px wide with a 4px
 * rounded data end and a square baseline, a 2px surface gap between the two
 * segments, hairline solid gridlines, and colours from `palette.ts`'s first
 * two categorical slots — never from `var(--accent)`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { PALETTES } from './palette.ts'
import { useThemeName } from './theme-name.ts'
import {
  formatCompact, formatSlot, formatTokens, stackSegments, timeTicks, valueTicks,
  type Segment, type Slot,
} from './usage-chart.ts'

const MARGIN = { top: 14, right: 6, bottom: 30, left: 46 }
const GAP = 2
const MAX_BAR = 24

/**
 * The four series, in stack order, with the palette slot each one paints from.
 *
 * Bottom to top: what the provider served from cache, what it charged to write
 * into the cache, the input it actually read, and the output. The first three
 * are **one bar between them** — they are the parts of `prompt_tokens`, not
 * three things added to it.
 *
 * `input` keeps slot 0 and `output` slot 1, so a screen that has never seen a
 * caching provider looks exactly as it did. The cache pair take the next two
 * slots rather than tints of slot 0: a tint says *less important*, and a
 * cached token is the same token at a different price.
 */
export const SEGMENTS = [
  { key: 'cacheRead', label: 'From cache', slot: 2 },
  { key: 'cacheWrite', label: 'Written to cache', slot: 3 },
  { key: 'input', label: 'Input', slot: 0 },
  { key: 'output', label: 'Output', slot: 1 },
] as const

export type SegmentColors = Record<Segment['key'], string>

/** Each series' colour for the theme in force. */
export function useSegmentColors(): SegmentColors {
  const theme = useThemeName()
  const palette = PALETTES[theme]
  return {
    input: palette.category[0],
    output: palette.category[1],
    cacheRead: palette.category[2],
    cacheWrite: palette.category[3],
  }
}

/**
 * Which series these columns actually contain, in stack order.
 *
 * The legend is drawn from this rather than from `SEGMENTS`, because most
 * providers report no caching at all and a legend naming two colours that
 * never appear teaches a distinction the chart is not making.
 */
export function presentSegments(slots: readonly Slot[]): typeof SEGMENTS[number][] {
  const seen = new Set<Segment['key']>()
  for (const slot of slots) for (const segment of stackSegments(slot)) seen.add(segment.key)
  return SEGMENTS.filter((segment) => seen.has(segment.key))
}

/** A column segment with its top corners rounded and its base square. */
function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.max(0, Math.min(r, w / 2, h))
  return [
    `M${x},${y + h}`,
    `L${x},${y + radius}`,
    `Q${x},${y} ${x + radius},${y}`,
    `L${x + w - radius},${y}`,
    `Q${x + w},${y} ${x + w},${y + radius}`,
    `L${x + w},${y + h}`,
    'Z',
  ].join(' ')
}

export function UsageTimeline({
  slots, bucketSeconds, offsetMinutes, endsNow, height = 248, label,
}: {
  slots: Slot[]
  bucketSeconds: number
  offsetMinutes: number
  /** The window ends now, so the last column is the bucket in progress. */
  endsNow: boolean
  height?: number
  /** What the chart shows, for a screen reader: "Tokens over time, all models". */
  label: string
}) {
  const theme = useThemeName()
  const palette = PALETTES[theme]
  const colors: SegmentColors = {
    input: palette.category[0],
    output: palette.category[1],
    cacheRead: palette.category[2],
    cacheWrite: palette.category[3],
  }

  const boxRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [active, setActive] = useState<number | null>(null)

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const measure = () => setWidth(Math.floor(box.getBoundingClientRect().width))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(box)
    return () => observer.disconnect()
  }, [])

  // A new window is a new set of columns; an index into the old one means
  // nothing in it.
  useEffect(() => setActive(null), [slots])

  const plotW = Math.max(0, width - MARGIN.left - MARGIN.right)
  const plotH = height - MARGIN.top - MARGIN.bottom
  const baseline = MARGIN.top + plotH
  const count = slots.length
  const slotW = count > 0 ? plotW / count : 0
  const barW = slotW >= 4 ? Math.min(MAX_BAR, Math.max(2, slotW * 0.62)) : Math.max(1, slotW - 1)

  const scale = useMemo(
    () => valueTicks(slots.reduce((max, s) => Math.max(max, s.input + s.output), 0)),
    [slots],
  )

  const start = slots[0]?.start ?? 0
  const end = slots[count - 1]?.end ?? 0
  const xAt = (ms: number) => MARGIN.left + ((ms - start) / Math.max(1, end - start)) * plotW
  const yAt = (value: number) => baseline - (value / scale.max) * plotH

  // Leave the right end to "Now" when there is one: a centred tick label
  // reaches about 20px either side of its tick, and "Now" is ~30px wide.
  const nowReserve = endsNow ? 60 : 24
  const ticks = useMemo(
    () => (count > 0 && plotW > 0
      ? timeTicks(start, end, offsetMinutes, Math.max(2, Math.floor(plotW / 86)))
      : []),
    [count, plotW, start, end, offsetMinutes],
  ).filter((tick) => xAt(tick.at) <= MARGIN.left + plotW - nowReserve)

  const indexAt = (clientX: number): number | null => {
    const box = boxRef.current
    if (!box || count === 0) return null
    const x = clientX - box.getBoundingClientRect().left - MARGIN.left
    if (x < 0 || x > plotW) return null
    return Math.min(count - 1, Math.max(0, Math.floor(x / slotW)))
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (count === 0) return
    const current = active ?? count - 1
    const next =
      event.key === 'ArrowLeft' ? Math.max(0, current - 1)
      : event.key === 'ArrowRight' ? Math.min(count - 1, current + 1)
      : event.key === 'Home' ? 0
      : event.key === 'End' ? count - 1
      : event.key === 'Escape' ? null
      : undefined
    if (next === undefined) return
    event.preventDefault()
    setActive(next)
  }

  const activeSlot = active !== null ? slots[active] : null
  const inProgress = endsNow && active === count - 1
  const readout = activeSlot
    ? `${formatSlot(activeSlot.start, bucketSeconds, offsetMinutes)}${inProgress ? ', in progress' : ''}: `
      + (activeSlot.runs > 0
        ? `${formatTokens(activeSlot.input + activeSlot.output)} tokens, `
          + `${formatTokens(activeSlot.input)} input, ${formatTokens(activeSlot.output)} output, `
          + `${activeSlot.runs} ${activeSlot.runs === 1 ? 'operation' : 'operations'}`
        : 'no usage')
    : ''

  const tipLeft = activeSlot ? MARGIN.left + (active! + 0.5) * slotW : 0
  const tipOnLeft = tipLeft > width / 2

  return (
    <div
      ref={boxRef}
      className="rm-usage-timeline"
      tabIndex={count > 0 ? 0 : -1}
      role="group"
      aria-label={`${label}. Use the left and right arrow keys to read each bar.`}
      onKeyDown={onKeyDown}
      onFocus={() => setActive((current) => current ?? (count > 0 ? count - 1 : null))}
      onBlur={() => setActive(null)}
      style={{ position: 'relative', height, width: '100%' }}
    >
      {width > 0 && (
        <svg
          width={width}
          height={height}
          aria-hidden="true"
          style={{ display: 'block', touchAction: 'pan-y' }}
          onPointerMove={(event) => setActive(indexAt(event.clientX))}
          onPointerDown={(event) => setActive(indexAt(event.clientX))}
          onPointerLeave={() => {
            if (document.activeElement !== boxRef.current) setActive(null)
          }}
        >
          {/* The bucket in progress: a quiet band, so the last column reads as
              "so far" rather than as a finished bar that came in low. */}
          {endsNow && count > 0 && (
            <rect
              x={MARGIN.left + (count - 1) * slotW}
              y={MARGIN.top}
              width={slotW}
              height={plotH}
              fill={palette.text}
              opacity={0.035}
            />
          )}

          {activeSlot && (
            <rect
              x={MARGIN.left + active! * slotW}
              y={MARGIN.top}
              width={slotW}
              height={plotH}
              fill={palette.text}
              opacity={0.07}
            />
          )}

          {scale.ticks.map((value) => {
            const y = Math.round(yAt(value)) + 0.5
            return (
              <g key={value}>
                <line
                  x1={MARGIN.left}
                  x2={MARGIN.left + plotW}
                  y1={y}
                  y2={y}
                  stroke={palette.grid}
                  strokeWidth={1}
                />
                <text
                  x={MARGIN.left - 10}
                  y={y}
                  dy="0.32em"
                  textAnchor="end"
                  fontSize={11}
                  fill={palette.dim}
                  style={{ fontVariantNumeric: 'tabular-nums' }}
                >
                  {formatCompact(value)}
                </text>
              </g>
            )
          })}

          {slots.map((slot, index) => {
            const total = slot.input + slot.output
            if (total <= 0) return null
            const pieces = stackSegments(slot)
            const x = MARGIN.left + index * slotW + (slotW - barW) / 2
            const fullH = Math.max(1, (total / scale.max) * plotH)
            const dim = activeSlot !== null && active !== index
            // Laid bottom-up from the baseline, so the pieces of `input` sit
            // under `output` in `stackSegments`' order and the column's height
            // is still `input + output` — the cache pieces subdivide the input
            // rather than adding to it.
            let bottom = baseline
            return (
              <g key={slot.start} opacity={dim ? 0.55 : 1}>
                {pieces.map((piece, at) => {
                  const h = (piece.tokens / total) * fullH
                  const top = bottom - h
                  const last = at === pieces.length - 1
                  // The surface gap goes between input and output only: the
                  // three parts of the input bar are one bar, and gaps inside
                  // it would read as three separate measurements.
                  const gap = last && pieces.length > 1 && fullH > GAP + 2 ? GAP : 0
                  bottom = top
                  return last
                    ? (
                      <path
                        key={piece.key}
                        d={roundedTop(x, top + gap, barW, Math.max(1, h - gap), 4)}
                        fill={colors[piece.key]}
                      />
                    )
                    : (
                      <rect
                        key={piece.key}
                        x={x}
                        y={top}
                        width={barW}
                        height={Math.max(1, h)}
                        fill={colors[piece.key]}
                      />
                    )
                })}
              </g>
            )
          })}

          <line
            x1={MARGIN.left}
            x2={MARGIN.left + plotW}
            y1={baseline + 0.5}
            y2={baseline + 0.5}
            stroke={palette.dim}
            strokeOpacity={0.5}
            strokeWidth={1}
          />

          {ticks.map((tick) => {
            const x = xAt(tick.at)
            const anchor = x < MARGIN.left + 18 ? 'start' : 'middle'
            return (
              <g key={tick.at}>
                <line x1={x} x2={x} y1={baseline} y2={baseline + 4} stroke={palette.dim} strokeOpacity={0.5} />
                <text x={x} y={height - 8} textAnchor={anchor} fontSize={11} fill={palette.dim}>
                  {tick.label}
                </text>
              </g>
            )
          })}

          {endsNow && count > 0 && (
            <g>
              <line
                x1={MARGIN.left + plotW - 0.5}
                x2={MARGIN.left + plotW - 0.5}
                y1={baseline}
                y2={baseline + 4}
                stroke={palette.text}
              />
              <text
                x={MARGIN.left + plotW}
                y={height - 8}
                textAnchor="end"
                fontSize={11}
                fontWeight={600}
                fill={palette.text}
              >
                Now
              </text>
            </g>
          )}
        </svg>
      )}

      {activeSlot && (
        <div
          className="rm-usage-tip"
          style={{
            left: tipOnLeft ? undefined : Math.min(tipLeft + barW / 2 + 10, Math.max(0, width - 196)),
            right: tipOnLeft ? Math.min(width - tipLeft + barW / 2 + 10, Math.max(0, width - 196)) : undefined,
            top: MARGIN.top,
          }}
        >
          <div className="rm-usage-tip-when">
            {formatSlot(activeSlot.start, bucketSeconds, offsetMinutes)}
            {inProgress && <span> · so far</span>}
          </div>
          {activeSlot.runs > 0 ? (
            <>
              <div className="rm-usage-tip-total">
                {formatTokens(activeSlot.input + activeSlot.output)}
                <span> tokens</span>
              </div>
              {/* The pieces the column is actually drawn from, so the tip and
                  the bar cannot disagree — and so the cached part is named
                  rather than folded silently into the input figure. */}
              {stackSegments(activeSlot).map((piece) => (
                <div key={piece.key} className="rm-usage-tip-row">
                  <span className="rm-usage-tip-key" style={{ background: colors[piece.key] }} />
                  <span>{piece.label}</span>
                  <strong>{formatTokens(piece.tokens)}</strong>
                </div>
              ))}
              <div className="rm-usage-tip-row">
                <span className="rm-usage-tip-key is-blank" />
                <span>Operations</span>
                <strong>{activeSlot.runs}</strong>
              </div>
            </>
          ) : (
            <div className="rm-usage-tip-empty">No usage</div>
          )}
        </div>
      )}

      <div className="rm-sr-only" aria-live="polite">{readout}</div>
    </div>
  )
}

/**
 * The chart's table twin: the same buckets as rows, newest first.
 *
 * Only buckets something ran in — ninety rows of zeros would bury the eight
 * that matter, and the chart beside it already shows where the quiet was.
 */
export function UsageSlotTable({
  slots, bucketSeconds, offsetMinutes, endsNow,
}: {
  slots: Slot[]
  bucketSeconds: number
  offsetMinutes: number
  endsNow: boolean
}) {
  const rows = slots
    .map((slot, index) => ({ slot, last: index === slots.length - 1 }))
    .filter(({ slot }) => slot.runs > 0)
    .reverse()

  if (rows.length === 0) {
    return <p className="rm-usage-quiet">Nothing ran in this period.</p>
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="rm-usage-table">
        <thead>
          <tr>
            <th style={{ textAlign: 'start' }}>When</th>
            <th>Input</th>
            <th>Output</th>
            <th>Total</th>
            <th>Operations</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ slot, last }) => (
            <tr key={slot.start}>
              <td style={{ textAlign: 'start' }}>
                {formatSlot(slot.start, bucketSeconds, offsetMinutes)}
                {endsNow && last && <span className="rm-usage-quiet"> · so far</span>}
              </td>
              <td>{formatTokens(slot.input)}</td>
              <td>{formatTokens(slot.output)}</td>
              <td>{formatTokens(slot.input + slot.output)}</td>
              <td>{slot.runs}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="rm-usage-quiet" style={{ marginTop: 8 }}>
        Periods with no usage are left out.
      </p>
    </div>
  )
}
