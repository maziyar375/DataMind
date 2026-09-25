/**
 * The demo build: the real app, answering from fixtures, for a static host.
 *
 *   npx vite build --config vite.demo.config.ts     → demo-dist/
 *   npx vite preview --config vite.demo.config.ts   → serve it
 *
 * It is `vite.config.ts` with four things on top — a base path for a GitHub
 * Pages project site, its own output directory so it never touches `dist/`,
 * `__DEMO__`, and the two plugins in `demo/plugins/`. Nothing under `src/`
 * knows it exists; see `demo/README.md`.
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, mergeConfig } from 'vite'
import baseConfig from './vite.config'
import { demoBanner } from './demo/plugins/demo-banner'
import { demoSubstitute } from './demo/plugins/demo-substitute'
import { DEMO_TODAY } from './demo/mock/fixtures/today'

const root = path.dirname(fileURLToPath(import.meta.url))

export default mergeConfig(
  baseConfig,
  defineConfig({
    base: process.env.DEMO_BASE ?? '/DataMind/',
    build: { outDir: 'demo-dist', emptyOutDir: true },
    define: { __DEMO__: true },
    plugins: [
      demoSubstitute(root),
      demoBanner({
        frontendRoot: root,
        sourceUrl: process.env.DEMO_SOURCE_URL ?? 'https://github.com/maziyar375/DataMind',
        asOf: DEMO_TODAY,
      }),
    ],
  }),
)
