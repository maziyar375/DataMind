"""Reports: the pure half of the feature.

Self-contained on the same terms as `app.semantic` and `app.sqlguard`, and
enforced by an import-linter contract rather than by good intentions: no
fastapi, no sqlalchemy, no litellm, no `app.infra`, no `app.api`, no
`app.services`. That is what lets the outline validator, the narration prompt
builder and the numeric check each run in a test against a dict and a fake
gateway, with no database and no HTTP client anywhere near them.

It sits *below* the pipeline for the same reason `app.semantic` does: a report
reads a pipeline node, and a node knows nothing about a report.

Arriving here in later phases — `prompts.py` (§10), `outline.py` (§3),
`narrate.py` and `checks.py` (§6). The stateful half lives elsewhere by design:
`services/report_service.py` owns the transaction boundary,
`workers/report.py` runs the generation, and `api/v1/reports.py` is HTTP shape
only.
"""
