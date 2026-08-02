/**
 * Every colour a chart is allowed to paint with.
 *
 * Hex rather than the app's oklch CSS variables because Vega/D3 cannot parse
 * `oklch()` and would fall back to black — so these are the *same* theme
 * colours, converted once. `VegaChart.tsx` renders from them; `palette.test.ts`
 * re-measures them (`npm run test:palette`), which is why they live in a module
 * of their own rather than inside a React component the test cannot import.
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
 * 0.20 — unconstrained, the search returns a neon set). Measured, not
 * eyeballed (OKLab ΔE ×100, Machado 2009 at severity 1.0, against the chart's
 * own `--panel` surface) — and now re-measurable, which changed one row:
 *
 *              worst adjacent  worst adjacent  worst adjacent   contrast
 *              deutan ΔE (≥8)  protan ΔE (≥8)  tritan ΔE (≥5)  vs surface
 *   light           9.8            16.7            12.9         all ≥3:1
 *   dark           15.5            15.8             7.3         all ≥3:1
 *
 * **The tritanopia column is new, and it is why the validator exists.** The
 * table here used to record one CVD number per mode — 9.8 and 15.5 — without
 * saying which deficiency, and both turn out to be *deuteranopia only*. Run
 * against all three, the dark set's violet/olive pair separates by 7.3 under
 * tritanopia: below the ≥8 bar the file was claiming to meet. It is held at a
 * lower floor rather than re-tuned, deliberately. Tritanopia affects roughly 1
 * in 10,000 people and is not sex-linked; red-green deficiency affects about 1
 * man in 12. Trading away a measured red-green margin to fix a blue-yellow one
 * would help far fewer readers than it hurt. The number is now *visible and
 * bounded* instead of unmeasured, which is the honest position.
 *
 * The dark row's deuteranopia figure is the blue-anchored set: it trades
 * normal-vision headroom (22.1 → 16.0, still clear of the 15 floor) for a much
 * stronger CVD margin (10.3 → 15.5). Every slot clears 3:1 unaided, so no
 * chart depends on the relief rule. Past four series in a scatter/bubble chart
 * — where any two marks can sit side by side — fold the tail into "Other"
 * rather than adding a ninth hue.
 *
 * ── The sequential ramp ──────────────────────────────────────────────────
 *
 * Five steps of that mode's own anchor hue (dark 250, light 315), for the
 * *ordered* colour jobs — an ordinal split, a continuous magnitude. It exists
 * because Vega chooses the scale family from the encoding type and gives each
 * family its own default range: overriding `category` alone left `ramp` and
 * `ordinal` on Vega's built-in `blues`, so one chart in the app came out a
 * different colour from the rest. Validated as an ordinal ramp (monotone L,
 * adjacent ΔL ≥ 0.06, surface-nearest step ≥ 2:1): dark 2.34:1 at #00539b,
 * light 2.05:1 at #d0a4e4. Dark runs dark→light and light runs light→dark —
 * low magnitude first in both, so "near zero" is always the end that recedes
 * toward that mode's surface.
 *
 * ── The diverging ramp ───────────────────────────────────────────────────
 *
 * For a measure that crosses zero — change, variance, a difference against
 * target — where the sequential ramp would say a large negative and a large
 * positive are the same thing.
 *
 * **This is the one palette that does not chase the accent**, and the reason
 * is that its job is to encode *sign*, not identity. Ember ↔ blue is the
 * reading a business audience already has, in both modes; a plum-anchored
 * diverging ramp in light mode would be on-brand and unreadable. The midpoint
 * is a near-neutral that recedes toward each mode's own surface, so "no
 * change" is the value that disappears.
 *
 * Gates, both modes: ends separate by ΔE ≥ 15 under every simulated
 * deficiency; adjacent steps by ≥ 8 normal and ≥ 5 under CVD; every step ≥ 2:1
 * against the surface and both extreme arms ≥ 3:1.
 *
 * ── The semantic pair ────────────────────────────────────────────────────
 *
 * `positive` and `negative`, for values either side of zero — a bar below the
 * axis, a shortfall against target. **Sign, not judgement.** Nothing here says
 * a rise is good news: a climbing refund rate is not, and the platform cannot
 * know which metric it is looking at. That is also why a KPI's delta is drawn
 * with an arrow and neutral text rather than painted from this pair — see
 * `Kpi` in `components/ui.tsx`.
 *
 * `positive` is deliberately the same value as `category[0]`: a chart with
 * negative values should keep the colour it would have had, with only the
 * negatives departing from it. `negative` is measured against `category[0]`
 * rather than against the surface alone — it has to be told from the bars it
 * sits beside, which is the comparison that matters. Note what is *not* used:
 * green/red, the conventional pair and the classic red-green failure.
 *
 *              vs category[0]   vs category[0]    contrast
 *              normal ΔE (≥15)  worst CVD (≥8)   vs surface (≥3:1)
 *   light          23.8             19.3             4.86
 *   dark           35.9             30.9             4.40
 *
 * If you change any value in this file, run `npm run test:palette`. A hue
 * swapped by eye is how a palette silently stops being readable.
 */

export type ThemeName = 'dark' | 'light'

export interface ChartPalette {
  text: string
  dim: string
  grid: string
  /** Discrete identity — one hue per series. */
  category: string[]
  /** Ordered magnitude, low first. */
  ramp: string[]
  /** Magnitude either side of a meaningful zero. */
  diverging: string[]
  positive: string
  negative: string
}

export const PALETTES: Record<ThemeName, ChartPalette> = {
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
    // Negative ← neutral → positive. The middle step is the one that recedes.
    diverging: ['#f2897c', '#a36d66', '#555151', '#5d81a6', '#67b0f9'],
    positive: '#008df2',   // category[0]: a chart keeps the colour it had
    negative: '#dc4d04',
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
    diverging: ['#93342c', '#a9736c', '#b4b0af', '#6387ac', '#005a9d'],
    positive: '#903ab2',
    negative: '#d4312a',
  },
}
