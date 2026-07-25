"""Offline evaluation: golden suites and (from Task 3) the eval runner.

This package is **not on the request path**. Nothing under `app.api`,
`app.services`, `app.pipeline`, or `app.domain` may import it — enforced by the
`eval is offline-only` import-linter contract. It exists so accuracy can be
measured against the fixtures without the web app ever depending on eval code.
"""
