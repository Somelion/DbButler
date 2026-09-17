import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.schemas import (
    TrendPoint,
    WaitEventSeries,
    WaitEventSnapshotResponse,
    WaitEventSnapshotRow,
    WaitEventTrendResponse,
)
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["wait-events"])

# One row per currently-active (running, not idle) backend, bucketed into
# Postgres's own wait_event_type categories — NULL means "on CPU, not
# waiting on anything," surfaced as its own 'CPU' bucket rather than left
# out. Idle/idle-in-transaction sessions are excluded on purpose: they're
# not waiting on a resource right now, and the Connections tile already
# covers idle-in-transaction specifically. This is a live snapshot, not a
# time-weighted Active Session History sample — see scheduler.py's
# _collect_wait_events for the periodic version that feeds the trend below.
WAIT_EVENT_SNAPSHOT_QUERY = """
    SELECT COALESCE(wait_event_type, 'CPU') AS category, count(*)
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid() AND state = 'active'
    GROUP BY 1
    ORDER BY 2 DESC
"""


@router.get("/{target_id}/wait-events", response_model=WaitEventSnapshotResponse)
def get_wait_events(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(WAIT_EVENT_SNAPSHOT_QUERY)
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    wait_rows = [WaitEventSnapshotRow(category=category, count=count) for category, count in rows]
    return WaitEventSnapshotResponse(total_active=sum(r.count for r in wait_rows), rows=wait_rows)


@router.get("/{target_id}/wait-events/trend", response_model=WaitEventTrendResponse)
def get_wait_event_trend(target_id: uuid.UUID, hours: int = 24):
    hours = max(1, min(hours, 24 * 90))  # matches the 30-day retention window with headroom
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT labels->>'category' AS category, collected_at, value
            FROM metric_points
            WHERE target_id = %s AND metric_name = 'wait_event_count'
              AND collected_at >= now() - (%s * interval '1 hour')
            ORDER BY category, collected_at
            """,
            (target_id, hours),
        )
        rows = cur.fetchall()

    by_category: dict[str, list[TrendPoint]] = {}
    for category, ts, value in rows:
        by_category.setdefault(category, []).append(TrendPoint(ts=ts, value=value))

    series = [WaitEventSeries(category=category, points=points) for category, points in by_category.items()]
    return WaitEventTrendResponse(hours=hours, series=series)
