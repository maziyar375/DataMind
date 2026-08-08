/**
 * The little that Node lends the DOM-free tests.
 *
 * `dashboard-schedule.test.ts`, `table-format.test.ts`, `palette.test.ts`,
 * `report-document.test.ts` and `report-print.test.ts` are run by Node
 * directly (`node --experimental-strip-types`) and never bundled into the SPA.
 * They keep to browser globals for exactly one reason — `@types/node` is not a
 * dependency of this project and adding it would put `process`, `require` and
 * `Buffer` in scope for the whole application, where none of them exist.
 *
 * `report-print.test.ts` is the one that cannot: it asserts that the page
 * width `styles.css` gives a printed report is still the width the charts are
 * redrawn against, and reading a stylesheet needs a filesystem. So the two
 * functions it uses are declared here, and only those two — this file is a
 * doorway, not a door left open.
 */
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string
}

declare module 'node:url' {
  export function fileURLToPath(url: URL | string): string
}
