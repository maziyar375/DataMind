/**
 * Colour maths, so the palette's claims can be re-checked instead of believed.
 *
 * `palette.ts` carries a table of measured numbers — perceptual distances,
 * colour-blind separations, contrast against the chart's own surface — and
 * instructs the next person to re-run them after any change. Until this file
 * existed there was nothing to re-run: the numbers were a one-off measurement
 * that quietly stopped being true the first time a hue was nudged by eye.
 *
 * Deliberately dependency-free and DOM-free. It is imported by `palette.ts`
 * (which React renders from) *and* by `palette.test.ts`, which Node runs
 * directly with type stripping — no bundler, no test framework, no new
 * package on a frontend that has no other use for one.
 *
 * Three things are computed here:
 *
 * * **OKLab** (Ottosson 2020) — a perceptually uniform space, so a Euclidean
 *   distance in it means "how different these look". CIELAB's distances are
 *   notoriously wrong for blues, which is most of the dark palette.
 * * **Machado 2009** colour-vision-deficiency simulation — the matrices are
 *   the published severity-1.0 set, applied in *linear* RGB. Simulating the
 *   worst case rather than a partial one is the point: a palette that survives
 *   full dichromacy survives everything milder.
 * * **WCAG relative luminance**, for contrast against a surface. A mark the
 *   same lightness as the panel it sits on is invisible whatever its hue.
 */

export type Rgb = readonly [number, number, number]
export type Lab = readonly [number, number, number]

// ── sRGB ─────────────────────────────────────────────────────────────────
function toLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function fromLinear(channel: number): number {
  return channel <= 0.0031308
    ? channel * 12.92
    : 1.055 * channel ** (1 / 2.4) - 0.055
}

export function hexToLinear(hex: string): Rgb {
  const value = hex.replace('#', '')
  const full = value.length === 3 ? [...value].map((c) => c + c).join('') : value
  const byte = (i: number) => parseInt(full.slice(i * 2, i * 2 + 2), 16) / 255
  return [toLinear(byte(0)), toLinear(byte(1)), toLinear(byte(2))]
}

export function linearToHex([r, g, b]: Rgb): string {
  const channel = (v: number) => {
    const clamped = Math.max(0, Math.min(1, fromLinear(v)))
    return Math.round(clamped * 255).toString(16).padStart(2, '0')
  }
  return `#${channel(r)}${channel(g)}${channel(b)}`
}

/** True when a colour survives the round trip — i.e. it is inside sRGB. */
export function inGamut([r, g, b]: Rgb): boolean {
  return [r, g, b].every((v) => v >= -1e-4 && v <= 1 + 1e-4)
}

// ── OKLab ────────────────────────────────────────────────────────────────
export function linearToOklab([r, g, b]: Rgb): Lab {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ]
}

export function oklabToLinear([L, a, b]: Lab): Rgb {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ]
}

/** `oklch(L C H)` — the form every token in `tokens.ts` is written in. */
export function oklchToLinear(L: number, C: number, hueDegrees: number): Rgb {
  const h = (hueDegrees * Math.PI) / 180
  return oklabToLinear([L, C * Math.cos(h), C * Math.sin(h)])
}

/** Parses the `oklch(L C H / A)` strings the design tokens are written in. */
export function parseOklch(token: string): Rgb {
  const match = token.match(
    /oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)/i,
  )
  if (!match) throw new Error(`not an oklch() token: ${token}`)
  return oklchToLinear(Number(match[1]), Number(match[2]), Number(match[3]))
}

export function lightness(hex: string): number {
  return linearToOklab(hexToLinear(hex))[0]
}

/**
 * Perceptual distance, ×100 — the units the palette table is written in.
 *
 * Roughly: under 8 two colours are the same colour to a hurried reader; 15 is
 * a comfortable "obviously different" for normal vision.
 */
export function deltaE(a: Rgb, b: Rgb): number {
  const [l1, a1, b1] = linearToOklab(a)
  const [l2, a2, b2] = linearToOklab(b)
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2) * 100
}

// ── colour vision deficiency ─────────────────────────────────────────────
/** Machado, Oliveira & Fernandes (2009), severity 1.0, in linear RGB. */
const CVD_MATRICES = {
  protanopia: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranopia: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
  tritanopia: [
    [1.255528, -0.076749, -0.178779],
    [-0.078411, 0.930809, 0.147602],
    [0.004733, 0.691367, 0.3039],
  ],
} as const

export type CvdKind = keyof typeof CVD_MATRICES
export const CVD_KINDS = Object.keys(CVD_MATRICES) as CvdKind[]

export function simulate(kind: CvdKind, [r, g, b]: Rgb): Rgb {
  const m = CVD_MATRICES[kind]
  return [
    m[0][0] * r + m[0][1] * g + m[0][2] * b,
    m[1][0] * r + m[1][1] * g + m[1][2] * b,
    m[2][0] * r + m[2][1] * g + m[2][2] * b,
  ]
}

/** The worst separation of two colours across every simulated deficiency. */
export function worstCvdDeltaE(a: Rgb, b: Rgb): number {
  return Math.min(
    ...CVD_KINDS.map((kind) => deltaE(simulate(kind, a), simulate(kind, b))),
  )
}

// ── contrast ─────────────────────────────────────────────────────────────
function relativeLuminance([r, g, b]: Rgb): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio, 1:1 to 21:1. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [light, dark] = [relativeLuminance(a), relativeLuminance(b)].sort(
    (x, y) => y - x,
  )
  return (light + 0.05) / (dark + 0.05)
}
