/**
 * The field behind the sign-in card — the one surface a visitor can play with
 * before they have an account.
 *
 * Two layers, both `aria-hidden` and both `pointer-events: none`, so none of
 * it is ever between someone and the form:
 *
 *  - **The waves.** Two dotted sheets growing out of the bottom corners, drawn
 *    on a single canvas. They flow on their own, part around the pointer, and
 *    carry a ring outward from a click anywhere on the page.
 *  - **The instruments.** Chart, table and database glyphs scattered down both
 *    margins, on a parallax against the pointer, brightening as it passes.
 *    They are the product's own objects — a bar chart, a donut, a result
 *    table, a database — rather than abstract shapes, because the thing being
 *    signed into reads databases and draws charts.
 *
 * The orbit rings are *not* here: they are centred on the mark, the page lays
 * the mark out, so they live in `LoginPage` where that centring is free.
 *
 * **Colour is never named in this file.** The canvas reads `--wave-a` and
 * `--wave-b` off `.rm-auth`, so both themes are decided in `styles.css` beside
 * the tokens, and a theme switch is picked up on the next frame.
 *
 * `prefers-reduced-motion` stops the clock — the sheets hold a still frame and
 * the glyphs stop drifting — but the pointer still moves both. What that
 * setting asks us to stop is motion the visitor did not cause; a wake that
 * follows a hand already in flight is not that.
 */
import { useEffect, useRef } from 'react'

/**
 * One dotted wave field.
 *
 * A field is a stack of smooth curves, each sampled as loose particles rather
 * than stroked as a line, and each curve is the sum of three sines — the
 * `y = baseY + a1·sin(x·f1 + p1) + a2·sin(x·f2 + p2) + …` shape, with a third
 * harmonic so no layer ever resolves into a recognisable single wave.
 *
 * Everything is in viewport fractions, so the field reflows rather than
 * scaling. The **outer** end of each field is pushed well off the screen: its
 * edge fade then happens where nobody can see it, and what remains on screen is
 * a surface cut by the window rather than a rectangle of particles with a
 * visible boundary. The inner end fades in view, which is what keeps the middle
 * of the page — where the card is — clear.
 */
type Wave = {
  /** Where the field runs. Outer end off-screen, inner end toward the middle. */
  x0: number; x1: number
  /** Baseline of the frontmost layer, and of the backmost. */
  y0: number; y1: number
  /** Curves in the stack. Twenty each: the count is bounded from below by the
      brief and from above by the band, since neighbours closer than about
      12px stop reading as separate curves and start reading as a cloud. */
  layers: number
  /** Cycles across the field for each harmonic, and their heights as a
      fraction of the viewport. */
  freq: [number, number, number]
  amp: [number, number, number]
  /** Radians per second for each harmonic. Different signs and speeds mean the
      three never come back into step, so the surface never repeats visibly. */
  drift: [number, number, number]
  /** Per-layer amplitude, frequency and phase all vary off this. Two unrelated
      seeds are most of why the fields are complementary and not copies. */
  seed: number
  color: '--wave-a' | '--wave-b'
}

const WAVES: Wave[] = [
  // Out of the bottom-left corner, rising and falling on its way to the lower
  // centre: the lower harmonic is the tallest, so this one reads as a swell.
  {
    x0: -0.18, x1: 0.34, y0: 1.04, y1: 0.80, layers: 20,
    freq: [1.9, 3.5, 6.1], amp: [0.027, 0.012, 0.0045],
    drift: [0.055, -0.084, 0.121], seed: 0.31, color: '--wave-a',
  },
  // Down to the bottom-right corner. Fewer, flatter layers and a stronger
  // second harmonic, so it ripples where the left one swells.
  {
    x0: 1.18, x1: 0.66, y0: 1.04, y1: 0.825, layers: 20,
    freq: [1.4, 4.4, 7.3], amp: [0.023, 0.016, 0.004],
    drift: [-0.069, 0.048, -0.095], seed: 2.74, color: '--wave-b',
  },
]

/** Particle spacing on the frontmost layer, and how much it opens up by the
    back one. Spacing is what makes the surface dense low and sparse high. */
const STEP_FRONT = 6
const STEP_BACK = 2.1
/**
 * How the layers distribute up the band. This was 1.25 — crowding them toward
 * the bottom — and it was wrong: at that bias the lowest curves sat 4px apart
 * while each swung through 40px, so they overlapped into a cloud of dots
 * instead of reading as separate curves, which is the entire look. Even
 * spacing keeps roughly 12px between neighbours, and the density still climbs
 * toward the bottom through the two things that can do it without stacking
 * curves on top of each other: particle spacing along each curve, and the fade.
 */
const STACK_BIAS = 1
/** Fraction of a field's width spent fading in at each end. */
const EDGE = 0.26

/** How far the pointer is felt, and how hard it shoves a particle aside. */
const REACH = 150
const SHOVE = 20
/** A click's ring: how fast it travels, how wide it is, how long it lives. */
const RING_SPEED = 620
const RING_BAND = 78
const RING_LIFE = 1.5

/**
 * Deterministic scatter in [0,1). The particles must not sit on a rigid grid —
 * a grid is what makes a dot field read as a texture rather than a surface —
 * but they also must not crawl, so the offset has to be a pure function of
 * which particle it is and nothing else. No RNG, no stored state.
 */
function jitter(a: number, b: number) {
  const n = Math.sin(a * 127.1 + b * 311.7) * 43758.5453
  return n - Math.floor(n)
}

/** Particles one field may draw in a frame. The buffer is sized to this and
    the sampling loops stop at it, so no window size can outgrow either. */
const MAX_DOTS = 6000

/**
 * The dots that ride the rings around the mark.
 *
 * They are drawn here, on the canvas, rather than being `<circle>`s inside a
 * turning SVG — which is what they were, and it cost less than half the frame
 * rate. The rings themselves are concentric, so turning them changes nothing
 * anyone can see; only these move, and a canvas already painting two thousand
 * dots a frame paints eight more for free.
 *
 * `r` and `size` are in the plate's own 900-unit viewBox, so they scale with
 * whatever `.rm-auth-orbit` is sized to at this breakpoint. `deg` is measured
 * anticlockwise from three o'clock. `far` marks the outer ring — the same
 * distinction `is-far` makes for the rings in `styles.css`.
 */
const ORBIT = [
  { r: 182, deg: 35, size: 5.6, far: false, alt: false },
  { r: 182, deg: 152, size: 5, far: false, alt: true },
  { r: 324, deg: 68, size: 6.2, far: true, alt: false },
  { r: 324, deg: 118, size: 5.3, far: true, alt: true },
  { r: 324, deg: 15, size: 5, far: true, alt: false },
  { r: 182, deg: 168, size: 5.6, far: false, alt: true },
  { r: 324, deg: 295, size: 5.3, far: true, alt: false },
  { r: 182, deg: 240, size: 5, far: false, alt: true },
]
/** Seconds for a full turn. Slow enough to be noticed rather than watched. */
const ORBIT_PERIOD = 100

/** Opacity steps the sheet is quantised into so it can be painted in batches. */
const BUCKETS = 12

/**
 * The instruments, placed by fraction of the viewport.
 *
 * `depth` is the parallax throw in pixels: the small far-off marks move least,
 * the big near cards move most, which is the whole of the depth cue. Every
 * position stays clear of the centre column the card occupies — the widest
 * breakpoint puts the card at 400px, so nothing sits between 34% and 66%.
 */
type Glyph = { x: number; y: number; depth: number; drift: number; art: React.ReactNode }

/** The card every instrument is drawn on. */
function Frame({ w, h, r = 12 }: { w: number; h: number; r?: number }) {
  return <rect x="0.6" y="0.6" width={w - 1.2} height={h - 1.2} rx={r} className="rm-glyph-frame" />
}

const GLYPHS: Glyph[] = [
  // ── left margin ──
  {
    // An area chart: the filled body is what makes it read as a chart at this
    // size rather than as a zigzag.
    x: 21, y: 29, depth: 17, drift: 0,
    art: (
      <svg width="162" height="88" viewBox="0 0 162 88" fill="none" aria-hidden="true">
        <Frame w={162} h={88} r={13} />
        <path className="rm-glyph-fill" d="M20 64L44 46L62 55L82 30L102 41L122 21L146 34V72H20Z" />
        <path className="rm-glyph-line" d="M20 64L44 46L62 55L82 30L102 41L122 21L146 34" />
        <circle className="rm-glyph-halo" cx="122" cy="21" r="6.5" />
        <circle className="rm-glyph-node" cx="122" cy="21" r="3.2" />
      </svg>
    ),
  },
  {
    // A donut with two live segments against a track, beside its legend.
    x: 18, y: 56, depth: 23, drift: 1,
    art: (
      <svg width="132" height="72" viewBox="0 0 132 72" fill="none" aria-hidden="true">
        <Frame w={132} h={72} r={12} />
        <circle className="rm-glyph-track" cx="34" cy="36" r="15" />
        <path className="rm-glyph-arc-a" d="M34 21A15 15 0 0 1 43.6 47.5" />
        <path className="rm-glyph-arc-b" d="M43.6 47.5A15 15 0 0 1 22.5 45.6" />
        <g className="rm-glyph-rows">
          <rect x="62" y="23" width="52" height="5" rx="2.5" />
          <rect x="62" y="33.5" width="38" height="5" rx="2.5" />
          <rect x="62" y="44" width="45" height="5" rx="2.5" />
        </g>
      </svg>
    ),
  },
  {
    // A completion ring — the one glyph that is a number rather than a series.
    x: 32, y: 14, depth: 11, drift: 2,
    art: (
      <svg width="56" height="56" viewBox="0 0 56 56" fill="none" aria-hidden="true">
        <Frame w={56} h={56} r={15} />
        <circle className="rm-glyph-track" cx="28" cy="28" r="11.5" />
        <path className="rm-glyph-arc-a" d="M28 16.5A11.5 11.5 0 1 1 17.2 31.9" />
      </svg>
    ),
  },
  {
    // A result table: a header rule is what tells it apart from the donut's
    // legend rows beside it.
    x: 11, y: 42, depth: 9, drift: 3,
    art: (
      <svg width="104" height="70" viewBox="0 0 104 70" fill="none" aria-hidden="true">
        <Frame w={104} h={70} r={11} />
        <g className="rm-glyph-head">
          <rect x="14" y="15" width="24" height="5" rx="2.5" />
          <rect x="46" y="15" width="18" height="5" rx="2.5" />
          <rect x="72" y="15" width="18" height="5" rx="2.5" />
        </g>
        <path className="rm-glyph-rule" d="M14 27h76" />
        <g className="rm-glyph-rows">
          <rect x="14" y="34" width="24" height="4.5" rx="2.25" />
          <rect x="46" y="34" width="14" height="4.5" rx="2.25" />
          <rect x="72" y="34" width="18" height="4.5" rx="2.25" />
          <rect x="14" y="46" width="20" height="4.5" rx="2.25" />
          <rect x="46" y="46" width="18" height="4.5" rx="2.25" />
          <rect x="72" y="46" width="12" height="4.5" rx="2.25" />
        </g>
      </svg>
    ),
  },
  { x: 27, y: 73, depth: 13, drift: 4, art: <Matrix cols={5} rows={3} /> },

  // ── right margin ──
  {
    // Pill bars rather than rectangles, and the series climbs across the two
    // brand hues instead of sitting in one.
    x: 64, y: 11, depth: 15, drift: 2,
    art: (
      <svg width="122" height="88" viewBox="0 0 122 88" fill="none" aria-hidden="true">
        <Frame w={122} h={88} r={13} />
        <g className="rm-glyph-bars">
          <rect x="20" y="50" width="9" height="20" rx="4.5" />
          <rect x="35" y="40" width="9" height="30" rx="4.5" />
          <rect x="50" y="46" width="9" height="24" rx="4.5" />
          <rect x="65" y="30" width="9" height="40" rx="4.5" />
          <rect x="80" y="36" width="9" height="34" rx="4.5" />
          <rect x="95" y="20" width="9" height="50" rx="4.5" />
        </g>
        <path className="rm-glyph-rule" d="M16 70h90" />
      </svg>
    ),
  },
  {
    // The database. Its top disc is filled so the cylinder reads as a solid
    // rather than as three loose ellipses.
    x: 72, y: 36, depth: 21, drift: 0,
    art: (
      <svg width="78" height="78" viewBox="0 0 78 78" fill="none" aria-hidden="true">
        <Frame w={78} h={78} r={18} />
        <g className="rm-glyph-db">
          <path d="M22 26v11c0 3.7 7.6 6.6 17 6.6s17-2.9 17-6.6V26" />
          <path d="M22 37v11c0 3.7 7.6 6.6 17 6.6s17-2.9 17-6.6V37" />
          <ellipse className="rm-glyph-db-top" cx="39" cy="26" rx="17" ry="6.6" />
        </g>
      </svg>
    ),
  },
  {
    x: 75, y: 60, depth: 12, drift: 3,
    art: (
      <svg width="110" height="68" viewBox="0 0 110 68" fill="none" aria-hidden="true">
        <Frame w={110} h={68} r={11} />
        <path className="rm-glyph-fill-b" d="M16 46L38 33L54 40L74 21L96 30V54H16Z" />
        <path className="rm-glyph-line-b" d="M16 46L38 33L54 40L74 21L96 30" />
        <circle className="rm-glyph-node-b" cx="96" cy="30" r="3" />
      </svg>
    ),
  },
  { x: 80, y: 22, depth: 7, drift: 1, art: <Matrix cols={5} rows={5} /> },
  { x: 86, y: 47, depth: 9, drift: 4, art: <Matrix cols={4} rows={3} /> },
]

/** The dot-grid patches — a schema's shape with the labels too far to read. */
function Matrix({ cols, rows }: { cols: number; rows: number }) {
  const step = 11
  const dots: React.ReactNode[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      dots.push(<circle key={`${r}-${c}`} cx={c * step + 2} cy={r * step + 2} r="1.7" />)
    }
  }
  return (
    <svg
      width={(cols - 1) * step + 4}
      height={(rows - 1) * step + 4}
      fill="none"
      aria-hidden="true"
      className="rm-glyph-matrix"
    >
      {dots}
    </svg>
  )
}

export default function AuthScene() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const glyphsRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const host = glyphsRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const calm = window.matchMedia('(prefers-reduced-motion: reduce)')
    const marks = host ? Array.from(host.children) as HTMLElement[] : []

    /* The pointer is tracked twice: where it actually is, and where the scene
       currently believes it is. Everything reads the second one and it eases
       toward the first, which is what turns a stream of discrete mouse events
       into a wake that has weight. */
    let aimX = 0, aimY = 0, atX = 0, atY = 0
    /* `want` is whether the pointer is on the page at all; `grip` eases toward
       it, so leaving the window drains the wake instead of switching it off. */
    let want = 0, grip = 0
    let width = 0, height = 0
    const rings: { x: number; y: number; born: number }[] = []

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = window.innerWidth
      height = window.innerHeight
      canvas!.width = Math.round(width * dpr)
      canvas!.height = Math.round(height * dpr)
      canvas!.style.width = `${width}px`
      canvas!.style.height = `${height}px`
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0)
      if (!want) { aimX = atX = width * 0.5; aimY = atY = height * 0.72 }
    }
    resize()
    window.addEventListener('resize', resize)

    function onMove(e: PointerEvent) {
      aimX = e.clientX
      aimY = e.clientY
      want = 1
    }
    function onLeave() { want = 0 }
    function onDown(e: PointerEvent) {
      rings.push({ x: e.clientX, y: e.clientY, born: performance.now() / 1000 })
      if (rings.length > 3) rings.shift()
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    window.addEventListener('pointerdown', onDown, { passive: true })
    document.addEventListener('pointerleave', onLeave)

    /* Read once per frame rather than per dot: `getPropertyValue` is a style
       resolution, and 1200 of them a frame is the whole budget. */
    let colors: Record<string, string> = {}
    let orbitAlpha = 0.72
    /* Where the ring plate is, in viewport coordinates. Re-read every tenth
       frame rather than every frame: `getBoundingClientRect` forces layout, and
       six reads a second is enough to survive a resize, a font landing late or
       a scroll, none of which happen mid-gesture. */
    let plateBox: DOMRect | null = null
    let sinceMeasure = 99
    function readPalette() {
      const style = getComputedStyle(canvas!)
      colors = {
        '--wave-a': style.getPropertyValue('--wave-a').trim() || '#5b8cff',
        '--wave-b': style.getPropertyValue('--wave-b').trim() || '#ff5bab',
      }
      const plate = document.querySelector('.rm-auth-orbit')
      orbitAlpha = plate ? Number(getComputedStyle(plate).opacity) || 0.72 : 0.72
    }
    readPalette()
    const themeWatch = new MutationObserver(readPalette)
    themeWatch.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme', 'style'],
    })

    /* Allocated once and reused every frame: a per-frame array here would hand
       the collector two thousand objects sixty times a second, which shows up
       as stutter rather than as slowness. Sized to `MAX_DOTS`, which the
       sampling loops also stop at, so no window can outgrow it. */
    const dots = new Float32Array(MAX_DOTS * 4)
    const counts = new Uint16Array(BUCKETS)

    let raf = 0
    const start = performance.now() / 1000

    function frame(nowMs: number) {
      raf = requestAnimationFrame(frame)
      const now = nowMs / 1000
      /* Reduced motion freezes the clock, not the pointer. */
      const t = calm.matches ? 0 : now - start

      atX += (aimX - atX) * 0.09
      atY += (aimY - atY) * 0.09
      grip += (want - grip) * 0.06

      // ── the instruments ──
      for (let i = 0; i < marks.length; i++) {
        const mark = marks[i]
        const g = GLYPHS[i]
        if (!g) continue
        const cx = (g.x / 100) * width
        const cy = (g.y / 100) * height
        const px = ((atX / width) - 0.5) * -2
        const py = ((atY / height) - 0.5) * -2
        mark.style.setProperty('--tx', `${px * g.depth}px`)
        mark.style.setProperty('--ty', `${py * g.depth}px`)
        const d = Math.hypot(atX - cx, atY - cy)
        const lit = grip * Math.max(0, 1 - d / 340) ** 1.6
        mark.style.setProperty('--lit', lit.toFixed(3))
      }

      // ── the waves ──
      ctx!.clearRect(0, 0, width, height)

      while (rings.length && now - rings[0].born > RING_LIFE) rings.shift()

      for (const w of WAVES) {
        const fx0 = w.x0 * width, fx1 = w.x1 * width
        const span = fx1 - fx0
        const fy0 = w.y0 * height, fy1 = w.y1 * height

        /* Pass one solves every particle and files it under an opacity bucket;
           nothing is painted until pass two. */
        let n = 0
        counts.fill(0)

        for (let L = 0; L < w.layers; L++) {
          const d = L / (w.layers - 1)
          const baseY = fy0 + (fy1 - fy0) * d ** STACK_BIAS

          /* Each layer gets its own amplitude, frequency and phase off two
             out-of-step wobbles, so neighbouring curves never run parallel —
             which is what a stack of identical sines looks like, and it looks
             like corduroy. Amplitude also falls with depth: a wave further
             away is a smaller wave. */
          const wa = Math.sin(d * 5.3 + w.seed * 3.1)
          const wb = Math.sin(d * 3.1 + w.seed * 7.7)
          const scale = height * (1 - 0.5 * d) * (0.78 + 0.22 * wa)
          const a1 = w.amp[0] * scale, a2 = w.amp[1] * scale, a3 = w.amp[2] * scale
          const f1 = w.freq[0] * (1 + 0.07 * wa)
          const f2 = w.freq[1] * (1 + 0.06 * wb)
          const f3 = w.freq[2] * (1 + 0.05 * wa)
          const ph = w.seed * 6.2832 + d * 2.7

          const step = STEP_FRONT * (1 + STEP_BACK * d)
          const count = Math.max(2, Math.ceil(Math.abs(span) / step))
          const fade = (1 - d) ** 0.8
          const rad = 1.7 - 0.85 * d

          for (let i = 0; i <= count; i++) {
            const u = i / count
            const jx = jitter(L, i), jy = jitter(i, L)

            const th = u * 6.2832
            let x = fx0 + span * u + (jx - 0.5) * step * 0.6
            let y = baseY
              + a1 * Math.sin(th * f1 + ph + t * w.drift[0])
              + a2 * Math.sin(th * f2 + ph * 1.7 + t * w.drift[1])
              + a3 * Math.sin(th * f3 + ph * 0.6 + t * w.drift[2])
              + (jy - 0.5) * 3.4

            /* Smoothstepped at both ends. The outer end's fade is off-screen,
               so what this actually buys is the inner one: the field thins to
               nothing before it reaches the card. */
            const e = Math.min(u, 1 - u) / EDGE
            const edge = e >= 1 ? 1 : e * e * (3 - 2 * e)

            let a = 1.15 * fade * edge
            let r = rad

            if (grip > 0.004) {
              const dx = x - atX, dy = y - atY
              const dist = Math.hypot(dx, dy)
              if (dist < REACH) {
                const fall = (1 - dist / REACH) ** 2 * grip
                const k = dist || 1
                x += (dx / k) * fall * SHOVE
                y += (dy / k) * fall * SHOVE
                a += fall * 0.5
                r += fall * 1.3
              }
            }

            for (const ring of rings) {
              const age = now - ring.born
              const edgeD = Math.abs(Math.hypot(x - ring.x, y - ring.y) - age * RING_SPEED)
              if (edgeD < RING_BAND) {
                const k = (1 - edgeD / RING_BAND) * (1 - age / RING_LIFE)
                a += k * 0.55
                r += k * 1.5
              }
            }

            if (a <= 1 / BUCKETS) continue
            const b = a >= 1 ? BUCKETS - 1 : (a * BUCKETS) | 0
            const o = n * 4
            dots[o] = x; dots[o + 1] = y; dots[o + 2] = r; dots[o + 3] = b
            counts[b]++
            n++
            if (n >= MAX_DOTS) break
          }
          if (n >= MAX_DOTS) break
        }

        /* Pass two paints each bucket as ONE path. `globalAlpha` and `fill` are
           the expensive calls, and a fill per particle costs about half the
           frame at this density — twelve of them costs nothing, and twelve
           opacity steps are indistinguishable on a two-pixel dot. */
        ctx!.fillStyle = colors[w.color]
        for (let b = 0; b < BUCKETS; b++) {
          if (!counts[b]) continue
          ctx!.globalAlpha = (b + 1) / BUCKETS
          ctx!.beginPath()
          for (let k = 0; k < n; k++) {
            const o = k * 4
            if (dots[o + 3] !== b) continue
            const x = dots[o], y = dots[o + 1], r = dots[o + 2]
            ctx!.moveTo(x + r, y)
            ctx!.arc(x, y, r, 0, 6.2832)
          }
          ctx!.fill()
        }
      }
      ctx!.globalAlpha = 1

      // ── the dots around the mark ──
      if (++sinceMeasure >= 10) {
        sinceMeasure = 0
        plateBox = document.querySelector('.rm-auth-orbit')?.getBoundingClientRect() ?? null
      }
      if (plateBox && plateBox.width > 0) {
        const scale = plateBox.width / 900
        const cx = plateBox.left + plateBox.width / 2
        const cy = plateBox.top + plateBox.height / 2
        const spin = (t / ORBIT_PERIOD) * Math.PI * 2
        for (const o of ORBIT) {
          const ang = (o.deg * Math.PI) / 180 + spin
          const x = cx + Math.cos(ang) * o.r * scale
          const y = cy - Math.sin(ang) * o.r * scale

          /* The same dissolve the rings get from their CSS mask, computed the
             same way, so a dot and the ring it rides fade out together as they
             pass behind the heading. */
          const down = (y - plateBox.top) / plateBox.height
          let a = down <= 0.5 ? 1 : down >= 0.7 ? 0 : 1 - (down - 0.5) / 0.2
          if (a <= 0.01) continue
          a *= (o.far ? 0.55 : 0.85) * orbitAlpha

          ctx!.globalAlpha = a
          ctx!.fillStyle = colors[o.alt ? '--wave-b' : '--wave-a']
          ctx!.beginPath()
          ctx!.arc(x, y, o.size * scale, 0, 6.2832)
          ctx!.fill()
        }
      }

      ctx!.globalAlpha = 1
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      themeWatch.disconnect()
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerdown', onDown)
      document.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  return (
    <>
      <canvas ref={canvasRef} className="rm-auth-waves" aria-hidden="true" />
      <div ref={glyphsRef} className="rm-auth-glyphs" aria-hidden="true">
        {GLYPHS.map((g, i) => (
          <div
            key={i}
            className="rm-auth-glyph"
            style={{ left: `${g.x}%`, top: `${g.y}%` }}
          >
            {/* The parallax is on the wrapper and the idle drift on the child:
                one element cannot hold two transforms, and collapsing them into
                one animated value would make the pointer fight the keyframes. */}
            <div className="rm-auth-glyph-drift" style={{ animationDelay: `${g.drift * -1.7}s` }}>
              {g.art}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
