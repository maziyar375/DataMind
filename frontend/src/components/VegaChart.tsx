/**
 * Renders a backend-produced Vega-Lite spec with vega-embed.
 *
 * The spec's data and encodings are chosen by the agent (the `chart` pipeline
 * node); this component only paints it. Colours are hex rather than the app's
 * oklch CSS variables because Vega/D3 cannot parse `oklch()` and would fall
 * back to black — so the values below are the *same* theme colours, converted
 * once. A MutationObserver re-renders the chart when the theme is toggled.
 *
 * ── The categorical palette ──────────────────────────────────────────────
 *
 * Eight hues spaced evenly around the wheel from the app's own accent, so the
 * whole set is derived from the brand rather than bolted on beside it. Slot 1
 * is that accent verbatim (`--accent`, OKLCH 0.52 0.19 315): a single-series
 * chart paints with slot 1, which is most charts, so it is the colour the
 * product actually reads as. Dark is the same eight hues re-stepped for the
 * dark surface — not a lightened copy — so a series keeps its hue when the
 * user flips the theme.
 *
 * The order is the colourblind-safety mechanism and is NOT cosmetic: it was
 * chosen to maximise the worst separation, then each step was moved toward a
 * refined chroma/lightness only where the gates still held. Both modes were
 * measured, not eyeballed (OKLab ΔE ×100, Machado 2009 at severity 1.0,
 * against the chart's own `--panel` surface):
 *
 *              worst adjacent  worst adjacent   contrast    all-pairs
 *              CVD ΔE (≥8)     normal ΔE (≥15)  vs surface  series cap
 *   light          9.8             19.7         all ≥3:1        4
 *   dark          10.3             22.1         all ≥3:1        4
 *
 * Every slot clears 3:1 unaided, so no chart depends on the relief rule, and
 * the caps above are *clean* passes — nothing sitting in a warn band. Past
 * four series in a scatter/bubble chart — where any two marks can sit side by
 * side — fold the tail into "Other" rather than adding a ninth hue.
 * If you change a value here, re-run the validator for BOTH modes; a hue
 * swapped by eye is how a palette silently stops being readable.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import embed, { type VisualizationSpec } from 'vega-embed'

type ThemeName = 'dark' | 'light'

const PALETTES: Record<ThemeName, {
  text: string; dim: string; grid: string; category: string[]
}> = {
  dark: {
    text: '#eaeff5',
    dim: '#889098',
    grid: '#242a30',
    //         plum      green     indigo    rose      amber     cyan      ember     teal
    // Dark plum and indigo are deliberately far apart in lightness (0.54 vs
    // 0.655) rather than both sitting mid-band where harmony would put them:
    // at equal lightness the pair collapses under deuteranopia (ΔE 6.5) the
    // moment a scatter puts them side by side. Spread this way it clears 9.7.
    category: ['#a40ed2', '#519c03', '#6786fd', '#eb087d', '#a38207', '#0e94ba', '#d75a07', '#0f9b89'],
  },
  light: {
    // Warm neutrals, matching the light theme's paper — the previous cold
    // blue-greys read as a foreign element sitting on it.
    text: '#0f0a06',
    dim: '#68625b',
    grid: '#dfdad2',
    //         plum      green     indigo    rose      amber     cyan      ember     teal
    category: ['#903ab2', '#448502', '#6d8cfd', '#b90461', '#8a6e08', '#007e9f', '#b94a00', '#018e7d'],
  },
}

function currentTheme(): ThemeName {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

function useThemeName(): ThemeName {
  const [name, setName] = useState<ThemeName>(currentTheme)
  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setName(currentTheme()))
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  return name
}

export function VegaChart({ spec }: { spec: Record<string, unknown> }) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  const theme = useThemeName()

  // Layout depends only on the spec's shape, so compute it once and share it
  // between the render (container sizing) and the embed effect.
  const layout = useMemo(() => {
    const encoding = (spec.encoding ?? {}) as Record<string, { type?: string }>
    const mark = typeof spec.mark === 'object' ? (spec.mark as { type?: string }).type : spec.mark
    const rowCount = Array.isArray((spec.data as { values?: unknown[] })?.values)
      ? (spec.data as { values: unknown[] }).values.length
      : 0

    // A horizontal bar's height must grow with its category count — ~22px per
    // bar — so every label stays legible; the container then scrolls when the
    // result is large. Capping the height (as an earlier version did) crushes
    // hundreds of bars into a few pixels. Every other chart gets a fixed plot
    // height so the marks, not the labels, own the box.
    const isHorizontalBar = mark === 'bar' && encoding.y?.type === 'nominal'
    const PER_BAR = 22
    const height = isHorizontalBar
      ? Math.min(60_000, Math.max(200, rowCount * PER_BAR))
      : mark === 'arc'
        ? 260
        : 300
    const xIsCategorical = encoding.x?.type === 'nominal' || encoding.x?.type === 'ordinal'
    // The same rule on the other axis: a vertical bar chart squeezed to the
    // container turns its categories into a smear once there are more of them
    // than the width has pixels for. Past a dozen, give each mark a minimum
    // width and let the box scroll sideways instead. The backend caps category
    // charts long before this matters — this is the floor that holds if that
    // cap ever changes, not the primary defence.
    const PER_COLUMN = 34
    const width: number | 'container' =
      xIsCategorical && rowCount > 12 ? rowCount * PER_COLUMN : 'container'
    return { encoding, isHorizontalBar, height, width, xIsCategorical }
  }, [spec])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const p = PALETTES[theme]
    const { encoding, height, width, xIsCategorical } = layout

    const config = {
      background: 'transparent',
      view: { stroke: 'transparent' },
      font: 'inherit',
      title: { color: p.text, fontSize: 13, fontWeight: 600, anchor: 'start' as const },
      axis: {
        labelColor: p.dim,
        titleColor: p.dim,
        gridColor: p.grid,
        domainColor: p.grid,
        tickColor: p.grid,
        labelFontSize: 11,
        titleFontSize: 11,
        labelLimit: 140,
        labelPadding: 4,
      },
      legend: { labelColor: p.dim, titleColor: p.text, labelFontSize: 11, titleFontSize: 11 },
      range: { category: p.category },
      // Rounded only at the data end, anchored to the baseline, so the mark
      // still reads as a measurement rather than a lozenge.
      bar: { cornerRadiusEnd: 4 },
      scale: { bandPaddingInner: 0.25 },
      mark: { color: p.category[0] },
      arc: { innerRadius: 0 },
      point: { size: 60, filled: true },
      line: { strokeWidth: 2 },
    }

    // Angle long category labels instead of standing them fully vertical, so
    // they stop eating half the chart. Set on the x-encoding directly (config
    // .axisX does not reliably carry labelAngle) and only when x is the
    // categorical axis — never for numeric axes (scatter, a horizontal bar's
    // measure axis).
    let encodingOverride = spec.encoding
    if (xIsCategorical && encoding.x && typeof encoding.x === 'object') {
      encodingOverride = {
        ...encoding,
        x: { ...encoding.x, axis: { labelAngle: -35, labelLimit: 110, labelPadding: 4 } },
      }
    }

    const full = {
      ...spec,
      encoding: encodingOverride,
      width,
      height,
      autosize: { type: width === 'container' ? 'fit-x' : 'pad', contains: 'padding' },
      background: 'transparent',
      config,
    }

    let cancelled = false
    let result: Awaited<ReturnType<typeof embed>> | null = null
    setFailed(false)
    embed(el, full as unknown as VisualizationSpec, { actions: false, renderer: 'svg' })
      .then((r) => {
        if (cancelled) r.finalize()
        else result = r
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      result?.finalize()
    }
  }, [spec, theme, layout])

  // A chart failure must never blank the answer or the table above it.
  if (failed) return null

  return (
    <div
      style={{
        width: '100%',
        marginTop: 6,
        padding: '8px 10px',
        border: '1px solid var(--border)',
        borderRadius: 10,
        background: 'var(--panel)',
        overflowX: 'auto',
        // A tall, many-bar horizontal chart scrolls inside a bounded box rather
        // than stretching the whole conversation down the page.
        overflowY: layout.isHorizontalBar ? 'auto' : 'visible',
        maxHeight: layout.isHorizontalBar ? 'min(70vh, 640px)' : undefined,
      }}
    >
      <div ref={ref} style={{ width: '100%', minHeight: 40 }} />
    </div>
  )
}
