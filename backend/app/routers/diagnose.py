import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.pg_stat_statements import TOP_QUERIES_QUERY, unavailable_message
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.routers.dashboard import compute_health
from app.schemas import DiagnosisResponse, DiagnosisStep, QueryStat
from app.severity import SEVERITY_RANK
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["diagnose"])

TOP_QUERIES_LIMIT = 5


@router.get("/{target_id}/diagnose", response_model=DiagnosisResponse)
def diagnose(target_id: uuid.UUID):
    """The databank's "Quick Runbook": replication -> locks -> cache hit
    rate -> bloat -> EXPLAIN a specific query -> pg_stat_statements top
    offenders — one button instead of deciding which check to run by hand.
    Replication leads because a broken/lagging standby means failover isn't
    safe, which outranks anything else on this list."""
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            health = compute_health(cur, target_id)

            query_stats_enabled = pg_stat_statements_enabled(cur)
            top_queries: list[QueryStat] = []
            query_stats_message = None
            if query_stats_enabled:
                cur.execute(TOP_QUERIES_QUERY, (TOP_QUERIES_LIMIT,))
                top_queries = [
                    QueryStat(query=row[0], calls=row[1], total_exec_ms=row[2], mean_exec_ms=row[3], rows=row[4])
                    for row in cur.fetchall()
                ]
            else:
                query_stats_message = unavailable_message(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    by_key = {category.key: category for category in health.categories}
    replication, locks, cache, bloat = by_key["replication"], by_key["locks"], by_key["cache"], by_key["bloat"]

    steps = [
        DiagnosisStep(
            step_number=1, label="Replication", severity=replication.severity, value=replication.value, detail=replication.detail
        ),
        DiagnosisStep(step_number=2, label="Locks", severity=locks.severity, value=locks.value, detail=locks.detail),
        DiagnosisStep(step_number=3, label="Cache hit rate", severity=cache.severity, value=cache.value, detail=cache.detail),
        DiagnosisStep(step_number=4, label="Table bloat", severity=bloat.severity, value=bloat.value, detail=bloat.detail),
        DiagnosisStep(
            step_number=5,
            label="EXPLAIN a specific query",
            severity="unknown",
            value="—",
            detail='If one query stands out below, use "Load into Explain" on it in Query Intelligence.',
        ),
        DiagnosisStep(
            step_number=6,
            label="Top offenders",
            severity="unknown" if not query_stats_enabled else "healthy",
            value=str(len(top_queries)) if query_stats_enabled else "off",
            detail=query_stats_message if not query_stats_enabled else "queries ranked by total execution time",
        ),
    ]

    top_finding = max(health.findings, key=lambda finding: SEVERITY_RANK[finding.severity], default=None)

    return DiagnosisResponse(
        overall_severity=health.overall_severity,
        top_finding=top_finding,
        steps=steps,
        top_queries=top_queries,
    )
