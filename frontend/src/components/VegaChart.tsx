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
 * Eight hues spaced evenly around the wheel from the theme's own accent, so
 * the whole set is derived from the brand rather than bolted on beside it.
 * Slot 1 is that accent's hue: a single-series chart paints with slot 1, which
 * is most charts, so it is the colour the product actually reads as.
 *
 * **Each mode anchors on its own accent, and they are different hues.** Light
 * chrome is plum (OKLCH 0.52 0.19 315) and dark chrome is blue (OKLCH 0.7 0.15
 * 250), so the light wheel starts at 315 and the dark wheel at 250 — charts
 * match the theme they are sitting in. The cost, stated plainly: a series does
 * NOT keep its hue when the user flips the theme. That was the earlier
 * arrangement (plum in both modes) and it made dark charts a foreign object on
 * a blue-accented page. Matching the theme was chosen over hue-stability.
 *
 * Still true either way: **no chart may paint from `var(--accent)`.** Slot 1
 * tracks the accent's hue but is stepped for the chart surface; reading the
 * variable directly is what once put a blue chart next to a plum one, and it
 * bypasses every check below.
 *
 * The order is the colourblind-safety mechanism and is NOT cosmetic: it was
 * chosen to maximise the worst separation, then each step was moved toward a
 * refined chroma/lightness only where the gates still held (chroma capped at
 * 0.20 — unconstrained, the search returns a neon set). Both modes were
 * measured, not eyeballed (OKLab ΔE ×100, Machado 2009 at severity 1.0,
 * against the chart's own `--panel` surface):
 *
 *              worst adjacent  worst adjacent   contrast    all-pairs
 *              CVD ΔE (≥8)     normal ΔE (≥15)  vs surface  series cap
 *   light          9.8             19.7         all ≥3:1        4
 *   dark          15.5            16.0          all ≥3:1        4
 *
 * The dark row is the blue-anchored set: it trades normal-vision headroom
 * (22.1 → 16.0, still clear of the 15 floor) for a much stronger CVD margin
 * (10.3 → 15.5). Every slot clears 3:1 unaided, so no chart depends on the
 * relief rule, and the caps above are *clean* passes — nothing sitting in a
 * warn band. Past four series in a scatter/bubble chart — where any two marks
 * can sit side by side — fold the tail into "Other" rather than adding a ninth
 * hue. If you change a value here, re-run the validator for BOTH modes; a hue
 * swapped by eye is how a palette silently stops being readable.
 *
 * ── The sequential ramp ──────────────────────────────────────────────────
 *
 * Five steps of that mode's own anchor hue (dark 250, light 315), for the
 * *ordered* colour jobs — an ordinal split, a continuous magnitude. It exists
 * because Vega chooses the scale family from the encoding type and gives each
 * family its own default range: overriding `category` alone left `ramp` and
 * `ordinal` on Vega's built-in `blues`, so one chart in the app came out a
 * different colour from the rest. Validated as an ordinal ramp (monotone L,
 * adjacent ΔL ≥ 0.06, surface-nearest step ≥ 2:1) against each mode's
 * `--panel`: dark 2.33:1 at #00539b, light 2.05:1 at #d0a4e4. Dark runs
 * dark→light and light runs light→dark — low magnitude first in both, so
 * "near zero" is always the end that recedes toward that mode's surface.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import embed, { type VisualizationSpec } from 'vega-embed'

type ThemeName = 'dark' | 'light'

const PALETTES: Record<ThemeName, {
  text: string; dim: string; grid: string; category: string[]; ramp: string[]
}> = {
  dark: {
    text: '#eaeff5',
    dim: '#889098',
    grid: '#242a30',
    //         blue      red       violet    olive     magenta   green     ember     cyan
    // Chroma is capped at 0.20 — the unconstrained search maximises it and
    // returns a neon set that reads as eight alarms rather than a palette.
    // Lightness deliberately varies slot to slot (0.50–0.67) instead of
    // sitting in one harmonious band: at equal lightness a hue pair collapses
    // under deuteranopia the moment a scatter puts the two side by side.
    category: ['#008df2', '#f85452', '#7c4cd5', '#626a00', '#d54db0', '#00764c', '#009daa', '#9b6100'],
    // Low magnitude first. On dark the anchor flips — "near zero" recedes
    // toward the surface — so this ramp runs dark→light, the mirror of light
    // mode, rather than the same array reversed by eye.
    ramp: ['#00539b', '#006eca', '#008cf0', '#53abff', '#8ccaff'],
  },
  light: {
    // Warm neutrals, matching the light theme's paper — the previous cold
    // blue-greys read as a foreign element sitting on it.
    text: '#0f0a06',
    dim: '#68625b',
    grid: '#dfdad2',
    //         plum      green     indigo    rose      amber     cyan      ember     teal
    category: ['#903ab2', '#448502', '#6d8cfd', '#b90461', '#8a6e08', '#007e9f', '#b94a00', '#018e7d'],
    ramp: ['#d0a4e4', '#b97cd3', '#9e4ebe', '#7a2f97', '#56206b'],
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

export function VegaChart({ spec, frameless = false, fill = false }: {
  spec: Record<string, unknown>
  /**
   * Drop the border, padding and panel background. A dashboard tile already
   * is a bordered panel, and nesting a second one inside it reads as a chart
   * that failed to fill its box.
   */
  frameless?: boolean
  /**
   * Fill the parent's height too, not only its width. In a dashboard tile the
   * box is the tile the user sized, and a fixed-height plot floating in a
   * taller tile reads as wasted space. The parent must give this component a
   * definite height (flex: 1 / height: 100%). Horizontal bars are exempt:
   * their height must grow with the category count so labels stay legible,
   * and they scroll instead.
   */
  fill?: boolean
}) {
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
    //
    // Which chart this *is* comes from `usermeta`, where the backend states its
    // decision, rather than from re-deriving it out of the encoding. The old
    // test — bar mark plus a nominal y — is a guess that a stacked bar or a
    // future `rect` mark can satisfy by accident. It survives only as the
    // fallback for specs compiled before `usermeta` existed: chat artifacts
    // persist their spec, so those are still on screen, and the guess is the
    // same one that was in force when they were written.
    const meta = (spec.usermeta as
      { datamind?: { chart_type?: string; orientation?: string } } | undefined)?.datamind
    const isHorizontalBar = meta
      ? meta.chart_type === 'bar' && meta.orientation === 'horizontal'
      : mark === 'bar' && encoding.y?.type === 'nominal'
    const PER_BAR = 22
    const height: number | 'container' = isHorizontalBar
      ? Math.min(60_000, Math.max(200, rowCount * PER_BAR))
      : fill
        ? 'container'
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
  }, [spec, fill])

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
      // Vega picks the scale *family* from the encoding type, and each family
      // has its own default range: `category` for discrete colour, but `ramp`
      // for continuous and `ordinal` for ordered. Overriding only `category`
      // left the other two on Vega's built-in `blues`, so a chart split by a
      // numeric field came out blue while every other chart was plum — one
      // product, two palettes. All three now draw from the same accent hue.
      range: { category: p.category, ramp: p.ramp, ordinal: p.ramp },
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
      autosize: {
        type:
          width === 'container' && height === 'container' ? 'fit'
          : width === 'container' ? 'fit-x'
          : height === 'container' ? 'fit-y'
          : 'pad',
        contains: 'padding',
      },
      background: 'transparent',
      config,
    }

    let cancelled = false
    let result: Awaited<ReturnType<typeof embed>> | null = null
    let observer: ResizeObserver | null = null
    let resizeTimer: number | null = null
    setFailed(false)
    embed(el, full as unknown as VisualizationSpec, { actions: false, renderer: 'svg' })
      .then((r) => {
        if (cancelled) {
          r.finalize()
          return
        }
        result = r
        // A 'container' size only re-measures on window:resize — that is the
        // sole trigger Vega-Lite compiles into the width/height signals. A
        // dashboard tile resizes its element without one (react-grid-layout,
        // the settings drawer opening), leaving the chart at its stale size
        // until the next refresh re-embeds it. So watch the element and hand
        // the new size to the view ourselves, debounced so a resize drag
        // re-lays-out once at rest, not sixty times a second.
        if (width === 'container' || height === 'container') {
          // Sub-2px changes are ignored: fit autosize can overshoot the box by
          // a pixel, and reacting to our own overshoot would loop forever.
          let lastW = -1
          let lastH = -1
          observer = new ResizeObserver(() => {
            if (resizeTimer !== null) window.clearTimeout(resizeTimer)
            resizeTimer = window.setTimeout(() => {
              const w = el.clientWidth
              const h = el.clientHeight
              if (Math.abs(w - lastW) < 2 && Math.abs(h - lastH) < 2) return
              lastW = w
              lastH = h
              let view = r.view
              if (width === 'container' && w > 0) view = view.width(w)
              if (height === 'container' && h > 0) view = view.height(h)
              void view.resize().runAsync()
            }, 60)
          })
          observer.observe(el)
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      observer?.disconnect()
      if (resizeTimer !== null) window.clearTimeout(resizeTimer)
      result?.finalize()
    }
  }, [spec, theme, layout])

  // A chart failure must never blank the answer or the table above it.
  if (failed) return null

  return (
    <div
      style={{
        width: '100%',
        // In fill mode the box's height comes from the parent, and the inner
        // div must inherit it as a definite length: Vega measures the
        // container to size the plot, and measuring an auto-height element
        // would read back the plot's own height — a feedback loop.
        height: fill ? '100%' : undefined,
        marginTop: frameless ? 0 : 6,
        padding: frameless ? 0 : '8px 10px',
        border: frameless ? 'none' : '1px solid var(--border)',
        borderRadius: frameless ? 0 : 10,
        background: frameless ? 'transparent' : 'var(--panel)',
        // Fill mode clips instead of scrolling: the chart is fitted to this
        // box, and a scrollbar triggered by a pixel of overshoot would change
        // the box's size and start a fit/unfit oscillation. Two exemptions
        // scroll on purpose, and are stable because their content size is
        // fixed rather than fitted: horizontal bars (per-category height) and
        // wide categorical charts (per-column width).
        overflowX:
          fill && !layout.isHorizontalBar && layout.width === 'container' ? 'hidden' : 'auto',
        // A tall, many-bar horizontal chart scrolls inside a bounded box rather
        // than stretching the whole conversation down the page.
        overflowY: layout.isHorizontalBar ? 'auto' : fill ? 'hidden' : 'visible',
        maxHeight: layout.isHorizontalBar ? 'min(70vh, 640px)' : undefined,
      }}
    >
      <div
        ref={ref}
        style={{
          width: '100%',
          minHeight: 40,
          height: fill && !layout.isHorizontalBar ? '100%' : undefined,
        }}
      />
    </div>
  )
}
