"""The five section routes, called — as the connection's people, over real SQL.

A section is a leaf of its connection (`docs/plans/retrieval-sections.md` §10):
no resource type, no privilege and no grant of its own. So the whole access
story is the connection's, asked through `policy.require` — `select` to read or
propose, `modify` to change the division — with 404 above 403 for somebody the
connection does not reach at all.

Built on `test_access_behaviour`'s `World`: five people, one connection, the
real app over the conftest's SQLite schema.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.infra.db import models
from tests.unit.conftest import AsyncSessionShim, _team_grant
from tests.unit.test_access_behaviour import (  # noqa: F401 — `_every_table` is a fixture
    NOTHING,
    World,
    _every_table,
    _message,
)


def _t(name: str, columns: int = 4) -> dict[str, Any]:
    return {
        "schema": "public", "name": name, "approx_row_count": 0,
        "columns": [{"name": f"c{i}", "data_type": "text"} for i in range(columns)],
    }


TABLES = [
    _t("orders"), _t("order_items"), _t("customers"),
    _t("products"), _t("product_tags"), _t("tags"),
    _t("audit_log"),
]
RELS = [
    {"from_table": f"public.{a}", "from_column": "x", "to_table": f"public.{b}",
     "to_column": "id"}
    for a, b in [("order_items", "orders"), ("orders", "customers"),
                 ("product_tags", "products"), ("product_tags", "tags")]
]


@pytest.fixture
def world(db: AsyncSessionShim) -> World:
    return World(db)


@pytest.fixture
def synced(world: World) -> World:
    """The world's connection, with a schema snapshot to divide."""
    s = world.db._session
    s.add(models.SchemaSnapshotRow(
        id=uuid4(), connection_id=world.conn, version=3, dialect="postgres",
        tables=TABLES, relationships=RELS, table_count=len(TABLES), catalog_meta={},
    ))
    s.flush()
    return world


def _url(world: World, tail: str = "") -> str:
    return f"/api/v1/connections/{world.conn}/sections{tail}"


async def _keep(world: World, method: str, url: str, body: Any = None) -> httpx.Response:
    """`World.call` without the rollback, for a write the next call must see."""
    transport = httpx.ASGITransport(app=world.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://w") as c:
        return await c.request(method, url, json=body)


def _grant(world: World, who: UUID, privilege: str) -> None:
    _team_grant(world.db._session, type_="connection", resource_id=world.conn,
                privilege=privilege, user=who)


SALES = {"name": "Sales", "description": "Orders and the people who place them.",
         "tables": ["public.orders", "public.order_items", "public.customers"]}
CATALOG = {"name": "Catalog", "description": "",
           "tables": ["public.products", "public.product_tags", "public.tags"]}


# ── reading ──────────────────────────────────────────────────────────────
async def test_nothing_saved_reads_as_an_empty_set_not_a_proposal(synced: World) -> None:
    response = await synced.as_(synced.owner).call("GET", _url(synced))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is False
    assert body["sections"] == []
    assert body["unassigned"] == [f"public.{t['name']}" for t in TABLES]
    assert body["has_snapshot"] is True
    assert body["snapshot_version"] == 3
    assert [e["table"] for e in body["catalog"]] == body["unassigned"]


async def test_a_proposal_is_computed_and_never_written(synced: World) -> None:
    _grant(synced, synced.viewer, "select")
    proposed = await _keep(synced.as_(synced.viewer), "POST", _url(synced, "/propose"))
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["saved"] is False
    assert {s["origin"] for s in body["sections"]} == {"PROPOSED"}
    assert all(s["id"] is None for s in body["sections"])
    assert body["unassigned"] == ["public.audit_log"]
    placed = [t for s in body["sections"] for t in s["tables"]] + body["unassigned"]
    assert sorted(placed) == sorted(f"public.{t['name']}" for t in TABLES)
    assert all(s["fit"] == "FITS" for s in body["sections"])

    after = await synced.call("GET", _url(synced))
    assert after.json()["saved"] is False


async def test_propose_can_divide_just_one_sections_tables(synced: World) -> None:
    response = await synced.as_(synced.owner).call(
        "POST", _url(synced, "/propose"),
        {"tables": ["public.orders", "public.order_items", "public.customers"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["tables"] for s in body["sections"]] == [
        ["public.orders", "public.order_items", "public.customers"]
    ]
    assert body["unassigned"] == []


async def test_an_unsynced_connection_is_told_to_sync(world: World) -> None:
    response = await world.as_(world.owner).call("POST", _url(world, "/propose"))
    assert response.status_code == 422
    assert "Sync it" in _message(response)


# ── writing ──────────────────────────────────────────────────────────────
async def test_save_then_read_shows_it(synced: World) -> None:
    _grant(synced, synced.editor, "modify")
    saved = await _keep(synced.as_(synced.editor), "PUT", _url(synced),
                        {"sections": [SALES, CATALOG]})
    assert saved.status_code == 200, saved.text

    body = (await synced.call("GET", _url(synced))).json()
    assert body["saved"] is True
    assert [s["name"] for s in body["sections"]] == ["Sales", "Catalog"]
    assert [s["position"] for s in body["sections"]] == [0, 1]
    assert {s["origin"] for s in body["sections"]} == {"CURATED"}
    assert {s["schema_version"] for s in body["sections"]} == {3}
    assert body["sections"][0]["tables"] == SALES["tables"]
    assert body["unassigned"] == ["public.audit_log"]


async def test_a_moved_table_and_a_swapped_name_keep_their_ids(synced: World) -> None:
    """The whole set in one transaction: a move is one edit to two rows, and a
    rename that swaps two names never meets the unique index halfway."""
    first = (await _keep(synced.as_(synced.owner), "PUT", _url(synced),
                         {"sections": [SALES, CATALOG]})).json()["sections"]
    sales_id, catalog_id = first[0]["id"], first[1]["id"]

    moved = await _keep(synced, "PUT", _url(synced), {"sections": [
        {**SALES, "id": sales_id, "name": "Catalog",
         "tables": [*SALES["tables"], "public.tags"]},
        {**CATALOG, "id": catalog_id, "name": "Sales",
         "tables": ["public.products", "public.product_tags"]},
    ]})
    assert moved.status_code == 200, moved.text
    sections = moved.json()["sections"]
    assert [(s["id"], s["name"]) for s in sections] == [
        (sales_id, "Catalog"), (catalog_id, "Sales"),
    ]
    assert "public.tags" in sections[0]["tables"]
    assert "public.tags" not in sections[1]["tables"]


@pytest.mark.parametrize(("sections", "says"), [
    ([SALES, {**CATALOG, "name": "sales"}], "Two sections are called"),
    ([SALES, {**CATALOG, "tables": ["public.orders"]}], "a table belongs to one section"),
    ([{**SALES, "tables": ["public.nope"]}], "is not a table"),
    ([{**SALES, "name": "Unassigned"}], "reserved"),
    ([{**SALES, "name": "NONE"}], "reserved"),
    ([{**SALES, "name": "Sales, marketing"}], "comma"),
    ([{**SALES, "name": "  "}], "needs a name"),
    ([{**SALES, "description": "x" * 1_001}], "longer than"),
])
async def test_a_set_that_cannot_be_routed_is_refused(
    synced: World, sections: list[dict[str, Any]], says: str
) -> None:
    response = await synced.as_(synced.owner).call(
        "PUT", _url(synced), {"sections": sections}
    )
    assert response.status_code == 422, response.text
    assert says in _message(response)


async def test_deleting_one_section_unassigns_its_tables(synced: World) -> None:
    saved = (await _keep(synced.as_(synced.owner), "PUT", _url(synced),
                         {"sections": [SALES, CATALOG]})).json()["sections"]
    gone = await _keep(synced, "DELETE", _url(synced, f"/{saved[1]['id']}"))
    assert gone.status_code == 204, gone.text

    body = (await synced.call("GET", _url(synced))).json()
    assert [s["name"] for s in body["sections"]] == ["Sales"]
    assert set(CATALOG["tables"]) <= set(body["unassigned"])


async def test_a_section_of_another_connection_is_not_found(synced: World) -> None:
    await _keep(synced.as_(synced.owner), "PUT", _url(synced), {"sections": [SALES]})
    response = await synced.call("DELETE", _url(synced, f"/{uuid4()}"))
    assert response.status_code == 404


async def test_clearing_turns_the_feature_off(synced: World) -> None:
    await _keep(synced.as_(synced.owner), "PUT", _url(synced), {"sections": [SALES]})
    cleared = await _keep(synced, "DELETE", _url(synced))
    assert cleared.status_code == 204
    assert (await synced.call("GET", _url(synced))).json()["saved"] is False


# ── who may ──────────────────────────────────────────────────────────────
READS = [("GET", ""), ("POST", "/propose")]
WRITES = [("PUT", ""), ("DELETE", ""), ("DELETE", "/{sid}")]


def _body(method: str) -> Any:
    return {"sections": [SALES]} if method == "PUT" else None


@pytest.mark.parametrize(("method", "tail"), READS + WRITES)
async def test_a_stranger_is_told_nothing_exists(
    synced: World, method: str, tail: str
) -> None:
    """404 above 403: nothing reaches them, so the connection is indistinguishable
    from one that does not exist."""
    response = await synced.as_(synced.stranger, NOTHING).call(
        method, _url(synced, tail.format(sid=uuid4())), _body(method)
    )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize(("method", "tail"), READS)
async def test_describe_is_not_enough_to_read_the_division(
    synced: World, method: str, tail: str
) -> None:
    response = await synced.as_(synced.glancer).call(method, _url(synced, tail))
    assert response.status_code == 403, response.text
    assert "select" in _message(response)


@pytest.mark.parametrize(("method", "tail"), WRITES)
async def test_select_reads_but_does_not_divide(
    synced: World, method: str, tail: str
) -> None:
    _grant(synced, synced.viewer, "select")
    assert (await synced.as_(synced.viewer).call("GET", _url(synced))).status_code == 200
    response = await synced.call(method, _url(synced, tail.format(sid=uuid4())),
                                 _body(method))
    assert response.status_code == 403, response.text
    assert "modify" in _message(response)
