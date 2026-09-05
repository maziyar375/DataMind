/**
 * The field behind the sign-in card — the one surface a visitor can play with
 * before they have an account.
 *
 * Three layers, all `aria-hidden` and none of them able to take a pointer
 * event, so nothing here is ever between someone and the form:
 *
 *  - **The line.** One long curve that crests under the mark and falls away
 *    past both edges of the window. It is the scene's spine: the mark sits on
 *    its crest, four cards hang off it at measured points, and slow motes
 *    travel it left to right — the path a question takes through the product.
 *  - **The waves.** Two dotted sheets growing out of the bottom corners, drawn
 *    on the same canvas. They flow on their own, part around the pointer, and
 *    carry a ring outward from a click anywhere on the page.
 *  - **The cards.** Four of them, one per thing this product actually does:
 *    the databases it reads, the question asked in plain language, the SQL
 *    checked before it runs, the answer that comes back. Left to right through
 *    the mark they are in that order, which is the order they happen in.
 *
 * The orbit rings are *not* here: they are centred on the mark, the page lays
 * the mark out, so they live in `LoginPage` where that centring is free.
 *
 * **Colour is never named in this file.** The canvas reads `--wave-a` and
 * `--wave-b` off `.rm-auth`, so both themes are decided in `styles.css` beside
 * the tokens, and a theme switch is picked up on the next frame.
 *
 * `prefers-reduced-motion` stops the clock — the sheets hold a still frame, the
 * motes hold their places, the cards stop drifting — but the pointer still
 * moves all three. What that setting asks us to stop is motion the visitor did
 * not cause; a wake that follows a hand already in flight is not that.
 */
import { useEffect, useRef } from 'react'

/* ── the line ──────────────────────────────────────────────────────────── */

/**
 * The curve everything in the margins is organised on.
 *
 * A parabola: `y = apexY + drop · s²` for `s` running −1 … 1 across the span.
 * It is drawn as a quadratic Bézier, which is not an approximation of that —
 * a quadratic Bézier *is* a parabola, so the curve the canvas strokes and the
 * curve the cards are placed on are the same object solved two ways, and a
 * card can never drift off the line it is supposed to hang from.
 *
 * The apex is taken from the orbit plate rather than from a percentage of the
 * window, so the crest stays under the mark at every breakpoint — including the
 * two short-viewport ones where the mark shrinks and moves.
 */
/** How far past each edge of the window the ends are pushed, in widths. Both
    ends leave the frame, so the line reads as a piece of something larger
    rather than as an object with two visible tips. */
const ARC_OVER = 0.13
/** How far the curve has fallen by those ends, in heights. */
const ARC_DROP = 0.34
/** How far below the mark's centre the crest sits, as a fraction of the plate.
    Not zero: on the centre line the curve would cut the mark in half, and a
    little lower it passes through the mark's foot, which reads as the mark
    resting on it. */
const ARC_SEAT = 0.09
/** Kept clear either side of the page's centre column, in px. The column is
    440 at its widest, so this is its half plus room to breathe: no card can be
    placed inside it however wide the window gets. */
const ARC_CLEAR = 250
/** Kept clear of the window's own edges, in px. */
const ARC_PAD = 26
/**
 * How much room the mark is given on the line, as multiples of the ring
 * plate's width: the line is gone by the first and back to full by the second.
 *
 * The mark already has three rings, eight dots travelling them and a glow of
 * its own inside a 520px circle. A line ruled through all of that is one system
 * too many in one place — so the line does not arrive at the mark at all. It
 * dissolves about a hundred pixels outside the outermost ring and picks up
 * again on the far side, which reads as one curve passing behind the brand
 * rather than as two lines pointing at it.
 */
const ARC_HUSH = 0.46
const ARC_FULL = 0.88

/** Motes riding the line, and how many seconds one takes to cross it. */
const FLOW = 16
const FLOW_PERIOD = 78

/** Weight of the motes riding the line. The line's own two weights are not
    here: they differ by theme — a hairline that reads on navy is a scratch on
    paper — so like every other colour decision on this screen they are taken
    from `.rm-auth` at run time. */
const FLOW_INK = 0.6

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
 * A card hanging off the line.
 *
 * `at` is where it sits along the half of the line beyond the clearance — 0 is
 * as close to the centre column as anything is allowed, 1 is the window's edge
 * — so the four keep their relative places at every width instead of being four
 * percentages that happen to work at one. `w`/`h` are the art's own pixel size,
 * which is what centres the card on its anchor and what stops it from being
 * clamped half off the screen.
 */
type Node = {
  side: -1 | 1
  at: number
  w: number
  h: number
  /** Parallax throw in px. It grows with distance from the mark: the outer
      pair sit lower on the curve, which reads as nearer, and near things move
      more. That is the whole of the depth cue. */
  depth: number
  drift: number
  /** The inner pair. They are the first thing to go when the margins narrow. */
  inner?: boolean
  art: React.ReactNode
}

/** The card every instrument is drawn on. */
function Frame({ w, h, r = 12 }: { w: number; h: number; r?: number }) {
  return <rect x="0.6" y="0.6" width={w - 1.2} height={h - 1.2} rx={r} className="rm-glyph-frame" />
}

const NODES: Node[] = [
  {
    // **The databases.** A cylinder wired to three endpoints — the shape of
    // "your Postgres, your MySQL, your SQL Server" without naming them.
    side: -1, at: 0.52, w: 150, h: 98, depth: 22, drift: 0,
    art: (
      <svg width="150" height="98" viewBox="0 0 150 98" fill="none" aria-hidden="true">
        <Frame w={150} h={98} r={17} />
        <g className="rm-glyph-db">
          <path d="M28 37v12c0 3.9 7.8 7 17.5 7S63 52.9 63 49V37" />
          <path d="M28 49v12c0 3.9 7.8 7 17.5 7S63 64.9 63 61V49" />
          <ellipse className="rm-glyph-db-top" cx="45.5" cy="37" rx="17.5" ry="7" />
        </g>
        <g className="rm-glyph-link">
          <path d="M67 49h24" />
          <path d="M91 49V33h20" />
          <path d="M91 49h20" />
          <path d="M91 49v16h20" />
        </g>
        <circle className="rm-glyph-node" cx="114" cy="33" r="3.4" />
        <circle className="rm-glyph-node" cx="114" cy="49" r="3.4" />
        <circle className="rm-glyph-node-b" cx="114" cy="65" r="3.4" />
      </svg>
    ),
  },
  {
    // **The question.** A bubble with a prompt half-typed in it: the caret is
    // what makes it read as something being asked rather than something said.
    side: -1, at: 0.16, w: 116, h: 80, depth: 13, drift: 2, inner: true,
    art: (
      <svg width="116" height="80" viewBox="0 0 116 80" fill="none" aria-hidden="true">
        <Frame w={116} h={80} r={15} />
        <path
          className="rm-glyph-bubble"
          d="M24 18h68a9 9 0 0 1 9 9v18a9 9 0 0 1-9 9H43l-11 9v-9h-8a9 9 0 0 1-9-9V27a9 9 0 0 1 9-9z"
        />
        <g className="rm-glyph-rows">
          <rect x="28" y="27" width="46" height="5" rx="2.5" />
          <rect x="28" y="38" width="26" height="5" rx="2.5" />
        </g>
        <rect className="rm-glyph-caret" x="58" y="37" width="3" height="7" rx="1.5" />
        <path className="rm-glyph-spark" d="M93 54l1.7 4.3 4.3 1.7-4.3 1.7L93 66l-1.7-4.3-4.3-1.7 4.3-1.7z" />
      </svg>
    ),
  },
  {
    // **The check.** Query text with a badge on it — the one promise this
    // product makes that a visitor cannot see for themselves before signing in.
    side: 1, at: 0.16, w: 116, h: 80, depth: 13, drift: 3, inner: true,
    art: (
      <svg width="116" height="80" viewBox="0 0 116 80" fill="none" aria-hidden="true">
        <Frame w={116} h={80} r={15} />
        <g className="rm-glyph-key">
          <rect x="22" y="20" width="26" height="6" rx="3" />
        </g>
        <g className="rm-glyph-rows">
          <rect x="52" y="20" width="18" height="6" rx="3" />
          <rect x="22" y="33" width="14" height="6" rx="3" />
          <rect x="40" y="33" width="34" height="6" rx="3" />
          <rect x="22" y="46" width="22" height="6" rx="3" />
        </g>
        <circle className="rm-glyph-badge" cx="86" cy="50" r="13" />
        <path className="rm-glyph-check" d="M80 50.5l4 4 8-8.5" />
      </svg>
    ),
  },
  {
    // **The answer.** The series climbs across both brand hues, so it reads as
    // a measure with a direction rather than six identical marks.
    side: 1, at: 0.52, w: 150, h: 98, depth: 22, drift: 1,
    art: (
      <svg width="150" height="98" viewBox="0 0 150 98" fill="none" aria-hidden="true">
        <Frame w={150} h={98} r={17} />
        <g className="rm-glyph-bars">
          <rect x="24" y="52" width="10" height="22" rx="5" />
          <rect x="42" y="42" width="10" height="32" rx="5" />
          <rect x="60" y="48" width="10" height="26" rx="5" />
          <rect x="78" y="34" width="10" height="40" rx="5" />
          <rect x="96" y="40" width="10" height="34" rx="5" />
          <rect x="114" y="26" width="10" height="48" rx="5" />
        </g>
        <path className="rm-glyph-rule" d="M20 74h110" />
      </svg>
    ),
  },
]

/**
 * The dot-grid patches — a schema's shape with the labels too far to read.
 *
 * These are texture rather than objects, which is why they are the one thing in
 * the margins still placed by percentage: they belong to the corners of the
 * window, not to the line, and they are mirrored in pairs so neither side of
 * the page carries more of them than the other.
 */
type Mote = { x: number; y: number; depth: number; drift: number; cols: number; rows: number }

const MOTES: Mote[] = [
  { x: 7, y: 20, depth: 9, drift: 0, cols: 4, rows: 3 },
  { x: 91, y: 17, depth: 7, drift: 2, cols: 4, rows: 4 },
  { x: 9, y: 62, depth: 11, drift: 3, cols: 3, rows: 3 },
  { x: 90, y: 64, depth: 8, drift: 4, cols: 3, rows: 2 },
]

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
    let arcInk = 0.4, arcHaze = 0.075
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
      arcInk = Number(style.getPropertyValue('--arc-ink')) || 0.4
      arcHaze = Number(style.getPropertyValue('--arc-haze')) || 0.075
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

    /* The cards carry no position of their own until the first frame has
       measured the line, so they start invisible and are faded in once — a
       card painted at the origin for one frame is a card seen jumping. */
    let placed = false

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

      const px = ((atX / width) - 0.5) * -2
      const py = ((atY / height) - 0.5) * -2

      // ── where the line is ──
      if (++sinceMeasure >= 10) {
        sinceMeasure = 0
        plateBox = document.querySelector('.rm-auth-orbit')?.getBoundingClientRect() ?? null
      }
      const seated = plateBox && plateBox.width > 0
      const apexX = seated ? plateBox!.left + plateBox!.width / 2 : width / 2
      const apexY = seated
        ? plateBox!.top + plateBox!.height * (0.5 + ARC_SEAT)
        : height * 0.3
      const span = width * (0.5 + ARC_OVER)
      const drop = height * ARC_DROP

      // ── the cards on it ──
      for (let i = 0; i < NODES.length; i++) {
        const mark = marks[i]
        const n = NODES[i]
        if (!mark) continue
        /* Anchor first, then clamp into the window, then take the height from
           the clamped x — so a card pulled in from the edge slides *along* the
           curve instead of hanging off it in mid-air. */
        let x = apexX + n.side * (ARC_CLEAR + n.at * (span - ARC_CLEAR))
        x = Math.min(Math.max(x, ARC_PAD + n.w / 2), width - ARC_PAD - n.w / 2)
        const s = (x - apexX) / span
        const y = apexY + drop * s * s

        mark.style.setProperty('--tx', `${x - n.w / 2 + px * n.depth}px`)
        mark.style.setProperty('--ty', `${y - n.h / 2 + py * n.depth}px`)
        const d = Math.hypot(atX - x, atY - y)
        mark.style.setProperty('--lit', (grip * Math.max(0, 1 - d / 340) ** 1.6).toFixed(3))
      }
      if (!placed && seated) {
        placed = true
        for (let i = 0; i < NODES.length; i++) marks[i]?.style.setProperty('--ready', '1')
      }

      // ── the motes in the corners ──
      for (let i = 0; i < MOTES.length; i++) {
        const mark = marks[NODES.length + i]
        const m = MOTES[i]
        if (!mark) continue
        const cx = (m.x / 100) * width
        const cy = (m.y / 100) * height
        mark.style.setProperty('--tx', `${px * m.depth}px`)
        mark.style.setProperty('--ty', `${py * m.depth}px`)
        const d = Math.hypot(atX - cx, atY - cy)
        mark.style.setProperty('--lit', (grip * Math.max(0, 1 - d / 340) ** 1.6).toFixed(3))
      }

      ctx!.clearRect(0, 0, width, height)

      // ── the line ──
      /* Both ends of each half fade, and they fade for different reasons: the
         outer one so the line leaves the frame as a piece of something larger,
         the inner one so it never reaches the mark. Distances are in pixels
         from the apex and converted to gradient offsets — the gradient runs
         outer end (0) to apex (1), so a point `d` from the apex is at `1 −
         d/span`. Both are clamped to the span, which is what keeps the stops in
         order on a window too narrow to hold the full sequence. */
      const plate = seated ? plateBox!.width : 520
      const hush = Math.min(span * 0.6, plate * ARC_HUSH)
      const rise = Math.min(span * 0.9, plate * ARC_FULL)

      /* Each half is stroked twice from the same path: once wide and faint for
         the haze it sits in, once hairline for the line itself. */
      for (const dir of [-1, 1] as const) {
        const endX = apexX + dir * span
        const grad = ctx!.createLinearGradient(endX, 0, apexX, 0)
        const colour = colors[dir < 0 ? '--wave-a' : '--wave-b']
        grad.addColorStop(0, 'transparent')
        grad.addColorStop(Math.min(0.34, (1 - rise / span) * 0.7), colour)
        grad.addColorStop(1 - rise / span, colour)
        grad.addColorStop(1 - hush / span, 'transparent')
        grad.addColorStop(1, 'transparent')
        ctx!.strokeStyle = grad
        ctx!.beginPath()
        ctx!.moveTo(endX, apexY + drop)
        ctx!.quadraticCurveTo(apexX + dir * span * 0.5, apexY, apexX, apexY)
        ctx!.lineWidth = 9
        ctx!.globalAlpha = arcHaze
        ctx!.stroke()
        ctx!.lineWidth = 1.3
        ctx!.globalAlpha = arcInk
        ctx!.stroke()
      }

      /* What travels it. Evenly spaced and all moving at one rate, so with the
         clock stopped they hold a still, even scatter rather than a clump —
         and each one dims on the same two ramps the line does, or it would be
         a dot crossing a gap the line itself had the sense to leave. */
      for (let i = 0; i < FLOW; i++) {
        const u = (i / FLOW + t / FLOW_PERIOD) % 1
        const s = u * 2 - 1
        const e = Math.min(u, 1 - u) / 0.22
        const d = Math.abs(s) * span
        const k = d <= hush ? 0 : d >= rise ? 1 : (d - hush) / (rise - hush)
        const a = (e >= 1 ? 1 : e * e * (3 - 2 * e))
          * (k * k * (3 - 2 * k)) * FLOW_INK
        if (a <= 0.01) continue
        ctx!.globalAlpha = a
        ctx!.fillStyle = colors[s < 0 ? '--wave-a' : '--wave-b']
        ctx!.beginPath()
        ctx!.arc(apexX + s * span, apexY + drop * s * s, 2.1, 0, 6.2832)
        ctx!.fill()
      }
      ctx!.globalAlpha = 1

      // ── the waves ──
      while (rings.length && now - rings[0].born > RING_LIFE) rings.shift()

      for (const w of WAVES) {
        const fx0 = w.x0 * width, fx1 = w.x1 * width
        const wspan = fx1 - fx0
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
          const count = Math.max(2, Math.ceil(Math.abs(wspan) / step))
          const fade = (1 - d) ** 0.8
          const rad = 1.7 - 0.85 * d

          for (let i = 0; i <= count; i++) {
            const u = i / count
            const jx = jitter(L, i), jy = jitter(i, L)

            const th = u * 6.2832
            let x = fx0 + wspan * u + (jx - 0.5) * step * 0.6
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
      if (seated) {
        const scale = plateBox!.width / 900
        const cx = apexX
        const cy = plateBox!.top + plateBox!.height / 2
        const spin = (t / ORBIT_PERIOD) * Math.PI * 2
        for (const o of ORBIT) {
          const ang = (o.deg * Math.PI) / 180 + spin
          const x = cx + Math.cos(ang) * o.r * scale
          const y = cy - Math.sin(ang) * o.r * scale

          /* The same dissolve the rings get from their CSS mask, computed the
             same way, so a dot and the ring it rides fade out together as they
             pass behind the heading. */
          const down = (y - plateBox!.top) / plateBox!.height
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
      {/* Cards first, motes after: the frame loop walks this list by index, and
          `NODES.length` is where it changes what it is placing. */}
      <div ref={glyphsRef} className="rm-auth-glyphs" aria-hidden="true">
        {NODES.map((n, i) => (
          <div
            key={`n${i}`}
            className={`rm-auth-glyph rm-auth-node${n.inner ? ' is-inner' : ''}`}
            style={{ transitionDelay: `${120 + i * 90}ms` }}
          >
            {/* The parallax is on the wrapper and the idle drift on the child:
                one element cannot hold two transforms, and collapsing them into
                one animated value would make the pointer fight the keyframes. */}
            <div className="rm-auth-glyph-drift" style={{ animationDelay: `${n.drift * -1.7}s` }}>
              {n.art}
            </div>
          </div>
        ))}
        {MOTES.map((m, i) => (
          <div key={`m${i}`} className="rm-auth-glyph" style={{ left: `${m.x}%`, top: `${m.y}%` }}>
            <div className="rm-auth-glyph-drift" style={{ animationDelay: `${m.drift * -1.7}s` }}>
              <Matrix cols={m.cols} rows={m.rows} />
            </div>
          </div>
        ))}
      </div>
    </>
  )
}
