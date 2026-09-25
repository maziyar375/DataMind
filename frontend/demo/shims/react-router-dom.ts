/**
 * `react-router-dom`, with hash routing in place of browser routing.
 *
 * GitHub Pages serves a project site from `/DataMind/` and has no SPA
 * fallback: a refresh on `/DataMind/chat/…` asks the server for a file that
 * does not exist and gets a 404. With the route after `#`, the server only
 * ever serves `index.html`, and every deep link survives a reload.
 *
 * `src/main.tsx` builds a data router with `createBrowserRouter` — it needs
 * one for `useBlocker` — so that is the name replaced here, with the hash
 * router that has the same API. `BrowserRouter` is aliased too, although the
 * app does not use it, so the swap stays true if it ever does. Everything
 * else is the real package: an explicit export shadows the star export of the
 * same name.
 */
export * from 'react-router-dom'
export { HashRouter as BrowserRouter, createHashRouter as createBrowserRouter } from 'react-router-dom'
