"""A semantic layer as a portable file.

Phase 4 of `docs/plans/semantic-layer-model.md`. What leaves, what may come back,
and where it lands:

* an export carries **no ids, hosts or credentials**, **nothing derived**
  (`joins`, `valid`, `issue`), and **no value meanings unless asked** — those are
  values from the data (D9);
* an export → import round trip is an empty change list, apart from the value
  meanings a plain export leaves out;
* an import lands **in the draft**, bound to this connection's snapshot: a table
  this schema lacks comes in flagged, not dropped;
* a hostile file is refused with a sentence — wrong format, a newer version,
  too many tables, a text past its limit — and the same limits hold the editor;
* nothing in a layer is executed, so import is not a guard entry point; the
  hostile SQL corpus is replayed through `check_expression` anyway.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import typing
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from app.api.schemas import SemanticImportRequest
from app.api.v1 import semantic as semantic_routes
from app.core.errors import (
    NotFoundError,
    SemanticConflictError,
    SemanticNoChangesError,
    ValidationError,
)
from app.domain.value_objects.authz import Privilege
from app.infra.authz.owner_only import OwnerOnlyAuthorizer
from app.semantic import (
    SemanticColumn,
    SemanticDocument,
    SemanticEntity,
    SemanticMetric,
    build_index,
    check_expression,
    diff_documents,
    limits,
)
from app.semantic import diff as d
from app.semantic.models import GlossaryTerm, SemanticJoin, TimeSemantics
from app.services import semantic_service
from app.services.semantic_service import SEMANTIC_EXPORTED, SEMANTIC_IMPORTED
from app.services.semantic_transfer import FILE_FORMAT, portable, read_file
from tests.unit.semantic_world import (
    OTHER,
    audit_rows,
    connection,
    ctx,
    customers,
    db,  # noqa: F401 - fixture
    engine,  # noqa: F401 - fixture
    head,
    layer,
    llm_config,
    orders,
    service,
    sync,
    versions,
)
from tests.unit.test_semantic_concurrency import BlockingGeneration, _job, _wire
from tests.unit.test_sqlguard_hostile import HOSTILE


def _with_meanings() -> SemanticDocument:
    doc = layer()
    doc.entities[0].columns.append(SemanticColumn(
        name="status", label="Order status", value_meanings={"C": "cancelled", "P": "paid"},
    ))
    return doc


async def _published(db, conn, doc: SemanticDocument | None = None) -> None:  # noqa: F811
    await service(db).save(conn, doc or _with_meanings(), base_revision=0, ctx=ctx())


def _file(doc: SemanticDocument, *, value_meanings: bool = False) -> dict[str, Any]:
    return {
        "format": FILE_FORMAT, "format_version": 1,
        "document": portable(doc, value_meanings=value_meanings),
    }


def _walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _walk_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _walk_keys(v)}
    return set()


# ── export ───────────────────────────────────────────────────────────────
async def test_an_export_carries_nothing_derived_and_nothing_from_the_connection(
    db,  # noqa: F811
) -> None:
    conn = connection(db)
    await _published(db, conn)
    file = await service(db).export(conn, version=None, value_meanings=False, ctx=ctx())
    body = file.model_dump(mode="json")

    assert body["format"] == FILE_FORMAT and body["format_version"] == 1
    assert body["source"] == {"connection": conn.name, "engine": "postgres", "version": 1}
    assert body["value_meanings_included"] is False
    assert not _walk_keys(body["document"]) & {"joins", "valid", "issue", "value_meanings"}
    text = json.dumps(body)
    for secret in (str(conn.id), conn.host, conn.username, conn.encrypted_password):
        assert f'"{secret}"' not in text, secret
    assert [r.detail for r in audit_rows(db, SEMANTIC_EXPORTED)] == [
        {"version": 1, "value_meanings_included": False}
    ]


async def test_value_meanings_leave_only_when_asked(db) -> None:  # noqa: F811
    conn = connection(db)
    await _published(db, conn)
    file = await service(db).export(conn, version=None, value_meanings=True, ctx=ctx())
    [status] = [c for c in file.document["entities"][0]["columns"] if c["name"] == "status"]
    assert status["value_meanings"] == {"C": "cancelled", "P": "paid"}
    assert file.value_meanings_included is True
    assert audit_rows(db, SEMANTIC_EXPORTED)[-1].detail["value_meanings_included"] is True


async def test_an_export_is_of_a_version_never_the_draft(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    with pytest.raises(NotFoundError, match="no published version"):
        await svc.export(conn, version=None, value_meanings=False, ctx=ctx())
    await _published(db, conn)
    draft = _with_meanings()
    draft.entities[1].label = "Buyers (draft)"
    await svc.save_draft(conn, draft, base_revision=1, ctx=ctx())
    file = await svc.export(conn, version=None, value_meanings=False, ctx=ctx())
    assert "Buyers (draft)" not in json.dumps(file.document)
    with pytest.raises(NotFoundError):
        await svc.export(conn, version=9, value_meanings=False, ctx=ctx())


# ── the round trip ───────────────────────────────────────────────────────
async def test_export_then_import_is_an_empty_change_list(db) -> None:  # noqa: F811
    source = connection(db)
    await _published(db, source)
    file = await service(db).export(source, version=None, value_meanings=True, ctx=ctx())

    target = connection(db)  # the same schema, another connection
    await service(db).import_file(
        target, file.model_dump(mode="json"), base_revision=0, ctx=ctx(OTHER)
    )
    published = SemanticDocument.model_validate(head(db, source).document)  # type: ignore[union-attr]
    imported = SemanticDocument.model_validate(head(db, target).draft_document)  # type: ignore[union-attr]
    assert diff_documents(published, imported) == []


async def test_without_value_meanings_the_round_trip_differs_only_by_them(db) -> None:  # noqa: F811
    source = connection(db)
    await _published(db, source)
    file = await service(db).export(source, version=None, value_meanings=False, ctx=ctx())

    target = connection(db)
    await service(db).import_file(target, file.model_dump(mode="json"), base_revision=0, ctx=ctx())
    published = SemanticDocument.model_validate(head(db, source).document)  # type: ignore[union-attr]
    imported = SemanticDocument.model_validate(head(db, target).draft_document)  # type: ignore[union-attr]
    assert [c.kind for c in diff_documents(published, imported)] == [d.VALUE_MEANINGS_CHANGED]


# ── import lands in the draft ────────────────────────────────────────────
async def test_an_import_lands_in_the_draft_and_publishes_as_imported(db) -> None:  # noqa: F811
    conn = connection(db)
    svc = service(db)
    await _published(db, conn, layer())
    before = dict(head(db, conn).document)  # type: ignore[union-attr]

    written, report = await svc.import_file(
        conn, _file(_with_meanings(), value_meanings=True), base_revision=1, ctx=ctx()
    )

    row = head(db, conn)
    assert row is not None
    assert row.document == before, "nothing a question reads moved"
    assert row.draft_origin == {"imported": True} and row.revision == 2
    assert (report.entities, report.unresolved, report.value_meaning_columns) == (2, 0, 1)
    assert report.value_meanings_included
    assert [c.kind for c in written.changes] == [d.COLUMN_ADDED]
    assert [r.detail for r in audit_rows(db, SEMANTIC_IMPORTED)] == [
        {"revision": 2, "entities": 2, "unresolved": 0, "invalid_metrics": 0}
    ]
    await svc.publish(conn, base_revision=2, ctx=ctx())
    assert versions(db, conn)[-1].origin == {"imported": True}


async def test_tables_this_schema_lacks_come_in_flagged_not_dropped(db) -> None:  # noqa: F811
    conn = connection(db)
    sync(db, conn, orders("id", "amount", "status"))  # no `customers` here
    doc = layer()
    doc.entities.append(SemanticEntity(
        table="public.invoices", label="Invoices",
        metrics=[SemanticMetric(name="billed", expression="SUM(amount)")],
    ))
    _, report = await service(db).import_file(conn, _file(doc), base_revision=0, ctx=ctx())

    draft = SemanticDocument.model_validate(head(db, conn).draft_document)  # type: ignore[union-attr]
    assert {e.table for e in draft.entities} == {
        "public.orders", "public.customers", "public.invoices",
    }
    assert not draft.entity("public.invoices").valid  # type: ignore[union-attr]
    assert not draft.entity("public.customers").valid  # type: ignore[union-attr]
    assert (report.unresolved, report.invalid_metrics) == (2, 1)


async def test_an_import_over_a_stale_revision_is_a_conflict(db) -> None:  # noqa: F811
    conn = connection(db)
    await _published(db, conn)
    with pytest.raises(SemanticConflictError):
        await service(db).import_file(conn, _file(layer()), base_revision=0, ctx=ctx(OTHER))
    response = await semantic_routes.import_semantic_layer(
        conn.id, SemanticImportRequest(file=_file(layer()), base_revision=0),
        ctx=ctx(), db=db, settings=semantic_service.Settings(), authz=OwnerOnlyAuthorizer(),
    )
    assert response.status_code == 409  # type: ignore[union-attr]


async def test_importing_what_is_already_there_is_nothing_to_do(db) -> None:  # noqa: F811
    conn = connection(db)
    await _published(db, conn)
    file = await service(db).export(conn, version=None, value_meanings=True, ctx=ctx())
    with pytest.raises(SemanticNoChangesError):
        await service(db).import_file(
            conn, file.model_dump(mode="json"), base_revision=1, ctx=ctx()
        )


# ── a hostile file ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "sentence"),
    [
        ([], "not a semantic layer file"),
        ({"format": "datamind.dashboard", "format_version": 1, "document": {}}, "format marker"),
        ({"format": FILE_FORMAT, "document": {}}, "which version"),
        ({"format": FILE_FORMAT, "format_version": 2, "document": {}}, "newer version"),
        ({"format": FILE_FORMAT, "format_version": 1}, "no document"),
        ({"format": FILE_FORMAT, "format_version": 1,
          "document": {"entities": [{"table": f"t{i}"} for i in range(2_001)]}}, "2,001 tables"),
        ({"format": FILE_FORMAT, "format_version": 1, "document": {"entities": "orders"}},
         "malformed"),
        ({"format": FILE_FORMAT, "format_version": 1,
          "document": {"business_context": "x" * 10_001}}, "the limit is 10,000"),
    ],
    ids=["not-an-object", "wrong-format", "no-version", "newer", "no-document",
         "too-many-tables", "malformed", "too-long"],
)
def test_a_file_that_is_not_one_is_refused_with_a_sentence(raw: Any, sentence: str) -> None:
    with pytest.raises(ValidationError, match=sentence):
        read_file(raw)


# ── the limits ───────────────────────────────────────────────────────────
MODELS = (
    SemanticDocument, TimeSemantics, SemanticEntity, SemanticColumn, SemanticMetric,
    SemanticJoin, GlossaryTerm,
)


def _texty(annotation: Any) -> bool:
    if annotation is str:
        return True
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    return origin in (list, dict) and bool(args) and all(a is str for a in args)


@pytest.mark.parametrize("model", MODELS, ids=[m.__name__ for m in MODELS])
def test_every_text_field_has_a_limit(model: type) -> None:
    """A field added to the model cannot quietly be unbounded prompt text."""
    hints = typing.get_type_hints(model)
    texty = {name for name in model.model_fields if _texty(hints[name])}  # type: ignore[attr-defined]
    missing = texty - set(limits.TEXT.get(model, {}))
    assert not missing, missing


def test_the_real_layers_sit_far_inside_the_limits() -> None:
    fixture = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "sales_semantic.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    raw.pop("_README", None)
    assert limits.problems(SemanticDocument.model_validate(raw)) == []


def test_the_editor_is_held_to_the_same_limits() -> None:
    doc = layer()
    doc.entities[0].description = "x" * 4_001
    with pytest.raises(ValidationError, match="`public.orders` description is 4,001 characters"):
        semantic_routes._document(doc.model_dump(mode="json"))
    for route in ("save_semantic_layer", "save_semantic_draft"):
        assert "_document(payload.document)" in inspect.getsource(getattr(semantic_routes, route))


async def test_a_generation_is_clipped_rather_than_refused(
    db, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    conn = connection(db)
    sync(db, conn, orders("id", "amount", "status"), customers())
    generation = BlockingGeneration(SemanticDocument(entities=[
        SemanticEntity(table="public.orders", description="y" * 9_000),
    ]))
    generation.release.set()
    _wire(monkeypatch, generation)
    await service(db).execute_job(_job(db, conn, llm_config(db)).id, asyncio.Event())
    draft = SemanticDocument.model_validate(head(db, conn).draft_document)  # type: ignore[union-attr]
    assert len(draft.entity("public.orders").description) == 4_000  # type: ignore[union-attr]
    assert limits.problems(draft) == []


# ── not a guard entry point: the hostile corpus, as expressions ──────────
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Merge",
        "TruncateTable", "Copy", "Grant", "Revoke", "Into", "Use", "Set", "Transaction",
        "Commit", "Rollback", "Pragma", "Execute", "LoadData",
    )
)


def _as_expressions(sql: str) -> list[str]:
    """A statement as a curator could paste it, and the part after `SELECT` —
    the shape a metric expression actually has."""
    text = sql.strip()
    out = [text]
    if text.upper().startswith("SELECT "):
        out.append(text[len("SELECT "):])
    return out


@pytest.mark.parametrize(("sql", "_rule"), HOSTILE, ids=[s[:40] or "empty" for s, _ in HOSTILE])
def test_the_hostile_corpus_is_refused_or_inert_as_a_metric(sql: str, _rule: Any) -> None:
    index = build_index([orders("id", "amount", "status", "total_amount")], "postgres")
    for text in _as_expressions(sql):
        for boolean in (False, True):
            valid, _issue = check_expression(
                text, entity_table="public.orders", index=index, boolean=boolean
            )
            if not valid:
                continue
            probe = (
                f"SELECT 1 FROM public.orders WHERE {text}" if boolean  # noqa: S608
                else f"SELECT {text} AS m FROM public.orders"  # noqa: S608
            )
            statements = sqlglot.parse(probe, read="postgres")
            assert len(statements) == 1, (text, boolean)
            assert isinstance(statements[0], exp.Select), (text, boolean)
            assert not any(isinstance(n, FORBIDDEN_NODES) for n in statements[0].walk()), (
                text, boolean,
            )


# ── routes ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("name", "privilege"),
    [("export_semantic_layer", Privilege.SELECT), ("import_semantic_layer", Privilege.MODIFY)],
)
def test_each_transfer_route_asks_one_privilege_on_the_layer(
    name: str, privilege: Privilege
) -> None:
    source = inspect.getsource(getattr(semantic_routes, name))
    asked = [p for p in Privilege if f"Privilege.{p.name}" in source]
    assert asked == [privilege]
    assert "_authorized(db, authz, connection_id, ctx," in source


def test_the_transfer_routes_are_published() -> None:
    from app.main import create_app
    from tests.unit.test_authz_conformance import _routes

    paths = {(m, r.path) for r in _routes(create_app()) for m in r.methods or set()}
    base = "/connections/{connection_id}/semantic"
    assert ("GET", base + "/export") in paths
    assert ("POST", base + "/import") in paths
