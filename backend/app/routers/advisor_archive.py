import uuid

from fastapi import APIRouter
from psycopg.types.json import Jsonb

from app.db.store import store_conn
from app.schemas import ArchivedFindingOut, ArchivedFindingsResponse, IndexFinding

router = APIRouter(prefix="/api/targets", tags=["advisor-archive"])

_COLUMNS = "id, target_id, finding_id, category, finding, archived_at"


def _row_to_archived(row) -> ArchivedFindingOut:
    return ArchivedFindingOut(
        id=row[0],
        target_id=row[1],
        finding_id=row[2],
        category=row[3],
        finding=IndexFinding(**row[4]),
        archived_at=row[5],
    )


def get_archived_finding_ids(target_id: uuid.UUID) -> set[str]:
    """Shared by index_advisor.py and schema_lint.py to keep an archived
    finding out of live results, even though both checks recompute their
    findings from scratch on every request."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT finding_id FROM archived_findings WHERE target_id = %s", (target_id,))
        return {row[0] for row in cur.fetchall()}


@router.get("/{target_id}/advisor/archive", response_model=ArchivedFindingsResponse)
def list_archived_findings(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM archived_findings WHERE target_id = %s ORDER BY archived_at DESC",
            (target_id,),
        )
        return ArchivedFindingsResponse(findings=[_row_to_archived(row) for row in cur.fetchall()])


@router.post("/{target_id}/advisor/archive", response_model=ArchivedFindingOut, status_code=201)
def archive_finding(target_id: uuid.UUID, payload: IndexFinding):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO archived_findings (target_id, finding_id, category, finding)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (target_id, finding_id) DO UPDATE
                SET finding = EXCLUDED.finding, archived_at = now()
            RETURNING {_COLUMNS}
            """,
            (target_id, payload.id, payload.category, Jsonb(payload.model_dump())),
        )
        row = cur.fetchone()
        conn.commit()
        return _row_to_archived(row)


@router.delete("/{target_id}/advisor/archive/{finding_id}", status_code=204)
def restore_finding(target_id: uuid.UUID, finding_id: str):
    """Un-archives a finding. It reappears in Index Advisor/Schema Lint the
    next time either runs, if the underlying issue is still there."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM archived_findings WHERE target_id = %s AND finding_id = %s",
            (target_id, finding_id),
        )
        conn.commit()
