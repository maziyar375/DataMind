/**
 * **No component decides what to show by reading a role.**
 *
 * `npm run test:permissions` — Node runs this file directly, and it reads the
 * tree rather than importing it: the claim is about every `.tsx` in `src/`,
 * and a test that imported them would need React, a DOM and a bundler to
 * assert something a parse can see.
 *
 * It lives in `scripts/` rather than beside the fourteen suites in
 * `src/components/` for one mechanical reason: those are pure logic and are
 * typechecked with the app, while this one reads the filesystem, and `node:fs`
 * needs `@types/node` — a devDependency this project does not have and should
 * not acquire to hold one test. Outside `src` it is outside `tsconfig`'s
 * include, and `node --experimental-strip-types` runs it either way.
 *
 * The rule it holds up is invariant I1 from
 * [`docs/access-control-rules.md`](../../docs/access-control-rules.md), on
 * this side of the wire. The SPA asks *"may I?"* through `useCan()`, which
 * reads the capability list `/auth/me` returned — the same set the API will
 * check on the next request — and never asks *"what am I?"*. That is what
 * makes *"the UI shows exactly what the backend would allow"* a property
 * rather than an aspiration.
 *
 * As of Phase 10 there is nothing left to read even if somebody tried:
 * `users.role` was dropped in migration `0029` and `User.role` is gone from
 * `api/types.ts`. So this test is a **tripwire for its return** — somebody
 * adding a two-value role back as "just a display field" gets a failure here
 * naming the alternative, which is `roles` (names, to show) and
 * `capabilities` (verbs, to decide).
 *
 * Comments are stripped before the search, deliberately: three files carry a
 * sentence explaining that the role is gone, and a test that punished those
 * would be a test that deletes its own explanation.
 */
// @ts-nocheck — this file is outside `tsconfig`'s `include`; see the header.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

let failures = 0
function check(name: string, ok: boolean, detail = ''): void {
  if (!ok) failures += 1
  console.log(ok ? `ok    ${name}` : `FAIL  ${name}${detail ? `\n        ${detail}` : ''}`)
}

/** Every source file under `src/`, excluding this one. */
function sources(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) {
      out.push(...sources(path))
    } else if (/\.tsx?$/.test(entry)) {
      out.push(path)
    }
  }
  return out
}

/**
 * The file with its comments removed.
 *
 * Block comments, line comments and JSX comments — `{/* … *\/}` — in that
 * order, because a JSX comment contains a block comment and stripping the
 * inner one first would leave the braces behind. Strings are not protected:
 * a `//` inside a string literal would be over-stripped, which costs a false
 * pass on a line nobody writes and buys a parser this file does not need.
 */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const ROOT = join(new URL('.', import.meta.url).pathname, '..', 'src') + '/'
const files = sources(ROOT)

check('the walk finds the tree', files.length > 30, `found ${files.length} files`)

// ── the rule ─────────────────────────────────────────────────────────────
const offenders: string[] = []
for (const path of files) {
  const source = code(readFileSync(path, 'utf8'))
  // `user.role`, `person.role`, `editing.role` — any property access on a
  // name that is not one of the words this product legitimately spells
  // `role`: a chat message's author, a semantic entity's kind, a knowledge
  // template's role.
  const matches = source.match(/\b(\w+)\.role\b/g) ?? []
  for (const hit of matches) {
    const holder = hit.slice(0, -'.role'.length)
    if (['message', 'm', 'entity', 'column', 'draft', 't', 'row', 'block'].includes(holder)) {
      continue
    }
    offenders.push(`${path.replace(ROOT, '')}: ${hit}`)
  }
}
check(
  'no component reads a principal’s role',
  offenders.length === 0,
  offenders.join('\n        '),
)

// ── and the thing it would be read *for* is answered another way ─────────
const permissions = readFileSync(join(ROOT, 'permissions.tsx'), 'utf8')
check('useCan reads capabilities', permissions.includes('capabilities'))
check('and never a role string', !code(permissions).includes("=== 'ADMIN'"))

const types = readFileSync(join(ROOT, 'api', 'types.ts'), 'utf8')
check(
  'the User type carries no role field',
  !/^\s*role\??:\s*'ADMIN'/m.test(types),
)
check('but does carry the role names a badge is drawn from', types.includes('roles?: string[]'))

console.log(
  failures === 0
    ? '\nall permission checks passed'
    : `\n${failures} permission check(s) failed`,
)
process.exit(failures === 0 ? 0 : 1)
