/**
 * The little bit of markdown a language model writes whether or not it was
 * asked to — read at display time, as pure functions.
 *
 * `ANSWER_SYSTEM` and `DESCRIBE_SYSTEM` both say "no markdown headings", and
 * both are obeyed. Neither says anything about emphasis, so `**Products &
 * Inventory**` arrives with its asterisks intact and the transcript showed
 * them literally — the answer is rendered as a text node under `pre-wrap`,
 * and nothing in this frontend has ever parsed markdown.
 *
 * This is the smallest thing that fixes that: three constructs, no
 * dependency, and **no HTML** — the caller receives spans and builds
 * elements, so a model can never write markup into the page. That is the
 * whole reason this is not `react-markdown`.
 *
 * What it deliberately does not read: headings, links, images, tables, block
 * quotes, raw HTML. The first is already forbidden by the prompt; the rest do
 * not appear in answers written from a query result, and each one is a
 * decision about what a model is allowed to put on the page. If one starts
 * showing up, add it here with a test, not by reaching for a parser.
 *
 * **Unclosed markup stays literal**, which is not a nicety — an answer
 * streams in, so `**Produ` is a real intermediate state on the way to
 * `**Products**`, and text that vanished and reappeared as the tokens landed
 * would read as a glitch. An unmatched marker is just text until it closes.
 */

/** A run of answer text and how it should be drawn. Never markup. */
export type Span =
  | { kind: 'text'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'code'; text: string }

/**
 * Code first, so `` `**not bold**` `` stays what it says it is; then `**`.
 * A single `*` is left alone on purpose — `2 * 3` is arithmetic, and a model
 * that means emphasis writes it doubled.
 */
const TOKEN = /`([^`\n]+)`|\*\*([^\n]+?)\*\*/g

/**
 * `- item`, `* item`, `+ item` — the marker only, and only at the head of a
 * line. `**Products**` opening a line is not a bullet: the `*` is followed by
 * another `*`, not by a space, which is exactly the case a naive `^\s*\*`
 * would eat.
 */
const BULLET = /^([ \t]*)[-*+][ \t]+/

/** `**bold**` needs something between the markers that is not a space. */
function isEmphasis(inner: string): boolean {
  return inner.trim().length > 0 && inner === inner.trim()
}

function pushText(spans: Span[], text: string): void {
  if (text.length === 0) return
  const last = spans[spans.length - 1]
  // Rejected markup is emitted as text next to text — merged, so a caller
  // rendering one element per span does not fragment a sentence into a dozen.
  if (last && last.kind === 'text') last.text += text
  else spans.push({ kind: 'text', text })
}

/** One line to its spans, in order. The line never contains a newline. */
export function formatLine(line: string): Span[] {
  const spans: Span[] = []
  let at = 0
  TOKEN.lastIndex = 0
  for (let m = TOKEN.exec(line); m !== null; m = TOKEN.exec(line)) {
    const [whole, code, strong] = m
    if (code !== undefined) {
      pushText(spans, line.slice(at, m.index))
      spans.push({ kind: 'code', text: code })
    } else if (strong !== undefined && isEmphasis(strong)) {
      pushText(spans, line.slice(at, m.index))
      spans.push({ kind: 'strong', text: strong })
    } else {
      // Not emphasis after all. Leave it where it was and keep scanning past
      // it, so `** a ** and **b**` still bolds the second one.
      pushText(spans, line.slice(at, m.index + whole.length))
    }
    at = m.index + whole.length
  }
  pushText(spans, line.slice(at))
  return spans
}

/**
 * The answer as lines of spans. The caller joins them with newlines — the
 * container is still `pre-wrap`, so blank lines and runs of spaces survive
 * exactly as they did before this module existed.
 *
 * A list marker becomes a real bullet character. That is as far as this goes:
 * a hanging indent would mean laying lines out as blocks, and a block layout
 * that reflows while an answer streams is a worse bug than the one being
 * fixed here.
 */
export function formatAnswer(text: string): Span[][] {
  return text.split('\n').map((line) => {
    const bullet = BULLET.exec(line)
    if (bullet === null) return formatLine(line)
    const spans = formatLine(line.slice(bullet[0].length))
    pushFront(spans, `${bullet[1]}• `)
    return spans
  })
}

function pushFront(spans: Span[], text: string): void {
  const first = spans[0]
  if (first && first.kind === 'text') first.text = text + first.text
  else spans.unshift({ kind: 'text', text })
}
