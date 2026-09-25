/**
 * The seam: three imports, redirected to demo modules at resolve time.
 *
 * | import                | becomes                         | why                               |
 * |-----------------------|---------------------------------|-----------------------------------|
 * | `src/api/client.ts`   | `demo/mock/client.ts`           | the one module that touches the network |
 * | `react-router-dom`    | `demo/shims/react-router-dom.ts`| hash routing for a host with no SPA fallback |
 * | `src/pages/LoginPage` | `demo/shims/LoginPage.tsx`      | the demo credentials, pre-filled  |
 *
 * **Why the typed client and not `fetch`.** Every request the SPA makes goes
 * through `src/api/client.ts` — nothing else in `src/` calls `fetch`,
 * `EventSource` or `XMLHttpRequest` — so replacing that one module catches
 * every call, and the replacement is type-checked against it
 * (`demo/mock/surface.ts`). A `fetch` interceptor would have to re-parse URLs
 * the client already knows how to build, and would lose the types.
 *
 * Imports are compared by **resolved path**, not by the string written: the
 * app spells the client `./api/client` in one file and `../api/client` in
 * thirty, and an alias or a new relative spelling tomorrow must not slip past.
 * Anything imported *from* `demo/` resolves normally, which is how the demo
 * modules reach the real client's types and the real router without looping
 * back into themselves.
 */
import path from 'node:path'
import type { Plugin } from 'vite'

export function demoSubstitute(frontendRoot: string): Plugin {
  const demoDir = path.join(frontendRoot, 'demo') + path.sep
  const src = path.join(frontendRoot, 'src')
  const swaps = new Map<string, string>([
    [path.join(src, 'api', 'client.ts'), path.join(demoDir, 'mock', 'client.ts')],
    [path.join(src, 'pages', 'LoginPage.tsx'), path.join(demoDir, 'shims', 'LoginPage.tsx')],
  ])
  const routerShim = path.join(demoDir, 'shims', 'react-router-dom.ts')

  const fromDemo = (importer: string | undefined) =>
    Boolean(importer && path.normalize(importer.split('?')[0]).startsWith(demoDir))

  return {
    name: 'datamind-demo-substitute',
    enforce: 'pre',
    async resolveId(source, importer, options) {
      if (!importer || fromDemo(importer)) return null // break the cycle
      // Only the app's own imports are redirected; a dependency that imports
      // one of these by name keeps the real thing.
      if (!path.normalize(importer.split('?')[0]).startsWith(src + path.sep)) return null
      if (source === 'react-router-dom') return routerShim
      // Only relative or aliased imports can name a file in src/.
      if (!source.startsWith('.') && !source.startsWith('/') && !source.startsWith('@/')) return null
      const resolved = await this.resolve(source, importer, { ...options, skipSelf: true })
      if (!resolved) return null
      return swaps.get(path.normalize(resolved.id.split('?')[0])) ?? null
    },
  }
}
