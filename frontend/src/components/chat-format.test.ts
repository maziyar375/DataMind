/**
 * The reader, exercised against what models actually write.
 *
 * `npm run test:chat` — Node runs this directly (type stripping, no bundler,
 * no test framework, no new dependency), the same bargain the other suites
 * make. Two failure modes are being checked: text that disappears, and text
 * that turns into something the model did not write. Both are worse than the
 * literal asterisks this replaces, so every case below asserts the *whole*
 * span list rather than just the interesting span.
 */
import { formatAnswer, formatLine } from './chat-format.ts'
import type { Span } from './chat-format.ts'

let failures = 0
function check(name: string, actual: unknown, expected: unknown): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures += 1
  console.log(
    ok
      ? `ok    ${name}`
      : `FAIL  ${name}\n        got ${JSON.stringify(actual)}\n        want ${JSON.stringify(expected)}`,
  )
}

const text = (t: string): Span => ({ kind: 'text', text: t })
const strong = (t: string): Span => ({ kind: 'strong', text: t })
const code = (t: string): Span => ({ kind: 'code', text: t })

/** Nothing may be lost: the spans concatenated back are the line, or explain why. */
function flatten(spans: Span[]): string {
  return spans.map((s) => s.text).join('')
}

// ── the case that started this ─────────────────────────────────────────────
check(
  'the reported line bolds its group name',
  formatLine('**Products & Inventory** has 8 tables.'),
  [strong('Products & Inventory'), text(' has 8 tables.')],
)

check(
  'plain prose is one span and is not touched',
  formatLine('Revenue was 4,182,900 in June, up 12% on May.'),
  [text('Revenue was 4,182,900 in June, up 12% on May.')],
)

check('an empty line has no spans', formatLine(''), [])

// ── emphasis ──────────────────────────────────────────────────────────────
check('two bolds in one line', formatLine('**a** and **b**'), [
  strong('a'), text(' and '), strong('b'),
])

check(
  'bold mid-sentence keeps both sides',
  formatLine('The **orders** table is the grain.'),
  [text('The '), strong('orders'), text(' table is the grain.')],
)

check(
  'a single asterisk is arithmetic, not emphasis',
  formatLine('unit_price * quantity = line total'),
  [text('unit_price * quantity = line total')],
)

check(
  'a space after the opening marker is not emphasis',
  formatLine('** not bold **'),
  [text('** not bold **')],
)

check('empty markers stay literal', formatLine('****'), [text('****')])

check(
  'a rejected span does not eat a later real one',
  formatLine('** a ** and **b**'),
  [text('** a ** and '), strong('b')],
)

// ── streaming: half-written markup must stay visible ───────────────────────
const arriving = ['', '**', '**Pro', '**Products', '**Products**', '**Products** has 8']
check(
  'every intermediate state of a streaming answer renders its own text',
  arriving.map((t) => flatten(formatLine(t))),
  ['', '**', '**Pro', '**Products', 'Products', 'Products has 8'],
)
check(
  'an unclosed marker is text, not a dropped span',
  formatLine('**Products'),
  [text('**Products')],
)

// ── code ──────────────────────────────────────────────────────────────────
check(
  'a backticked identifier becomes code',
  formatLine('Join `orders` to `order_items`.'),
  [text('Join '), code('orders'), text(' to '), code('order_items'), text('.')],
)

check(
  'code wins over emphasis inside it',
  formatLine('`**literal**`'),
  [code('**literal**')],
)

check('an unclosed backtick stays literal', formatLine('use `orders'), [
  text('use `orders'),
])

// ── bullets ───────────────────────────────────────────────────────────────
check('a dash marker becomes a bullet', formatAnswer('- orders'), [
  [text('• orders')],
])

check('a star marker becomes a bullet', formatAnswer('* orders'), [
  [text('• orders')],
])

check('indentation before a marker survives', formatAnswer('  - orders'), [
  [text('  • orders')],
])

check(
  'a bold line is not mistaken for a bullet',
  formatAnswer('**Products & Inventory**'),
  [[strong('Products & Inventory')]],
)

check(
  'a bulleted line still reads its emphasis',
  formatAnswer('- **orders** — one row per order'),
  [[text('• '), strong('orders'), text(' — one row per order')]],
)

check(
  'a hyphenated word at the head of a line is not a bullet',
  formatAnswer('year-to-date revenue is up'),
  [[text('year-to-date revenue is up')]],
)

// ── lines ─────────────────────────────────────────────────────────────────
check(
  'the answer is split into lines and blank lines survive',
  formatAnswer('**Sales**\n\n- orders\n- invoices'),
  [[strong('Sales')], [], [text('• orders')], [text('• invoices')]],
)

// ── Persian: the same rules, and nothing reordered ─────────────────────────
check(
  'a Persian answer bolds the same way',
  formatLine('**سفارش‌ها** ۸ جدول دارد.'),
  [strong('سفارش‌ها'), text(' ۸ جدول دارد.')],
)

check(
  'a zero-width non-joiner inside emphasis is kept byte for byte',
  formatLine('**سفارش‌ها**')[0]?.text,
  'سفارش‌ها',
)

// ── nothing is ever lost ──────────────────────────────────────────────────
const corpus = [
  '',
  'plain',
  '**a**',
  '**a',
  'a**',
  '****',
  '** **',
  '`x`',
  '`x',
  '*',
  '**`x`**',
  '***a***',
  'a * b ** c',
  '- item',
  '  * item',
  '-item',
  '**a** `b` **c**',
]
check(
  'every input is recoverable from its spans, modulo the markers it used',
  corpus.map((line) => flatten(formatLine(line)).replace(/\*\*|`/g, '')),
  corpus.map((line) => line.replace(/\*\*|`/g, '')),
)

check(
  'no span is ever empty, so nothing renders an empty element',
  corpus.flatMap((line) => formatLine(line)).filter((s) => s.text.length === 0),
  [],
)

// ── a model writing what it was told not to ───────────────────────────────
check(
  'a heading is left exactly as written, not silently swallowed',
  formatLine('## Products'),
  [text('## Products')],
)

check(
  'a link is left exactly as written',
  formatLine('see [the docs](http://x)'),
  [text('see [the docs](http://x)')],
)

check(
  'an html tag is text and stays text',
  formatLine('<script>alert(1)</script>'),
  [text('<script>alert(1)</script>')],
)

console.log(failures === 0 ? '\nall good' : `\n${failures} failing`)
if (failures > 0) throw new Error(`${failures} chat-format checks failed`)
