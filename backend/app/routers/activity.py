import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.schemas import ActivityResponse, ActivitySession
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["activity"])

# pg_blocking_pids() is the built-in, reliable way to ask "who is blocking me" —
# no need to hand-roll the pg_locks self-join.
ACTIVITY_QUERY = """
    SELECT
        pid,
        usename,
        application_name,
        state,
        wait_event_type,
        wait_event,
        query,
        EXTRACT(EPOCH FROM (now() - query_start))::bigint AS query_duration_seconds,
        EXTRACT(EPOCH FROM (now() - state_change))::bigint AS state_duration_seconds,
        pg_blocking_pids(pid) AS blocked_by_pids
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
    ORDER BY query_start NULLS LAST
"""

# One row per pid currently waiting on an ungranted lock, describing what
# it's actually waiting for (mode + resource) — DISTINCT ON (l.pid) since a
# backend only ever waits on one lock acquisition at a time, so a pid can't
# have more than one genuinely "current" ungranted request.
LOCK_WAIT_QUERY = """
    SELECT DISTINCT ON (l.pid)
        l.pid,
        l.mode,
        CASE
            WHEN l.locktype = 'relation' THEN COALESCE(n.nspname || '.' || c.relname, 'a relation')
            WHEN l.locktype = 'tuple' THEN COALESCE(n.nspname || '.' || c.relname, 'a table') || ' row'
            WHEN l.locktype = 'transactionid' THEN 'transaction ' || l.transactionid::text
            ELSE l.locktype
        END AS waiting_on
    FROM pg_locks l
    LEFT JOIN pg_class c ON c.oid = l.relation
    LEFT JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT l.granted
    ORDER BY l.pid
"""


@router.get("/{target_id}/activity", response_model=ActivityResponse)
def get_activity(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(ACTIVITY_QUERY)
            rows = cur.fetchall()
            cur.execute(LOCK_WAIT_QUERY)
            waiting_lock_map = {pid: f"{mode} on {waiting_on}" for pid, mode, waiting_on in cur.fetchall()}
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    blocked_by_map = {row[0]: list(row[9] or []) for row in rows}
    blocking_map: dict[int, list[int]] = {}
    for pid, blocked_by in blocked_by_map.items():
        for blocker_pid in blocked_by:
            blocking_map.setdefault(blocker_pid, []).append(pid)

    sessions = [
        ActivitySession(
            pid=row[0],
            usename=row[1],
            application_name=row[2],
            state=row[3],
            wait_event_type=row[4],
            wait_event=row[5],
            query=row[6],
            query_duration_seconds=row[7],
            state_duration_seconds=row[8],
            blocked_by_pids=blocked_by_map.get(row[0], []),
            blocking_pids=blocking_map.get(row[0], []),
            waiting_lock=waiting_lock_map.get(row[0]),
        )
        for row in rows
    ]
    return ActivityResponse(sessions=sessions)


@router.post("/{target_id}/activity/{pid}/cancel")
def cancel_query(target_id: uuid.UUID, pid: int):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_cancel_backend(%s)", (pid,))
            ok = cur.fetchone()[0]
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc
    return {"ok": bool(ok)}


@router.post("/{target_id}/activity/{pid}/terminate")
def terminate_session(target_id: uuid.UUID, pid: int):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
            ok = cur.fetchone()[0]
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc
    return {"ok": bool(ok)}
