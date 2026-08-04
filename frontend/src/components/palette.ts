/**
 * The chart palette: every colour a chart may paint with, in both themes.
 *
 * Hex rather than the app's oklch CSS variables because Vega/D3 cannot parse
 * `oklch()` and would fall back to black — these are the *same* theme colours,
 * converted once.
 *
 * Its own module, apart from the component that uses it, so the values can be
 * imported by `palette.test.ts` (`npm run test:palette`) without dragging in
 * React and vega-embed. Every claim below is asserted there and re-runnable;
 * see the note at the end of this comment about why that matters.
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
 *   dark          15.5            16.0          all ≥3:1        5
 *
 * The dark row is the blue-anchored set: it trades normal-vision headroom
 * (22.1 → 16.0, still clear of the 15 floor) for a much stronger CVD margin
 * (10.3 → 15.5). Every slot clears 3:1 unaided, so no chart depends on the
 * relief rule, and the caps above are *clean* passes — nothing sitting in a
 * warn band.
 *
 * **The operative cap is 4, the lower of the two**, and it has to be: the same
 * chart is repainted when the reader flips the theme, so a fifth series that
 * only separates in dark would collapse the moment they switched. Past four
 * series in a scatter/bubble chart — where any two marks can sit side by side
 * — fold the tail into "Other" rather than adding a ninth hue.
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
 *
 * **`heatmap` is a fourth family and was the one still missing.** A `rect`
 * mark's quantitative colour does not resolve to `ramp`; Vega-Lite gives it
 * its own `heatmap` range, which nothing set — so every heatmap this product
 * has ever drawn came out in Vega's built-in yellow-green-blue while the chart
 * beside it was plum. Measured, not guessed: compiling a heatmap spec through
 * the installed vega-lite and reading the cell fills back returned
 * `rgb(239,249,189)` and `rgb(28,49,133)`. It now takes the sequential ramp,
 * like every other ordered scale.
 *
 * ── The diverging ramp ───────────────────────────────────────────────────
 *
 * Five steps for the one job the ramps above cannot do: **polarity**, where
 * the question is which side of a baseline a value falls on. A sequential ramp
 * answers "how much" and says nothing about sign, so a heatmap of change drawn
 * on one hue hides the difference between -40% and +40%.
 *
 * Reached by setting `domainMid` on the colour scale, which is what makes
 * Vega-Lite select this family — verified by compiling both variants and
 * reading the resolved scale range, rather than assumed.
 *
 * Two hues around a **neutral grey midpoint**, per the rule that a diverging
 * scale never puts a hue at its centre: the middle step is that mode's own
 * neutral (dark 250, light 85) at chroma ≤ 0.01, so "no change" reads as
 * absence rather than as a third category. The midpoint is also the step that
 * recedes toward the surface — dark's is the darkest, light's the lightest —
 * the same anchor rule the sequential ramp follows, so near-zero always sinks
 * into the page in either theme. Arms step outward evenly (ΔL 0.20 dark, 0.18
 * light), well past the 0.06 minimum.
 *
 * ── Polarity ─────────────────────────────────────────────────────────────
 *
 * The diverging ramp's two poles, named, for the places a single value carries
 * a sign: a bar below zero, and any future metric that declares its direction.
 * Deliberately **not** drawn from the categorical wheel — a negative painted in
 * "series colour 2" says nothing — and asserted at ΔE ≥ 15 from every slot in
 * its mode so it cannot be read as an identity.
 *
 * **Why the positive pole is teal and not green.** Red-down/green-up is the
 * convention and dark mode can carry it (a conventional pair measures CVD ΔE
 * 18.6 there). Light mode cannot: on near-white paper both poles must be dark
 * to stay legible, and dark red against dark green is the textbook
 * deuteranopia collapse — the best conventional pair reaches **7.5**, inside
 * the 6–8 band that is only legal alongside a second, non-colour channel. A
 * bar has one (it points the other way) but a heatmap cell has none at all:
 * colour is the entire encoding. So the gate binds, the positive pole moves to
 * teal, and both modes clear it (9.6 dark, 10.5 light). Warm is negative and
 * cool is positive in both themes, so the reading survives a theme flip even
 * though the exact hue does not.
 *
 * **The KPI delta stays uncoloured**, which is not an oversight. The direction
 * of a change is in the data; whether it is *good* is not — a rising refund
 * rate is not good news, and nothing in a result says which metric it holds.
 * The delta keeps its arrow and neutral text. These tokens exist for the sign
 * of a *value*, which is a fact, and are ready for the delta the day a metric
 * can declare its polarity.
 *
 * ── Changing any of this ─────────────────────────────────────────────────
 *
 * Run `npm run test:palette`. Every figure quoted above is asserted there for
 * **both** modes, so a value swapped by eye fails rather than silently
 * degrading. That script exists because these numbers were once a measurement
 * nobody could re-run: rebuilding it caught the dark all-pairs cap recorded as
 * 4 when the set actually carries 5, and the missing `heatmap` range above.
 */

export type ThemeName = 'dark' | 'light'

export type Palette = {
  text: string
  dim: string
  grid: string
  category: string[]
  ramp: string[]
  /** Polarity around a neutral midpoint. Index 2 is the neutral. */
  diverging: string[]
  /** The named poles of `diverging`, for a single signed value. */
  polarity: { negative: string; positive: string }
}

/** Each mode's `--panel`, the surface a chart is actually read against. */
export const SURFACE: Record<ThemeName, string> = {
  dark: '#12171b',
  light: '#fffdfa',
}

export const PALETTES: Record<ThemeName, Palette> = {
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
    //            −ve pole   −ve       neutral    +ve       +ve pole
    diverging: ['#ffb5ab', '#bd776e', '#4e5358', '#3b9d9d', '#1ee6e7'],
    polarity: { negative: '#ffb5ab', positive: '#1ee6e7' },
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
    diverging: ['#820006', '#a35f57', '#acaaa7', '#198585', '#024d4d'],
    polarity: { negative: '#820006', positive: '#024d4d' },
  },
}
