import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.pg_stat_statements import TOP_QUERIES_QUERY, is_enabled, unavailable_message
from app.schemas import QueryStat, QueryStatsResponse
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["query-stats"])


@router.get("/{target_id}/query-stats", response_model=QueryStatsResponse)
def get_query_stats(target_id: uuid.UUID, limit: int = 20):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            if not is_enabled(cur):
                return QueryStatsResponse(enabled=False, queries=[], message=unavailable_message(cur))

            cur.execute(TOP_QUERIES_QUERY, (limit,))
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    queries = [
        QueryStat(query=row[0], calls=row[1], total_exec_ms=row[2], mean_exec_ms=row[3], rows=row[4])
        for row in rows
    ]
    return QueryStatsResponse(enabled=True, queries=queries)
