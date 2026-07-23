import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two settings here exist for remote and proxied environments — Lightning.ai,
 * Codespaces, Gitpod, or any VM reached through a tunnel.
 *
 * 1. `allowedHosts`. Vite 5.4.12+ rejects any request whose Host header it
 *    does not recognise (a DNS-rebinding protection). Every proxied dev
 *    domain trips it, producing "Blocked request. This host is not allowed."
 *
 * 2. The API proxy target. The browser is not on the same machine as the API,
 *    so a baked-in "http://localhost:8000" resolves to the *user's own*
 *    laptop and every request fails. Instead the SPA calls a same-origin
 *    "/api" path, and Vite forwards it server-side, where the compose network
 *    name "api:8000" is reachable.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,

    // Convenience for the dev server only. Do not serve this config publicly.
    allowedHosts: true,

    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
        // SSE must not be buffered, or every run step arrives at once when
        // the stream closes instead of live.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            const contentType = proxyRes.headers['content-type']
            if (contentType && contentType.includes('text/event-stream')) {
              proxyRes.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
    },

    // Bind-mounted source on some hosts does not emit inotify events.
    watch: { usePolling: process.env.VITE_POLL === '1' },
  },
})
