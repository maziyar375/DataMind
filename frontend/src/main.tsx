import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import App from './App'
import './styles.css'

/**
 * One catch-all route rendering the shell, and the sections' own routes inside
 * it (`App.tsx`).
 *
 * A data router — `createBrowserRouter` rather than `<BrowserRouter>` — for one
 * concrete reason: `useBlocker` exists only on this one, and the unsaved-work
 * guard is the difference between "the app knows your edits are unsaved" and
 * "the app acts on it". Nothing else here uses loaders or actions; the shell
 * still owns the auth gate, and pages still fetch their own data.
 */
const router = createBrowserRouter([{ path: '*', element: <App /> }])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
