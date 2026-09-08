"""The access review: two lenses over the same five facts, and a CSV.

`GET /access-review?principal_id=…` answers *"what can Ali reach?"*
`GET /access-review?resource_type=…&resource_id=…` answers *"who can reach
this?"*

Gated on `access.review`, which is a **read** capability an Auditor holds and a
Connection Owner does not: this is a screen about the people in an
installation, and being able to share your own dashboard is not a reason to be
able to enumerate everybody's access.

Three things about the shape:

* **One route, two lenses**, because they return the same row type and the
  screen is one screen with a switch. Two endpoints would have meant two
  serialisers and, eventually, two answers.
* **No pagination.** An access review is a whole answer or it is misleading —
  *"these are the twenty rows on page one"* is not something an auditor can
  sign off on. `AccessReviewService._all`'s docstring carries the reasoning and
  the condition under which that changes.
* **CSV is `Accept: text/csv`, not a second path.** Same query, same rows, same
  gate; a `/access-review.csv` would be a second route to keep in step, and the
  content type is what actually differs.
"""
from __future__ import annotations

import csv
import io
import re
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import AccessReviewDep, DbDep
from app.api.schemas import ReachRead
from app.core.errors import ValidationError
from app.domain.value_objects.authz import ResourceType
from app.services.access_review_service import AccessReviewService, ReachRow

router = APIRouter(prefix="/access-review", tags=["access-review"])

#: Text a spreadsheet would run rather than show. The same defusing
#: `frontend/src/components/table-format.ts` applies to a downloaded result,
#: and it is here for the same reason: a display name and a resource name are
#: both text somebody else typed, and Excel treats a leading `=`, `+`, `@` or
#: tab as the start of a formula. A leading apostrophe is the standard
#: defusing and survives the round trip as a visible character — the value is
#: shown, not run.
#:
#: `-` is deliberately absent, exactly as it is there: the only thing it would
#: catch is a name beginning with a minus, which is data far more often than
#: it is an attack.
_FORMULA = re.compile(r"^[=+@\t\r]")

#: The columns, in the order a reader wants them: who, what, how much, and
#: through what. `via` last because it is empty for three of the five paths.
_COLUMNS = (
    "principal", "principal_kind", "resource_type", "resource",
    "privilege", "path", "via",
)


@router.get("", response_model=list[ReachRead])
async def review(
    ctx: AccessReviewDep,
    db: DbDep,
    principal_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    privilege: str | None = None,
    accept: str = Query(default="json", pattern="^(json|csv)$", alias="format"),
) -> Response | list[ReachRead]:
    """One lens or the other. Exactly one of the two must be named.

    Refused rather than defaulted when both or neither arrive: *"everything
    everybody can reach"* is a report this could produce and nobody asked for,
    and silently picking one of the two lenses would make a mistyped query
    answer a different question than the one on screen.
    """
    service = AccessReviewService(db)

    if principal_id is not None and resource_id is not None:
        raise ValidationError(
            "Ask about a principal or about a resource, not both — they are "
            "two lenses on the same rows and the screen shows one at a time."
        )
    if principal_id is not None:
        rows = await service.by_principal(principal_id)
    elif resource_id is not None:
        if resource_type is None:
            raise ValidationError(
                "A resource needs its type as well as its id: the two derived "
                "types carry their connection's id, so an id alone names more "
                "than one thing."
            )
        rows = await service.by_resource(_type(resource_type), resource_id)
    else:
        raise ValidationError(
            "Name a principal or a resource. Reviewing everything at once is "
            "a report, not a review."
        )

    if resource_type is not None:
        rows = [row for row in rows if row.resource_type == resource_type]
    if privilege is not None:
        rows = [row for row in rows if row.privilege == privilege]
    rows = sorted(
        rows, key=lambda r: (r.resource_type, r.resource_name, r.principal_name)
    )

    if accept == "csv":
        return Response(content=_csv(rows), media_type="text/csv; charset=utf-8")
    return [_read(row) for row in rows]


def _type(value: str) -> ResourceType:
    try:
        return ResourceType(value)
    except ValueError as exc:
        raise ValidationError(f"“{value}” is not a resource type.") from exc


def _read(row: ReachRow) -> ReachRead:
    return ReachRead(
        principal_id=row.principal_id,
        principal_name=row.principal_name,
        principal_kind=row.principal_kind,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_name=row.resource_name,
        privilege=row.privilege,
        path=row.path,
        via=row.via,
    )


def _cell(value: str) -> str:
    """One value, defused. Quoting itself is `csv.writer`'s job."""
    return f"'{value}" if _FORMULA.match(value) else value


def _csv(rows: list[ReachRow]) -> str:
    """The same rows as a file. RFC 4180, with CRLF and a header.

    `csv.writer` with `\\r\\n` is RFC 4180's line ending and what Excel is
    happiest opening — the same choice `toCsv` makes on the frontend, so a
    review exported from the server and a table exported from the browser open
    the same way.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_COLUMNS)
    for row in rows:
        writer.writerow([
            _cell(row.principal_name),
            row.principal_kind,
            row.resource_type,
            _cell(row.resource_name),
            row.privilege,
            row.path,
            _cell(row.via),
        ])
    return buffer.getvalue()
