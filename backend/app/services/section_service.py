"""Connection sections: read, propose, save, and turn off.

`docs/plans/retrieval-sections.md`. The algorithm lives in
`app/pipeline/sections.py` and is pure; this module owns the rows and the
transaction, and answers every read in one shape — the sections, the tables in
none of them, and what each costs — so the screen draws a proposal and a saved
set the same way.

**Nothing on the ask path reads this module yet** (Phase 1). A connection with
sections saved answers every question exactly as one without; the `scope` node
that reads them is Phase 2.

Access is the connection's, decided by the router before any method here is
called: a section is a leaf of its connection the way a semantic layer is, with
no resource type, privilege or grant of its own (plan §10).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.infra.db.models import ConnectionSection, DatabaseConnection, SchemaSnapshotRow
from app.pipeline import sections as algo
from app.pipeline.metadata import table_chars
from app.pipeline.nodes import retrieve_budget_chars
from app.services.semantic_service import load_layer

_NO_SNAPSHOT = "This connection has no schema snapshot. Sync it, then try again."


@dataclass(frozen=True, slots=True)
class SectionView:
    """One section as the screen draws it, saved or proposed."""

    id: UUID | None
    name: str
    description: str
    tables: list[str]
    origin: str
    schema_version: int | None
    position: int
    #: `table_chars` over the members still in the snapshot — the figure
    #: `retrieve` decides with, so the badge and the runtime cannot disagree.
    chars: int
    fit: algo.Fit
    #: Members the current snapshot no longer has. Kept, never deleted:
    #: deleting a person's work to hide drift is worse than showing it.
    missing: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SectionSet:
    #: Whether this is what the connection has saved, or a proposal nobody has.
    saved: bool
    sections: list[SectionView]
    #: Tables in the snapshot and in no section, in snapshot order.
    unassigned: list[str]
    #: Every table in the snapshot with its weight, in snapshot order, so the
    #: screen can re-size a section as tables move without asking again.
    catalog: list[tuple[str, int]]
    budget_chars: int
    snapshot_version: int
    has_snapshot: bool


@dataclass(frozen=True, slots=True)
class SectionWrite:
    name: str
    description: str = ""
    tables: Sequence[str] = ()
    id: UUID | None = None


class SectionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── reads ────────────────────────────────────────────────────────────
    async def read(self, connection: DatabaseConnection) -> SectionSet:
        """What is saved. Nothing saved is an empty set, not a proposal — the
        screen asks for a proposal explicitly, so a GET never computes one."""
        snapshot = await self._snapshot(connection.id)
        rows = await self._rows(connection.id)
        return self._shape(
            snapshot,
            [
                (row.id, row.name, row.description, list(row.tables), row.origin,
                 row.schema_version)
                for row in rows
            ],
            saved=bool(rows),
        )

    async def propose(
        self, connection: DatabaseConnection, *, only: Iterable[str] | None = None
    ) -> SectionSet:
        """A complete division of the snapshot, computed and returned — **never
        written**. With `only`, a division of just those tables: how *Split this
        section* asks for one section's members to be divided again."""
        snapshot = await self._snapshot(connection.id)
        if not snapshot["tables"]:
            raise ValidationError(_NO_SNAPSHOT)
        layer = await load_layer(self._db, connection, snapshot=snapshot)
        proposal = algo.propose(
            snapshot,
            semantic=layer.document.model_dump(mode="json") if layer.document else None,
            budget_chars=retrieve_budget_chars(),
            only=only,
        )
        return self._shape(
            snapshot,
            [
                (None, s.name, s.description, list(s.tables), "PROPOSED", None)
                for s in proposal.sections
            ],
            saved=False,
            universe=None if only is None else set(only),
        )

    # ── writes ───────────────────────────────────────────────────────────
    async def save(
        self, connection: DatabaseConnection, incoming: Sequence[SectionWrite]
    ) -> SectionSet:
        """Replace the connection's sections with `incoming`, in one transaction.

        The whole set rather than one section, because sections partition a
        set of tables: moving `public.refunds` from *Operations* to *Sales* is
        one edit to two rows, and two requests could interleave into a table
        that is in both or neither.

        An id the connection already has keeps its row (and its `created_at`);
        any other id, or none, is a new section. Everything saved is CURATED —
        a person has looked at it — and stamped with the snapshot it was saved
        against.
        """
        snapshot = await self._snapshot(connection.id)
        if not snapshot["tables"]:
            raise ValidationError(_NO_SNAPSHOT)
        existing = await self._rows(connection.id)
        cleaned = self._validate(incoming, snapshot, existing)

        kept = {row.id: row for row in existing}
        for row in existing:
            await self._db.delete(row)
        # Flushed apart from the inserts, so a rename that swaps two names is a
        # delete and then an insert, never an update that meets the unique
        # index halfway through.
        await self._db.flush()

        for position, section in enumerate(cleaned):
            old = kept.get(section.id) if section.id else None
            self._db.add(ConnectionSection(
                id=old.id if old else uuid.uuid4(),
                connection_id=connection.id,
                name=section.name,
                description=section.description,
                tables=list(section.tables),
                origin="CURATED",
                schema_version=snapshot["version"],
                position=position,
                **({"created_at": old.created_at} if old else {}),
            ))
        # Inside the handler, so a refusal from the database is an error the
        # caller sees rather than a 200 over a rolled-back write.
        await self._db.flush()
        return await self.read(connection)

    async def delete(self, connection: DatabaseConnection, section_id: UUID) -> None:
        """One section. Its tables go to Unassigned — nothing else moves."""
        row = await self._db.get(ConnectionSection, section_id)
        if row is None or row.connection_id != connection.id:
            raise NotFoundError("Section not found.")
        await self._db.delete(row)
        await self._db.flush()

    async def clear(self, connection: DatabaseConnection) -> None:
        """Every section: the feature off for this connection."""
        for row in await self._rows(connection.id):
            await self._db.delete(row)
        await self._db.flush()

    # ── helpers ──────────────────────────────────────────────────────────
    def _validate(
        self,
        incoming: Sequence[SectionWrite],
        snapshot: dict[str, Any],
        existing: Sequence[ConnectionSection],
    ) -> list[SectionWrite]:
        """The set as it will be stored, or a `ValidationError` naming why not.

        A table may be named if the snapshot has it, or if a saved section
        already held it — so drift never blocks a save, and a name that was
        never anywhere (a typo, a guess) is refused rather than stored.
        """
        if len(incoming) > algo.MAX_SECTIONS:
            raise ValidationError(
                f"A connection can have at most {algo.MAX_SECTIONS} sections."
            )
        known = {algo.key(t) for t in snapshot["tables"]}
        known |= {t for row in existing for t in row.tables}

        names: set[str] = set()
        placed: dict[str, str] = {}
        out: list[SectionWrite] = []
        for section in incoming:
            name = section.name.strip()
            problem = algo.valid_name(name)
            if problem:
                raise ValidationError(problem)
            if name.lower() in names:
                raise ValidationError(f'Two sections are called "{name}".')
            names.add(name.lower())

            description = " ".join(section.description.split())
            if len(description) > algo.MAX_DESCRIPTION_CHARS:
                raise ValidationError(
                    f'The description of "{name}" is longer than '
                    f"{algo.MAX_DESCRIPTION_CHARS} characters."
                )

            tables: list[str] = []
            for table in section.tables:
                if table not in known:
                    raise ValidationError(
                        f'"{table}" is not a table in this connection\'s schema.'
                    )
                if table in placed and placed[table] != name:
                    raise ValidationError(
                        f'"{table}" is in both "{placed[table]}" and "{name}" — '
                        "a table belongs to one section."
                    )
                if table not in tables:
                    tables.append(table)
                    placed[table] = name
            out.append(SectionWrite(
                name=name, description=description, tables=tables, id=section.id,
            ))
        return out

    def _shape(
        self,
        snapshot: dict[str, Any],
        rows: list[tuple[UUID | None, str, str, list[str], str, int | None]],
        *,
        saved: bool,
        universe: set[str] | None = None,
    ) -> SectionSet:
        tables = snapshot["tables"]
        by_key = {algo.key(t): t for t in tables}
        budget = retrieve_budget_chars()

        views: list[SectionView] = []
        placed: set[str] = set()
        for position, (id_, name, description, members, origin, version) in enumerate(rows):
            placed.update(members)
            views.append(SectionView(
                id=id_, name=name, description=description, tables=members,
                origin=origin, schema_version=version, position=position,
                chars=algo.section_chars(members, by_key),
                fit=algo.fit(members, by_key, budget),
                missing=[m for m in members if m not in by_key],
            ))

        return SectionSet(
            saved=saved,
            sections=views,
            unassigned=[
                algo.key(t) for t in tables
                if algo.key(t) not in placed
                and (universe is None or algo.key(t) in universe)
            ],
            catalog=[(algo.key(t), table_chars(t)) for t in tables],
            budget_chars=budget,
            snapshot_version=snapshot["version"],
            has_snapshot=bool(tables),
        )

    async def _rows(self, connection_id: UUID) -> list[ConnectionSection]:
        result = await self._db.execute(
            select(ConnectionSection)
            .where(ConnectionSection.connection_id == connection_id)
            .order_by(ConnectionSection.position, ConnectionSection.name)
        )
        return list(result.scalars())

    async def _snapshot(self, connection_id: UUID) -> dict[str, Any]:
        result = await self._db.execute(
            select(SchemaSnapshotRow)
            .where(SchemaSnapshotRow.connection_id == connection_id)
            .order_by(SchemaSnapshotRow.version.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"tables": [], "relationships": [], "dialect": "postgres", "version": 0}
        return {
            "tables": row.tables or [],
            "relationships": row.relationships or [],
            "dialect": row.dialect,
            "version": row.version,
        }
