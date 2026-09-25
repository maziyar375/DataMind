/**
 * Every delay in the demo, in one place.
 *
 * A step's playback delay **is** its recorded `duration_ms`, and the numbers
 * inside its detail ("Classified ANALYTICAL in 640ms") are written from the
 * same value, so the trail a visitor watches and the trail the finished turn
 * shows cannot disagree. Tune here; nothing else hardcodes a duration.
 *
 * The defaults put a first-try analytical answer at about six and a half
 * seconds, the repair loop at about eight, and a schema question at about
 * four — the brief's four-to-eight, and the order of what the product
 * measures, compressed.
 */
export const STEP_MS = {
  /** One short classification call. */
  route: 640,
  /** A trigram lookup against the knowledge store that finds nothing close enough. */
  match: 36,
  /** A lookup that hits: the candidate's slots bound from the question, then re-guarded. */
  matchHit: 64,
  /** No sections on either connection: skipped, and hidden from the trail. */
  scope: 2,
  /** Rendering the snapshot; both schemas fit the budget whole. */
  retrieve: 48,
  /** A schema question, answered in prose while this runs. */
  describe: 3250,
  /** A structured "is this answerable as asked?" call. */
  clarify: 1240,
  /** The first SQL draft. */
  generate: 1680,
  /** A redraft after the guard refused the first one. */
  regenerate: 1420,
  /** The guard is SQLGlot on the SQL text: milliseconds. */
  validate: 28,
  /** Node overhead on top of the database's own duration (EXPLAIN included). */
  executeOverhead: 70,
  inspect: 9,
  /** The answer streams while this runs. */
  present: 1650,
  /** The intent was asked for during `present`, so this is mostly compiling. */
  chart: 380,
  /** A step that decides nothing and says so. */
  skipped: 1,
} as const

/** How the prose arrives: a chunk of this many characters every `TEXT_TICK_MS`. */
export const TEXT_CHUNK_CHARS = 9
/** Floor on the gap between chunks, so short answers still read as typing. */
export const TEXT_TICK_MS = 28

/** From `send` to the first event — the run being claimed and started. */
export const RUN_START_MS = 180

/** The follow-up chips. The real call is a full model completion; this is a pause. */
export const SUGGESTIONS_MS = 900

/** The honest fallback streams too, a little faster than an answer. */
export const FALLBACK_TICK_MS = 16

/** Any other request: long enough to show a spinner, short enough not to wait on. */
export const REQUEST_MS = 120

/**
 * A deep analysis. The chat nodes inside each step (`scope` … `inspect`) keep
 * their `STEP_MS` durations; these are the four deep nodes around them.
 *
 * The product says "takes a few minutes" and means it. The demo compresses a
 * five-step plan to about twenty-five seconds — long enough to watch the plan
 * fill in and to press *Answer now* part-way, short enough to sit through.
 */
export const DEEP_MS = {
  /** One structured call: the restatement, the steps, the stop condition. */
  plan: 3400,
  /** `step` on a step with dependencies: the reviser reads what they found. */
  revise: 1350,
  /** `step` on a step that depends on nothing: no call, just the cursor. */
  step: 4,
  /** `app.analysis` over the rows, and the evidence filed. */
  compute: 16,
  /** The answer streams while this runs. */
  synthesize: 5200,
} as const
