/**
 * The palette's claims, re-measured from the values themselves.
 *
 * `npm run test:palette` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), like the other two checked
 * modules here.
 *
 * **Why this exists.** `palette.ts` quoted ΔE and Machado figures that were a
 * one-off manual measurement, and its own comment told the next person to
 * re-run them — with nothing to run. So the numbers could only rot, and two
 * had: the dark all-pairs cap was recorded as 4 when the set carries 5, and
 * the `heatmap` scale family was missing entirely, which is not a number at
 * all but was invisible for the same reason. A palette is the one part of a
 * design system where "it looks fine to me" is not evidence — a reader with
 * deuteranopia is not in the room.
 *
 * The colour science lives here rather than in `palette.ts` because none of it
 * ships: the app needs the hex values, not the means of checking them.
 *
 * ΔE throughout is Euclidean distance in OKLab ×100. CVD is simulated with
 * Machado–Oliveira–Fernandes (2009) at severity 1.0; the thresholds are
 * calibrated to that model, so swapping the simulation would mean recalibrating
 * the gates rather than just changing an implementation detail.
 */
import { PALETTES, SURFACE, type ThemeName } from './palette.ts'

// ── gates ────────────────────────────────────────────────────────────────
const CVD_TARGET = 8 // adjacent pairs, min(protan, deutan)
const NORMAL_FLOOR = 15 // adjacent pairs, unsimulated vision
const MARK_CONTRAST = 3 // a mark against its surface (WCAG non-text)
const RAMP_CONTRAST = 2 // the surface-nearest step of an ordered ramp
const RAMP_MIN_DL = 0.06 // adjacent steps of an ordered ramp, OKLCH L
const CHROMA_FLOOR = 0.1 // below this a categorical hue reads as grey
const NEUTRAL_CHROMA_MAX = 0.02 // the midpoint of a diverging ramp is not a hue
const SERIES_DISTANCE = 15 // polarity vs every categorical slot
const SERIES_CAP = 4 // all-pairs cap that holds in BOTH modes

// Machado, Oliveira & Fernandes (2009) CVD transforms at severity 1.0, linear RGB.
const MACHADO: Record<string, number[][]> = {
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
}

const srgb = (hex: string): number[] => {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
}
const toLinear = (c: number): number =>
  c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
const linear = (hex: string): number[] => srgb(hex).map(toLinear)

function relativeLuminance(hex: string): number {
  const [r, g, b] = linear(hex)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

function oklabOf(rgb: number[]): number[] {
  const [r, g, b] = rgb
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

const lightness = (hex: string): number => oklabOf(linear(hex))[0]
const chroma = (hex: string): number => {
  const [, a, b] = oklabOf(linear(hex))
  return Math.hypot(a, b)
}

function simulate(hex: string, kind: string): number[] {
  const [r, g, b] = linear(hex)
  const M = MACHADO[kind]
  const clamp = (c: number) => Math.max(0, Math.min(1, c))
  return [
    clamp(M[0][0] * r + M[0][1] * g + M[0][2] * b),
    clamp(M[1][0] * r + M[1][1] * g + M[1][2] * b),
    clamp(M[2][0] * r + M[2][1] * g + M[2][2] * b),
  ]
}

/** OKLab ΔE ×100. `kind` omitted measures unsimulated (normal) vision. */
function deltaE(a: string, b: string, kind?: string): number {
  const x = oklabOf(kind ? simulate(a, kind) : linear(a))
  const y = oklabOf(kind ? simulate(b, kind) : linear(b))
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2])
}

const cvdDelta = (a: string, b: string): number =>
  Math.min(deltaE(a, b, 'protan'), deltaE(a, b, 'deutan'))

// ── harness ──────────────────────────────────────────────────────────────
// No `node:assert`, no `process`: keeping this file free of Node's types is
// what lets it sit under `src/` and be type-checked with everything else.
let failures = 0
function check(name: string, pass: boolean, detail: string): void {
  if (!pass) failures += 1
  console.log(pass ? `ok    ${name} — ${detail}` : `FAIL  ${name} — ${detail}`)
}

const round = (n: number) => Math.round(n * 10) / 10

/** The worst adjacent pair, since only neighbours touch in a stack or a line. */
function worstAdjacent(
  colors: string[],
  measure: (a: string, b: string) => number,
): { value: number; pair: [string, string] } {
  let worst = { value: Infinity, pair: [colors[0], colors[1]] as [string, string] }
  for (let i = 0; i < colors.length - 1; i += 1) {
    const value = measure(colors[i], colors[i + 1])
    if (value < worst.value) worst = { value, pair: [colors[i], colors[i + 1]] }
  }
  return worst
}

/** How many leading slots stay pairwise separable — the scatter/bubble cap. */
function allPairsCap(colors: string[]): number {
  let cap = 1
  for (let k = 2; k <= colors.length; k += 1) {
    let ok = true
    for (let i = 0; i < k; i += 1) {
      for (let j = i + 1; j < k; j += 1) {
        if (cvdDelta(colors[i], colors[j]) < CVD_TARGET) ok = false
        if (deltaE(colors[i], colors[j]) < NORMAL_FLOOR) ok = false
      }
    }
    if (!ok) return cap
    cap = k
  }
  return cap
}

/** Monotone lightness with a real step between neighbours. */
function rampIsOrdered(steps: string[]): { ok: boolean; minStep: number } {
  const ls = steps.map(lightness)
  const rising = ls[ls.length - 1] > ls[0]
  let minStep = Infinity
  for (let i = 1; i < ls.length; i += 1) {
    const d = ls[i] - ls[i - 1]
    if (rising ? d <= 0 : d >= 0) return { ok: false, minStep: 0 }
    minStep = Math.min(minStep, Math.abs(d))
  }
  return { ok: minStep >= RAMP_MIN_DL, minStep }
}

// ── the checks, per mode ─────────────────────────────────────────────────
const caps: number[] = []

for (const mode of ['dark', 'light'] as ThemeName[]) {
  const p = PALETTES[mode]
  const surface = SURFACE[mode]
  console.log(`\n── ${mode} ──`)

  // Categorical: identity. Every gate the file's table quotes.
  const cvd = worstAdjacent(p.category, cvdDelta)
  check(
    'categorical: adjacent CVD separation',
    cvd.value >= CVD_TARGET,
    `worst ${cvd.pair[0]}↔${cvd.pair[1]} ΔE ${round(cvd.value)} (≥${CVD_TARGET})`,
  )

  const normal = worstAdjacent(p.category, (a, b) => deltaE(a, b))
  check(
    'categorical: adjacent normal-vision floor',
    normal.value >= NORMAL_FLOOR,
    `worst ΔE ${round(normal.value)} (≥${NORMAL_FLOOR})`,
  )

  const dim = p.category.filter((c) => chroma(c) < CHROMA_FLOOR)
  check(
    'categorical: chroma floor',
    dim.length === 0,
    dim.length ? `read as grey: ${dim.join(', ')}` : `all 8 ≥ ${CHROMA_FLOOR}`,
  )

  const worstMark = Math.min(...p.category.map((c) => contrast(c, surface)))
  check(
    'categorical: contrast vs surface',
    worstMark >= MARK_CONTRAST,
    `worst ${round(worstMark)}:1 (≥${MARK_CONTRAST}:1)`,
  )

  const cap = allPairsCap(p.category)
  caps.push(cap)
  check('categorical: all-pairs series cap', cap >= SERIES_CAP, `${cap} slots separable pairwise`)

  // Sequential: magnitude. One hue, ordered, and never invisible on the surface.
  const ordered = rampIsOrdered(p.ramp)
  check(
    'sequential: monotone lightness',
    ordered.ok,
    `smallest step ΔL ${ordered.minStep.toFixed(2)} (≥${RAMP_MIN_DL})`,
  )
  const nearest = Math.min(...p.ramp.map((c) => contrast(c, surface)))
  check(
    'sequential: surface-nearest step stays visible',
    nearest >= RAMP_CONTRAST,
    `${round(nearest)}:1 (≥${RAMP_CONTRAST}:1)`,
  )

  // Diverging: polarity. Neutral centre, ordered arms, separable poles.
  const [negPole, , neutral, , posPole] = p.diverging
  check(
    'diverging: the midpoint is neutral, not a third hue',
    chroma(neutral) <= NEUTRAL_CHROMA_MAX,
    `C ${chroma(neutral).toFixed(3)} (≤${NEUTRAL_CHROMA_MAX})`,
  )
  const armA = rampIsOrdered(p.diverging.slice(0, 3))
  const armB = rampIsOrdered(p.diverging.slice(2))
  check(
    'diverging: both arms step outward evenly',
    armA.ok && armB.ok,
    `smallest step ΔL ${Math.min(armA.minStep, armB.minStep).toFixed(2)} (≥${RAMP_MIN_DL})`,
  )
  // The midpoint recedes toward the surface, so "no change" sinks into the page.
  const midRecedes =
    contrast(neutral, surface) < contrast(negPole, surface) &&
    contrast(neutral, surface) < contrast(posPole, surface)
  check(
    'diverging: the midpoint recedes toward this mode surface',
    midRecedes,
    `neutral ${round(contrast(neutral, surface))}:1 vs poles ` +
      `${round(contrast(negPole, surface))}:1 / ${round(contrast(posPole, surface))}:1`,
  )
  const poleSep = cvdDelta(negPole, posPole)
  check(
    'diverging: the two poles separate under CVD',
    poleSep >= CVD_TARGET,
    `ΔE ${round(poleSep)} (≥${CVD_TARGET}) — colour is the only channel on a heatmap cell`,
  )
  const faintest = Math.min(...p.diverging.map((c) => contrast(c, surface)))
  check(
    'diverging: every step stays visible',
    faintest >= RAMP_CONTRAST,
    `worst ${round(faintest)}:1 (≥${RAMP_CONTRAST}:1)`,
  )

  // Polarity: the named poles must not read as an identity.
  check(
    'polarity: the poles are the diverging ends',
    p.polarity.negative === negPole && p.polarity.positive === posPole,
    'one family, two uses',
  )
  const nearestSeries = Math.min(
    ...p.category.flatMap((c) => [
      deltaE(p.polarity.negative, c),
      deltaE(p.polarity.positive, c),
    ]),
  )
  check(
    'polarity: sits outside the categorical wheel',
    nearestSeries >= SERIES_DISTANCE,
    `nearest series ΔE ${round(nearestSeries)} (≥${SERIES_DISTANCE})`,
  )
  const polarityMark = Math.min(
    contrast(p.polarity.negative, surface),
    contrast(p.polarity.positive, surface),
  )
  check(
    'polarity: usable as a mark',
    polarityMark >= MARK_CONTRAST,
    `worst ${round(polarityMark)}:1 (≥${MARK_CONTRAST}:1)`,
  )
}

// ── the rule that spans both modes ───────────────────────────────────────
console.log('\n── both modes ──')
// The same chart is repainted when the reader flips the theme, so the series
// cap the product may offer is the lower of the two — not each mode's own.
check(
  'series cap holds under a theme flip',
  Math.min(...caps) >= SERIES_CAP,
  `dark ${caps[0]}, light ${caps[1]} → operative cap ${Math.min(...caps)}`,
)

if (failures > 0) {
  // A thrown error is the non-zero exit; `process` would need Node's types.
  throw new Error(`${failures} palette checks failed`)
}
console.log('\nall passed')
