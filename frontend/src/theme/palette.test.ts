/**
 * The palette's claims, re-measured.
 *
 * `npm run test:palette` — Node runs this file directly (type stripping, no
 * bundler, no test framework, no new dependency), like the schedule and
 * table-format checks beside it.
 *
 * It exists because `palette.ts` carries a table of perceptual measurements
 * and tells the next person to re-run them, and until now there was nothing to
 * run: the numbers were a one-off that would quietly stop being true the first
 * time a hue was nudged. Writing this found one — the recorded CVD figures
 * were deuteranopia only, and the dark set's violet/olive pair separates by
 * 7.3 under tritanopia, under the ≥8 the file claimed to hold.
 *
 * The gates below are the ones `palette.ts` documents. Where a floor differs
 * per deficiency the reason is written at the check, not left as a number.
 */
import {
  contrastRatio, deltaE, hexToLinear, lightness, parseOklch, simulate, type Rgb,
} from './color.ts'
import { PALETTES, type ThemeName } from './palette.ts'
import { THEMES } from './tokens.ts'

// Red-green deficiency is roughly 1 man in 12; the palette is ordered to
// survive it. Tritanopia is nearer 1 in 10,000 and not sex-linked, so it is
// held to a lower floor rather than allowed to force a re-tune that would cost
// the many to help the few — see the note in `palette.ts`.
const ADJACENT_CVD = { deuteranopia: 8, protanopia: 8, tritanopia: 5 } as const
const ADJACENT_NORMAL = 15
const SURFACE_CONTRAST = 3
const RAMP_SURFACE_CONTRAST = 2
const RAMP_STEP_LIGHTNESS = 0.06

let failures = 0

function check(name: string, ok: boolean, detail: string): void {
  if (!ok) failures += 1
  console.log(ok ? `ok    ${name} — ${detail}` : `FAIL  ${name} — ${detail}`)
}

function atLeast(name: string, actual: number, floor: number, unit = ''): void {
  check(name, actual >= floor, `${actual.toFixed(2)}${unit} (floor ${floor}${unit})`)
}

const surfaceOf = (mode: ThemeName): Rgb => parseOklch(THEMES[mode].panel)

for (const mode of ['dark', 'light'] as ThemeName[]) {
  const p = PALETTES[mode]
  const surface = surfaceOf(mode)
  console.log(`\n── ${mode} ──`)

  // ── categorical: adjacent slots are what a reader compares ──────────────
  const category = p.category.map(hexToLinear)
  check(
    `${mode}: eight categorical slots`,
    p.category.length === 8,
    `${p.category.length} slots`,
  )

  let worstNormal = Infinity
  for (let i = 0; i < category.length - 1; i++) {
    worstNormal = Math.min(worstNormal, deltaE(category[i], category[i + 1]))
  }
  atLeast(`${mode}: adjacent categories, normal vision`, worstNormal, ADJACENT_NORMAL)

  for (const [kind, floor] of Object.entries(ADJACENT_CVD)) {
    let worst = Infinity
    let at = ''
    for (let i = 0; i < category.length - 1; i++) {
      const d = deltaE(
        simulate(kind as keyof typeof ADJACENT_CVD, category[i]),
        simulate(kind as keyof typeof ADJACENT_CVD, category[i + 1]),
      )
      if (d < worst) {
        worst = d
        at = `${p.category[i]}/${p.category[i + 1]}`
      }
    }
    check(
      `${mode}: adjacent categories, ${kind}`,
      worst >= floor,
      `${worst.toFixed(1)} at ${at} (floor ${floor})`,
    )
  }

  // A mark the same lightness as the panel it sits on is invisible whatever
  // its hue, so no slot may depend on the one beside it for relief.
  atLeast(
    `${mode}: every category slot against the surface`,
    Math.min(...category.map((c) => contrastRatio(c, surface))),
    SURFACE_CONTRAST,
    ':1',
  )

  // ── sequential ramp: ordered, so lightness must be too ──────────────────
  const ramp = p.ramp
  const ls = ramp.map(lightness)
  const rising = ls.every((l, i) => i === 0 || l > ls[i - 1])
  const falling = ls.every((l, i) => i === 0 || l < ls[i - 1])
  check(
    `${mode}: ramp lightness is monotone`,
    rising || falling,
    `${ls.map((l) => l.toFixed(2)).join(' → ')}`,
  )
  atLeast(
    `${mode}: ramp steps are told apart by lightness alone`,
    Math.min(...ls.slice(1).map((l, i) => Math.abs(l - ls[i]))),
    RAMP_STEP_LIGHTNESS,
  )
  atLeast(
    `${mode}: ramp step nearest the surface`,
    Math.min(...ramp.map((c) => contrastRatio(hexToLinear(c), surface))),
    RAMP_SURFACE_CONTRAST,
    ':1',
  )
  // "Near zero" must be the end that recedes, or the ramp reads backwards.
  const nearZero = contrastRatio(hexToLinear(ramp[0]), surface)
  const farEnd = contrastRatio(hexToLinear(ramp[ramp.length - 1]), surface)
  check(
    `${mode}: the low end of the ramp is the one that recedes`,
    nearZero < farEnd,
    `${nearZero.toFixed(2)}:1 → ${farEnd.toFixed(2)}:1`,
  )

  // ── diverging ramp: two arms and a vanishing middle ─────────────────────
  const div = p.diverging
  check(`${mode}: diverging has an odd number of steps`, div.length % 2 === 1,
    `${div.length} steps`)
  const arms = [hexToLinear(div[0]), hexToLinear(div[div.length - 1])]
  atLeast(`${mode}: diverging arms, normal vision`, deltaE(arms[0], arms[1]), ADJACENT_NORMAL)
  for (const kind of Object.keys(ADJACENT_CVD) as (keyof typeof ADJACENT_CVD)[]) {
    atLeast(
      `${mode}: diverging arms, ${kind}`,
      deltaE(simulate(kind, arms[0]), simulate(kind, arms[1])),
      ADJACENT_NORMAL,
    )
  }
  const divRgb = div.map(hexToLinear)
  let divAdjacent = Infinity
  for (let i = 0; i < divRgb.length - 1; i++) {
    divAdjacent = Math.min(divAdjacent, deltaE(divRgb[i], divRgb[i + 1]))
  }
  atLeast(`${mode}: adjacent diverging steps`, divAdjacent, 8)
  atLeast(
    `${mode}: diverging arms against the surface`,
    Math.min(contrastRatio(arms[0], surface), contrastRatio(arms[1], surface)),
    SURFACE_CONTRAST,
    ':1',
  )
  // The midpoint is the value that should disappear, so it is allowed to sit
  // closer to the surface than the arms — but not to vanish entirely.
  const middle = divRgb[(divRgb.length - 1) / 2]
  atLeast(
    `${mode}: diverging midpoint recedes without vanishing`,
    contrastRatio(middle, surface),
    RAMP_SURFACE_CONTRAST,
    ':1',
  )
  check(
    `${mode}: the midpoint is the step nearest the surface`,
    divRgb.every((c) => contrastRatio(c, surface) >= contrastRatio(middle, surface)),
    `${contrastRatio(middle, surface).toFixed(2)}:1`,
  )

  // ── the semantic pair: sign, told apart from the bars beside it ─────────
  check(
    `${mode}: positive is the colour the chart already had`,
    p.positive === p.category[0],
    `${p.positive} vs category[0] ${p.category[0]}`,
  )
  const negative = hexToLinear(p.negative)
  const positive = hexToLinear(p.positive)
  atLeast(`${mode}: negative against positive, normal vision`,
    deltaE(negative, positive), ADJACENT_NORMAL)
  for (const kind of Object.keys(ADJACENT_CVD) as (keyof typeof ADJACENT_CVD)[]) {
    atLeast(
      `${mode}: negative against positive, ${kind}`,
      deltaE(simulate(kind, negative), simulate(kind, positive)),
      ADJACENT_CVD[kind],
    )
  }
  atLeast(`${mode}: negative against the surface`,
    contrastRatio(negative, surface), SURFACE_CONTRAST, ':1')
}

console.log(failures === 0 ? '\nall passed' : `\n${failures} FAILED`)
