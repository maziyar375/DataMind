"""The curation surface: ownership scoping, `can_curate`, and the save gate.

Three properties, in the order they would hurt if they broke:

* **Ownership scoping.** A template is reachable only through a connection the
  caller owns. Another user's connection is a 404, not a 403 — the same answer
  the semantic layer gives, because "this exists but is not yours" is itself a
  disclosure.
* **`can_curate` gates every write, and no endpoint checks `is_admin`.** That
  is decision D4: curation is open to any signed-in user today, and one
  settings flag makes it admin-only later without touching a call site. The
  test runs the whole write surface in *both* settings.
* **Reading is never gated.** Seeing what the system knows is not a privilege,
  so a reader who cannot curate still gets the list, the capabilities and the
  live check.
"""
from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.clock import utcnow
from app.core.config import Settings, get_settings
from app.core.context import RequestContext
from app.infra.db.models import (
    DatabaseConnection,
    KnowledgeTemplateRow,
    SchemaSnapshotRow,
)
from app.main import create_app

USER = uuid4()
STRANGER = uuid4()
CONNECTION_ID = uuid4()

BASE = f"/api/v1/connections/{CONNECTION_ID}/knowledge"

TABLES = [
    {
        "schema": "public",
        "name": "orders",
        "columns": [
            {"name": "id", "data_type": "bigint", "is_primary_key": True},
            {"name": "created_at", "data_type": "timestamp with time zone"},
            {"name": "region", "data_type": "text", "distinct_count": 3},
            {"name": "status", "data_type": "text"},
            {"name": "amount", "data_type": "numeric(12,2)"},
        ],
    }
]

GOOD_SQL = (
    "SELECT SUM(o.amount) AS revenue FROM orders o "
    "WHERE o.region = :region AND o.status <> 'CANCELLED'"
)


def _connection(owner_id: UUID = USER) -> DatabaseConnection:
    return DatabaseConnection(
        id=CONNECTION_ID,
        owner_id=owner_id,
        name="aurora",
        database_type="postgres",
        host="db.internal",
        port=5432,
        database_name="aurora",
        username="analytics_ro",
        encrypted_password="ciphertext",
        max_rows=1000,
        statement_timeout_ms=30_000,
        disclosure_policy="SAMPLE",
    )


def _snapshot(version: int = 4) -> SchemaSnapshotRow:
    return SchemaSnapshotRow(
        id=uuid4(),
        connection_id=CONNECTION_ID,
        version=version,
        dialect="postgres",
        tables=TABLES,
        relationships=[],
        table_count=len(TABLES),
    )


class FakeDb:
    """Enough of an `AsyncSession` for the knowledge routes.

    Queries are told apart by what they *select* rather than by call order, so
    a route that grows a query nobody expected fails loudly here instead of
    silently receiving a connection.
    """

    def __init__(
        self,
        connection: DatabaseConnection | None,
        snapshot: SchemaSnapshotRow | None = None,
    ) -> None:
        self.connection = connection
        self.snapshot = snapshot
        self.rows: list[KnowledgeTemplateRow] = []
        self.flushes = 0

    async def execute(self, statement: Any) -> Any:
        selected = statement.column_descriptions[0]
        entity, name = selected.get("entity"), selected.get("name")
        if entity is DatabaseConnection:
            # The owner predicate is honoured rather than ignored: scoping is
            # the property this file exists to prove, and a fake that answered
            # every lookup with the same row would prove it for nobody.
            owner = statement.compile().params.get("owner_id_1")
            scoped = (
                self.connection
                if self.connection is not None
                and (owner is None or self.connection.owner_id == owner)
                else None
            )
            return _Result(scoped)
        if entity is SchemaSnapshotRow and name == "SchemaSnapshotRow":
            return _Result(self.snapshot)
        if name == "version":
            return _Result(self.snapshot.version if self.snapshot else None)
        if entity is KnowledgeTemplateRow:
            return _Result(None, rows=self._visible(statement))
        raise AssertionError(f"unexpected query: {statement}")

    def _visible(self, statement: Any) -> list[KnowledgeTemplateRow]:
        text = str(statement)
        rows = list(self.rows)
        if "status !=" in text or "status IS NOT" in text:
            rows = [r for r in rows if r.status != "ARCHIVED"]
        return rows

    def add(self, obj: Any) -> None:
        obj.created_at = obj.created_at or utcnow()
        obj.updated_at = obj.updated_at or utcnow()
        self.rows.append(obj)

    async def flush(self) -> None:
        self.flushes += 1

    async def get(self, _model: Any, key: UUID) -> Any:
        return next((r for r in self.rows if r.id == key), None)

    async def delete(self, obj: Any) -> None:  # pragma: no cover - archive only
        self.rows.remove(obj)


class _Result:
    def __init__(self, value: Any, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self) -> Any:
        return _Scalars(self._rows)


class _Scalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


def _client(
    db: FakeDb, *, user: UUID = USER, admin_only: bool = False, admin: bool = False
) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[deps.get_ctx] = lambda: RequestContext(
        user_id=user,
        email="user@test.local",
        role="ADMIN" if admin else "MEMBER",
        correlation_id="test",
    )
    settings = Settings(curation_admin_only=admin_only)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[deps.get_settings] = lambda: settings
    client = TestClient(app)
    client.db = db  # type: ignore[attr-defined]
    return client


@pytest.fixture
def client() -> Any:
    return _client(FakeDb(_connection(), _snapshot()))


# ── authoring ────────────────────────────────────────────────────────────
def test_a_curator_can_teach_a_question(client: Any) -> None:
    response = client.post(
        f"{BASE}/templates",
        json={
            "question": "revenue for {region}",
            "sql": GOOD_SQL,
            "params": [{"name": "region", "type": "string", "comment": "one of: EMEA"}],
            "note": "Cancelled orders are never revenue.",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["question"] == "revenue for {region}"
    assert body["question_normalized"] == "revenue for *"
    assert body["status"] == "ACTIVE" and body["role"] == "RETRIEVABLE"
    assert body["referenced_tables"] == ["public.orders"]
    # The snapshot it validated against, so the UI can say when the schema has
    # moved on underneath it.
    assert body["schema_version"] == 4


def test_a_statement_the_guard_rejects_is_not_stored(client: Any) -> None:
    response = client.post(
        f"{BASE}/templates",
        json={"question": "everything", "sql": "SELECT * FROM pg_shadow"},
    )
    assert response.status_code == 422
    assert client.db.rows == []
    # The guard's own message, verbatim. Rewriting it into something friendlier
    # loses the reason.
    assert "pg_shadow" in response.json()["detail"]


def test_a_parameter_the_question_never_names_is_refused(client: Any) -> None:
    # It could never be filled in, so the template would be stored and never
    # match — cheaper to say so now than to let the curator discover silence.
    response = client.post(
        f"{BASE}/templates",
        json={
            "question": "revenue",
            "sql": GOOD_SQL,
            "params": [{"name": "region", "type": "string"}],
        },
    )
    assert response.status_code == 422
    assert "{region}" in response.json()["detail"]


def test_teaching_needs_a_synced_schema(client: Any) -> None:
    client.db.snapshot = None
    response = client.post(
        f"{BASE}/templates", json={"question": "revenue", "sql": "SELECT 1 FROM orders"}
    )
    assert response.status_code == 422
    assert "Sync this connection's schema" in response.json()["detail"]


# ── editing and archiving ────────────────────────────────────────────────
def _teach(client: Any, question: str = "revenue for {region}") -> dict[str, Any]:
    return client.post(
        f"{BASE}/templates",
        json={
            "question": question,
            "sql": GOOD_SQL,
            "params": [{"name": "region", "type": "string"}],
        },
    ).json()


def test_an_edit_revalidates_and_restamps_the_schema_version(client: Any) -> None:
    created = _teach(client)
    client.db.snapshot = _snapshot(version=9)

    response = client.patch(
        f"{BASE}/templates/{created['id']}", json={"note": "use orders, not the rollup"}
    )
    assert response.status_code == 200
    assert response.json()["note"] == "use orders, not the rollup"
    assert response.json()["schema_version"] == 9


def test_an_edit_that_breaks_the_sql_is_refused(client: Any) -> None:
    created = _teach(client)
    response = client.patch(
        f"{BASE}/templates/{created['id']}",
        json={"sql": "SELECT * FROM users", "params": []},
    )
    assert response.status_code == 422
    # The stored row is untouched.
    assert client.db.rows[0].sql == GOOD_SQL


def test_delete_archives_rather_than_destroying(client: Any) -> None:
    created = _teach(client)
    response = client.delete(f"{BASE}/templates/{created['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "ARCHIVED"
    # The row is still there — the system does not destroy a person's work.
    assert len(client.db.rows) == 1


def test_the_system_verdicts_cannot_be_set_by_hand(client: Any) -> None:
    created = _teach(client)
    # `STALE` and `CONFLICTED` are what Phase 4 writes when it re-validates.
    # A form that could set them would let a curator hide drift.
    response = client.patch(
        f"{BASE}/templates/{created['id']}", json={"status": "STALE"}
    )
    assert response.status_code == 422


def test_an_edit_clears_the_reason_it_no_longer_explains(client: Any) -> None:
    created = _teach(client)
    client.db.rows[0].status = "STALE"
    client.db.rows[0].status_reason = "`orders.region` no longer exists."

    body = client.patch(
        f"{BASE}/templates/{created['id']}", json={"status": "ACTIVE"}
    ).json()
    assert body["status"] == "ACTIVE" and body["status_reason"] == ""


# ── the live check ───────────────────────────────────────────────────────
def test_check_returns_the_verdict_and_the_proposals_in_one_round_trip(
    client: Any,
) -> None:
    response = client.post(
        f"{BASE}/templates/check",
        json={
            "question": "revenue for {region} since {from_date}",
            "sql": (
                "SELECT SUM(o.amount) FROM orders o WHERE o.region = 'EMEA' "
                "AND o.status <> 'CANCELLED' AND o.created_at >= '2026-01-01'"
            ),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["referenced_tables"] == ["public.orders"]
    assert [p["name"] for p in body["proposals"]] == ["region", "status", "from_date"]
    # The refusal travels with its reason, so the editor can show it unticked.
    refused = next(p for p in body["proposals"] if p["name"] == "status")
    assert refused["eligible"] is False and "≠" in refused["reason"]
    assert body["question_slots"] == ["region", "from_date"]


def test_check_does_the_substitution_when_the_curator_has_ticked(client: Any) -> None:
    # On the tree, in the server, so the statement that gets saved is the one
    # the guard just read.
    response = client.post(
        f"{BASE}/templates/check",
        json={
            "question": "revenue for {region}",
            "sql": "SELECT SUM(o.amount) FROM orders o WHERE o.region = 'EMEA'",
            "accept": ["region"],
        },
    )
    body = response.json()
    assert ":region" in body["sql"] and "'EMEA'" not in body["sql"]
    assert [p["name"] for p in body["params"]] == ["region"]


def test_check_reports_a_rejection_instead_of_raising(client: Any) -> None:
    body = client.post(
        f"{BASE}/templates/check", json={"sql": "SELECT * FROM pg_shadow"}
    ).json()
    assert body["valid"] is False and body["issue"]
    assert body["issues"][0]["rule_id"]


def test_check_writes_nothing(client: Any) -> None:
    client.post(f"{BASE}/templates/check", json={"sql": GOOD_SQL})
    assert client.db.rows == [] and client.db.flushes == 0


# ── listing ──────────────────────────────────────────────────────────────
def test_the_list_hides_archived_rows_unless_asked(client: Any) -> None:
    created = _teach(client)
    client.delete(f"{BASE}/templates/{created['id']}")

    assert client.get(f"{BASE}/templates").json()["templates"] == []
    with_archive = client.get(f"{BASE}/templates?include_archived=true").json()
    assert len(with_archive["templates"]) == 1


def test_the_list_reports_drift_without_persisting_it(client: Any) -> None:
    """Phase 1 changes no behaviour, so it reports drift and does not act on it.

    The semantic layer computes drift on read for the same reason: the UI shows
    a template that stopped working the moment a re-sync creates that fact,
    with no migration and no background job. Withdrawing it from use is a
    behaviour change and belongs to Phase 4.
    """
    created = _teach(client)
    client.db.snapshot = SchemaSnapshotRow(
        id=uuid4(), connection_id=CONNECTION_ID, version=5, dialect="postgres",
        tables=[{"schema": "public", "name": "orders",
                 "columns": [{"name": "id", "data_type": "bigint"}]}],
        relationships=[], table_count=1,
    )

    body = client.get(f"{BASE}/templates").json()
    assert body["stale_ids"] == [created["id"]]
    # Reported, not written.
    assert client.db.rows[0].status == "ACTIVE"


def test_the_list_says_whether_the_connection_has_ever_been_synced(
    client: Any,
) -> None:
    assert client.get(f"{BASE}/templates").json()["schema_synced"] is True
    client.db.snapshot = None
    assert client.get(f"{BASE}/templates").json()["schema_synced"] is False


# ── ownership scoping ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", "/templates", None),
        ("get", "/capabilities", None),
        ("post", "/templates/check", {"sql": "SELECT 1"}),
        ("post", "/templates", {"question": "q", "sql": "SELECT 1 FROM orders"}),
    ],
)
def test_another_users_connection_is_a_404_on_every_route(
    method: str, path: str, payload: dict[str, Any] | None
) -> None:
    # 404 rather than 403: "this exists but is not yours" is itself a
    # disclosure, and it is the answer the semantic layer already gives.
    client = _client(FakeDb(_connection(owner_id=STRANGER), _snapshot()))
    call = getattr(client, method)
    response = call(BASE + path, json=payload) if payload else call(BASE + path)
    assert response.status_code == 404


def test_a_template_on_another_connection_is_not_reachable(client: Any) -> None:
    created = _teach(client)
    client.db.rows[0].connection_id = uuid.uuid4()
    assert client.patch(
        f"{BASE}/templates/{created['id']}", json={"note": "x"}
    ).status_code == 404


# ── can_curate, in both settings ─────────────────────────────────────────
WRITES = [
    ("post", "/templates", {"question": "q {region}", "sql": GOOD_SQL,
                            "params": [{"name": "region", "type": "string"}]}),
    ("patch", "/templates/{id}", {"note": "x"}),
    ("delete", "/templates/{id}", None),
]


@pytest.mark.parametrize("method,path,payload", WRITES)
def test_any_signed_in_user_may_curate_by_default(
    method: str, path: str, payload: dict[str, Any] | None
) -> None:
    client = _client(FakeDb(_connection(), _snapshot()))
    created = _teach(client, question="seed {region}")
    call = getattr(client, method)
    url = BASE + path.format(id=created["id"])
    response = call(url, json=payload) if payload else call(url)
    assert response.status_code in (200, 201), response.text


@pytest.mark.parametrize("method,path,payload", WRITES)
def test_the_flag_makes_every_write_admin_only(
    method: str, path: str, payload: dict[str, Any] | None
) -> None:
    """One env var, and every write path moves — because they all ask the same
    function. No endpoint here checks `ctx.is_admin` directly."""
    seeded = _client(FakeDb(_connection(), _snapshot()))
    created = _teach(seeded, question="seed {region}")

    member = _client(seeded.db, admin_only=True)
    call = getattr(member, method)
    url = BASE + path.format(id=created["id"])
    response = call(url, json=payload) if payload else call(url)
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"]

    admin = _client(seeded.db, admin_only=True, admin=True)
    call = getattr(admin, method)
    response = call(url, json=payload) if payload else call(url)
    assert response.status_code in (200, 201), response.text


def test_reading_stays_open_when_curation_is_closed() -> None:
    # Seeing what the system knows is not a privilege, and a list that
    # disappeared when the flag flipped would hide the store from the people
    # who most need to read it.
    seeded = _client(FakeDb(_connection(), _snapshot()))
    _teach(seeded, question="seed {region}")

    member = _client(seeded.db, admin_only=True)
    listing = member.get(f"{BASE}/templates")
    assert listing.status_code == 200 and len(listing.json()["templates"]) == 1
    assert listing.json()["can_curate"] is False
    assert member.post(f"{BASE}/templates/check", json={"sql": GOOD_SQL}).status_code == 200


def test_capabilities_says_which_buttons_should_exist() -> None:
    # So the UI hides rather than disables: a disabled control the reader can
    # never enable is an insult.
    open_client = _client(FakeDb(_connection(), _snapshot()))
    assert open_client.get(f"{BASE}/capabilities").json() == {"can_curate": True}

    closed = _client(FakeDb(_connection(), _snapshot()), admin_only=True)
    assert closed.get(f"{BASE}/capabilities").json() == {"can_curate": False}


def test_no_knowledge_endpoint_checks_is_admin_directly() -> None:
    """D4, enforced on the parse rather than on the prose.

    The discipline — every write asks one function — is what makes flipping
    curation to admin-only a single line in `policy.py` instead of an audit of
    every route. A grep would trip over this module's own docstring saying so,
    so the check reads the AST: no attribute access named `is_admin` anywhere
    in the file.
    """
    import ast
    from pathlib import Path

    source = Path("app/api/v1/knowledge.py").read_text()
    tree = ast.parse(source)
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "is_admin" not in attributes
    assert "can_curate" in {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
