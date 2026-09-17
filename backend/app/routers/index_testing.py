import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql

from app.db.store import store_conn
from app.schemas import IndexTestingResponse, MaintenanceResult, TrackedIndexOut
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["index-testing"])

# Below this many hours since creation with zero scans, a "not used yet"
# verdict would be premature — freshly added indexes just haven't had a
# realistic window of traffic yet.
TOO_EARLY_HOURS = 24

_TRACKED_COLUMNS = "id, schema_name, table_name, index_name, ddl, baseline_seq_scan, created_at"


def _verdict(scans_since_added: int, created_at: datetime) -> str:
    if scans_since_added > 0:
        return "being_used"
    hours_elapsed = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
    return "too_early" if hours_elapsed < TOO_EARLY_HOURS else "not_used_yet"


@router.get("/{target_id}/index-testing", response_model=IndexTestingResponse)
def get_index_testing(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_TRACKED_COLUMNS} FROM tracked_indexes "
            "WHERE target_id = %s AND dropped_at IS NULL ORDER BY created_at DESC",
            (target_id,),
        )
        tracked_rows = cur.fetchall()

    if not tracked_rows:
        return IndexTestingResponse(indexes=[])

    results: list[TrackedIndexOut] = []
    gone: list[uuid.UUID] = []

    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            for tracked_id, schema_name, table_name, index_name, ddl, baseline_seq_scan, created_at in tracked_rows:
                cur.execute(
                    """
                    SELECT idx.idx_scan, pg_relation_size(idx.indexrelid), s.seq_scan
                    FROM pg_stat_user_indexes idx
                    JOIN pg_stat_user_tables s ON s.relid = idx.relid
                    WHERE idx.schemaname = %s AND idx.indexrelname = %s
                    """,
                    (schema_name, index_name),
                )
                live = cur.fetchone()
                if live is None:
                    # Dropped outside the app (psql, another tool) — reconcile
                    # below rather than showing a row for an index that's gone.
                    gone.append(tracked_id)
                    continue

                idx_scan, index_bytes, seq_scan_now = live
                seq_scans_since_added = max(0, seq_scan_now - baseline_seq_scan)
                results.append(
                    TrackedIndexOut(
                        id=tracked_id,
                        schema_name=schema_name,
                        table_name=table_name,
                        index_name=index_name,
                        ddl=ddl,
                        created_at=created_at,
                        scans_since_added=idx_scan,
                        index_bytes=index_bytes,
                        seq_scans_since_added=seq_scans_since_added,
                        verdict=_verdict(idx_scan, created_at),
                    )
                )
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    if gone:
        with store_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tracked_indexes SET dropped_at = now() WHERE id = ANY(%s)",
                (gone,),
            )
            conn.commit()

    return IndexTestingResponse(indexes=results)


@router.post("/{target_id}/index-testing/{tracked_id}/drop", response_model=MaintenanceResult)
def drop_tracked_index(target_id: uuid.UUID, tracked_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT schema_name, index_name FROM tracked_indexes "
            "WHERE id = %s AND target_id = %s AND dropped_at IS NULL",
            (tracked_id, target_id),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Tracked index not found (or already dropped).")
    schema_name, index_name = row

    stmt = sql.SQL("DROP INDEX CONCURRENTLY {}").format(sql.Identifier(schema_name, index_name))
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(stmt)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tracked_indexes SET dropped_at = now() WHERE id = %s", (tracked_id,))
        conn.commit()

    return MaintenanceResult(ok=True, message=f"Dropped {schema_name}.{index_name}.")
