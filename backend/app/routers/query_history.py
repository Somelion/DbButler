import uuid

from fastapi import APIRouter, Query

from app.db.store import store_conn
from app.schemas import (
    QueryHistoryPoint,
    QueryHistoryResponse,
    QueryHistorySeries,
    QueryHistorySummaryItem,
    QueryHistorySummaryResponse,
)

router = APIRouter(prefix="/api/targets", tags=["query-history"])


def _truncate(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@router.get("/{target_id}/query-history/summary", response_model=QueryHistorySummaryResponse)
def get_query_history_summary(target_id: uuid.UUID):
    """The latest known state per tracked query only — cheap regardless of
    how much snapshot history exists, meant for populating the picker. Full
    history for whichever queries get selected comes from get_query_history
    below, mirroring Table Trends' summary/detail split (routers/metrics.py)."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT queryid, query_text, last_calls, last_total_exec_ms, last_seen_at
            FROM tracked_queries
            WHERE target_id = %s
            ORDER BY last_total_exec_ms DESC
            """,
            (target_id,),
        )
        rows = cur.fetchall()

    queries = [
        QueryHistorySummaryItem(
            queryid=queryid,
            query=_truncate(query_text),
            calls=last_calls,
            mean_exec_ms=(last_total_exec_ms / last_calls) if last_calls else 0.0,
            last_seen_at=last_seen_at,
        )
        for queryid, query_text, last_calls, last_total_exec_ms, last_seen_at in rows
    ]
    return QueryHistorySummaryResponse(queries=queries)


@router.get("/{target_id}/query-history", response_model=QueryHistoryResponse)
def get_query_history(target_id: uuid.UUID, hours: int = 24, queryids: list[int] = Query(default=[])):
    """Full snapshot history, but only for explicitly requested queries —
    same reasoning as Table Trends: with up to 50 tracked queries and a
    snapshot roughly every 5 minutes, returning every tracked query's history
    just to render a picker doesn't scale, and isn't needed since the
    summary above already covers the picker."""
    hours = max(1, min(hours, 24 * 90))

    if not queryids:
        return QueryHistoryResponse(hours=hours, queries=[])

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT tq.queryid, tq.query_text, s.collected_at, s.calls, s.mean_exec_ms
            FROM query_stat_snapshots s
            JOIN tracked_queries tq ON tq.id = s.tracked_query_id
            WHERE tq.target_id = %s AND tq.queryid = ANY(%s)
              AND s.collected_at >= now() - (%s * interval '1 hour')
            ORDER BY tq.queryid, s.collected_at
            """,
            (target_id, queryids, hours),
        )
        rows = cur.fetchall()

    result: dict[int, QueryHistorySeries] = {}
    for queryid, query_text, ts, calls, mean_exec_ms in rows:
        if queryid not in result:
            result[queryid] = QueryHistorySeries(queryid=queryid, query=_truncate(query_text), points=[])
        result[queryid].points.append(QueryHistoryPoint(ts=ts, calls=calls, mean_exec_ms=mean_exec_ms))

    return QueryHistoryResponse(hours=hours, queries=list(result.values()))
