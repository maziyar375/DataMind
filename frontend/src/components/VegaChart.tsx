/**
 * Renders a backend-produced Vega-Lite spec with vega-embed.
 *
 * The spec's data and encodings are chosen by the agent (the `chart` pipeline
 * node); this component only paints it, and the colours it paints with live in
 * `theme/palette.ts` — moved there so `npm run test:palette` can re-measure
 * them without importing React. A MutationObserver re-renders the chart when
 * the theme is toggled.
 *
 * Two colour decisions are made *here* rather than in the spec, and both for
 * the same reason: the backend knows the data and the browser knows the theme.
 * A measure that crosses zero gets the diverging ramp instead of the
 * sequential one, and a bar chart with values below the axis paints those from
 * the semantic pair. In both cases the spec's `usermeta` states the *fact*
 * (this measure is signed; here is the test that finds a negative row) and
 * this file supplies the colours.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import embed, { type VisualizationSpec } from 'vega-embed'

import { PALETTES, type ThemeName } from '../theme/palette'

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
   * definite height (flex: 1 / height: 100%). The charts that grow downward
   * are exempt — a horizontal bar's height must follow its category count and
   * a heatmap's its second dimension, so labels and cells stay legible; those
   * scroll instead.
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
      { datamind?: {
        chart_type?: string; orientation?: string; stack?: string
        categories?: number; bands?: number
        color_scale?: 'sequential' | 'diverging'; negative_test?: string
      } } | undefined)?.datamind
    const isHorizontalBar = meta
      ? meta.chart_type === 'bar' && meta.orientation === 'horizontal'
      : mark === 'bar' && encoding.y?.type === 'nominal'
    // A heatmap has the same problem for the same reason — rows of cells
    // stacked down the page — but its own row count, and it needs a taller
    // band than a bar because a cell is read as an area, not a length.
    const isHeatmap = meta?.chart_type === 'heatmap'
    const PER_BAND = isHeatmap ? 26 : 22
    // Marks drawn along the vertical category axis. For a heatmap that is the
    // second dimension; for bars, a stacked split puts its parts on one bar so
    // the count is the categories, while a grouped one gives each part its own
    // bar and it is back to the row count.
    const bandCount = isHeatmap
      ? meta?.bands ?? 0
      : meta?.stack === 'grouped' ? rowCount : meta?.categories ?? rowCount
    // Past a dozen bands the fixed plot height starts crushing them; below it,
    // filling the tile looks better than a short chart floating in a tall box.
    const growsDown = isHorizontalBar || (isHeatmap && bandCount > 12)
    const height: number | 'container' = growsDown
      ? Math.min(60_000, Math.max(200, bandCount * PER_BAND))
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
    //
    // What is counted is *columns*, not rows, and once a chart is split by a
    // series those stop being the same number: eight regions over twelve
    // months is 96 rows and twelve columns. The backend states the column
    // count in `usermeta`; the row count is only the fallback for specs
    // compiled before it did, where no split was expressible anyway.
    const columnCount = meta?.categories ?? rowCount
    const PER_COLUMN = 34
    const width: number | 'container' =
      xIsCategorical && columnCount > 12 ? columnCount * PER_COLUMN : 'container'
    return { encoding, growsDown, height, width, xIsCategorical, meta }
  }, [spec, fill])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const p = PALETTES[theme]
    const { encoding, height, width, xIsCategorical, meta } = layout

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
      // Vega picks the scale *family* from the encoding type AND the mark, and
      // each family has its own default range: `category` for discrete colour,
      // `ramp` for continuous, `ordinal` for ordered — and `heatmap` for a
      // continuous colour on a `rect`. Overriding only `category` left the
      // others on Vega's built-ins, so a chart split by a numeric field came
      // out blue while every other chart was plum: one product, two palettes.
      //
      // `heatmap` is that same bug, caught a second time. The type arrived
      // after this comment was written, and because a rect's colour scale asks
      // for the `heatmap` range rather than `ramp`, every heatmap drew in
      // Vega's viridis — green-to-yellow, from no palette in this file. The
      // lesson generalises: a new mark can quietly introduce a new range name,
      // so read the compiled scale rather than assuming `ramp` covers it.
      range: {
        category: p.category,
        ramp: p.ramp,
        ordinal: p.ramp,
        heatmap: p.ramp,
      },
      // Rounded only at the data end, anchored to the baseline, so the mark
      // still reads as a measurement rather than a lozenge.
      bar: { cornerRadiusEnd: 4 },
      scale: { bandPaddingInner: 0.25 },
      mark: { color: p.category[0] },
      arc: { innerRadius: 0 },
      point: { size: 60, filled: true },
      line: { strokeWidth: 2 },
    }

    // Overrides applied to the spec's own encoding. Each is something the
    // backend could not decide: it holds the data, this file holds the theme
    // and the pixels.
    let encodingOverride = spec.encoding as Record<string, unknown> | undefined
    const override = (channel: string, value: unknown) => {
      encodingOverride = { ...(encodingOverride ?? {}), [channel]: value }
    }

    // Angle long category labels instead of standing them fully vertical, so
    // they stop eating half the chart. Set on the x-encoding directly (config
    // .axisX does not reliably carry labelAngle) and only when x is the
    // categorical axis — never for numeric axes (scatter, a horizontal bar's
    // measure axis).
    if (xIsCategorical && encoding.x && typeof encoding.x === 'object') {
      override('x', {
        ...encoding.x,
        axis: { labelAngle: -35, labelLimit: 110, labelPadding: 4 },
      })
    }

    // A measure that crosses zero: the sequential ramp would say a large
    // negative and a large positive are equally "a lot", which is the opposite
    // of what the reader needs. The spec already pinned `domainMid: 0` — where
    // the neutral step belongs is a fact about the data — so only the colours
    // are added here.
    if (meta?.color_scale === 'diverging' && encoding.color) {
      const colour = encoding.color as Record<string, unknown>
      override('color', {
        ...colour,
        scale: { ...(colour.scale as object ?? {}), range: p.diverging },
      })
    }

    // Bars below the axis, painted from the semantic pair. `negative_test` is
    // a Vega expression the backend wrote, because only it knows the field
    // name and can escape it; which two colours the test selects between is a
    // theme decision and belongs here. Sign, not judgement — nothing in this
    // says a fall is bad news.
    if (meta?.negative_test) {
      override('color', {
        condition: { test: meta.negative_test, value: p.negative },
        value: p.positive,
      })
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
        // fixed rather than fitted: the charts that grow downward with their
        // rows (horizontal bars, tall heatmaps) and wide categorical charts
        // (per-column width).
        overflowX:
          fill && !layout.growsDown && layout.width === 'container' ? 'hidden' : 'auto',
        // A tall, many-bar horizontal chart scrolls inside a bounded box rather
        // than stretching the whole conversation down the page.
        overflowY: layout.growsDown ? 'auto' : fill ? 'hidden' : 'visible',
        maxHeight: layout.growsDown ? 'min(70vh, 640px)' : undefined,
      }}
    >
      <div
        ref={ref}
        style={{
          width: '100%',
          minHeight: 40,
          height: fill && !layout.growsDown ? '100%' : undefined,
        }}
      />
    </div>
  )
}
