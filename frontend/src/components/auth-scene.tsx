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
 * One dotted sheet, in viewport fractions so it reflows with the window.
 *
 * `a` is the corner it grows from (pushed slightly off-screen so the sheet is
 * cut by the edge instead of ending in mid-air), `u` runs along a strand and
 * `v` steps back across them. The wave displaces along `v`, which is why the
 * two vectors are stored rather than one angle.
 */
type Field = {
  ax: number; ay: number
  ux: number; uy: number
  vx: number; vy: number
  color: '--wave-a' | '--wave-b'
  /** Flips the travel direction so the two corners do not read as one sheet. */
  flow: 1 | -1
}

/*
 * Measured off the reference, not guessed: each sheet covers about a third of
 * the width and a fifth of the height, tucked into its corner. The numbers
 * matter more than they look — the vertical span is `|uy| + |vy| + amplitude`,
 * and an earlier pass at 0.22 + 0.36 + 0.11 put the sheets across two thirds of
 * the screen, which is not a bigger version of this, it is a different picture.
 */
const FIELDS: Field[] = [
  { ax: -0.09, ay: 1.03, ux: 0.37, uy: -0.135, vx: 0.06, vy: -0.13, color: '--wave-a', flow: 1 },
  { ax: 1.09, ay: 1.03, ux: -0.37, uy: -0.135, vx: -0.06, vy: -0.13, color: '--wave-b', flow: -1 },
]

/*
 * What makes the sheet read as a plane lying in space rather than a pattern
 * printed on the glass. Three effects, all of them one line each:
 *
 *  - `PERSPECTIVE` bunches the strands as they recede, so the near ones are
 *    far apart and the far ones crowd toward a horizon.
 *  - `TAPER` narrows each strand about its own midline with depth, which is
 *    the pair of converging edges the eye actually reads as distance.
 *  - the swell shrinks with depth too, because a wave further away is smaller.
 *
 * Together they turn a flat dotted band into a corner of a moving surface.
 */
const PERSPECTIVE = 1.7
const TAPER = 0.3

/* Dot spacing in CSS pixels, along a strand and between strands. The counts are
   derived from these and the sheet's measured length rather than picked per
   breakpoint: a fixed count spreads to 16px apart on a wide window and packs to
   9px on a phone, which is one sheet that looks like two different materials. */
const ALONG_STEP = 7.5
const STRAND_STEP = 12
/** How far the pointer is felt, and how hard it shoves a dot aside. */
const REACH = 150
const SHOVE = 20
/** A click's ring: how fast it travels, how wide it is, how long it lives. */
const RING_SPEED = 620
const RING_BAND = 78
const RING_LIFE = 1.5

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))

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
       as stutter rather than as slowness. Sized to the clamps in `dotsAcross`'s
       constants, so no frame can outgrow it. */
    const dots = new Float32Array(132 * 16 * 4)
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

      for (const f of FIELDS) {
        const ax = f.ax * width, ay = f.ay * height
        const ux = f.ux * width, uy = f.uy * height
        const vx = f.vx * width, vy = f.vy * height
        const vlen = Math.hypot(vx, vy) || 1
        const nvx = vx / vlen, nvy = vy / vlen
        const strands = clamp(Math.round(vlen / STRAND_STEP), 8, 22)
        const across = clamp(Math.round(Math.hypot(ux, uy) / ALONG_STEP), 24, 132)
        /* Off the sheet's own depth rather than the viewport's, so the ripple
           stays inside the corner it belongs to at every size. */
        const amp = vlen * 0.2

        /* Pass one solves every dot into `dots` and files it under an opacity
           bucket. Nothing is painted here. */
        let n = 0
        counts.fill(0)
        for (let s = 0; s < strands; s++) {
          /* Even in depth, uneven on screen: that difference is the whole of
             the perspective. */
          const vRaw = s / (strands - 1)
          const v = (vRaw * (1 + PERSPECTIVE)) / (1 + PERSPECTIVE * vRaw)
          const taper = 1 - TAPER * v
          for (let i = 0; i < across; i++) {
            /* Narrowed about the strand's own midline, so both edges converge
               instead of the sheet sliding sideways as it recedes. */
            const u = 0.5 + (i / (across - 1) - 0.5) * taper

            /* Two harmonics, the second off-tempo, so the sheet never settles
               into the single clean sine that reads as a screensaver. */
            const phase = u * 10.5 + v * 1.25 - t * 0.85 * f.flow
            const swell = amp * (0.4 + 0.6 * Math.sin(Math.PI * v * 0.82 + 0.22)) * (1 - 0.4 * v)
            const d = Math.sin(phase) * swell + Math.sin(phase * 1.93 + 1.3) * swell * 0.3

            let x = ax + ux * u + vx * v + nvx * d
            let y = ay + uy * u + vy * v + nvy * d
            /* Both fades are one-sided on purpose. The sheet's outer edge is
               anchored off-screen, so it is cut by the window like a real thing
               continuing past it; fading that edge too would draw the taper's
               own straight diagonal, which reads as a torn sheet rather than a
               surface. What does fade is the inner edge, toward the middle of
               the page, and the far edge into its own horizon. */
            const edge = 1 - u ** 2.6
            const depth = (1 - v) ** 0.85
            let a = 0.95 * (edge > 0 ? edge : 0) * depth
            let r = 0.7 + 1.55 * (1 - v * 0.72)

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
              const edge = Math.abs(Math.hypot(x - ring.x, y - ring.y) - age * RING_SPEED)
              if (edge < RING_BAND) {
                const k = (1 - edge / RING_BAND) * (1 - age / RING_LIFE)
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
          }
        }

        /* Pass two paints each bucket as ONE path. `globalAlpha` and `fill` are
           the expensive calls, and a fill per dot costs about half the frame at
           this density — twelve of them costs nothing, and twelve opacity steps
           are indistinguishable on a two-pixel dot. */
        ctx!.fillStyle = colors[f.color]
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
