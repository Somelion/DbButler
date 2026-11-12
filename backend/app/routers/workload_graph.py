"""Workload Map: how the target's tables are really connected — joined in
the tracked queries' plans (scheduler.py::_capture_query_plans ->
app/workload_extract.py), compared against the declared foreign keys.

Read-only: store reads plus catalog SELECTs on one autocommit target
connection (app/workload_data.py). The graph itself is assembled by
app/workload_graph.py.
"""

import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException, Query

from app.db.store import store_conn
from app.schemas import WorkloadGraphResponse, WorkloadTableDetailResponse
from app.target_conn import connect_to_target
from app.timescaledb_health import fetch_chunk_map, is_timescaledb_installed
from app.workload_catalog import fetch_parent_map
from app.workload_data import (
    OPEN_TABLE_FINDINGS_SQL,
    collect_workload,
    load_queries,
    load_relations,
    table_filter,
    target_unreachable,
    validate_hours,
)
from app.workload_extract import Table

router = APIRouter(prefix="/api/targets", tags=["workload-map"])

TABLE_DETAIL_QUERY_LIMIT = 15

# Indexes on one logical table, with size and scans summed over the
# matching per-partition indexes (partitioned indexes are linked to their
# children through pg_inherits). TimescaleDB chunk indexes aren't linked
# that way, so a hypertable's usage only counts the hypertable's own index.
TABLE_INDEXES_SQL = """
    WITH RECURSIVE tree AS (
        SELECT ix.indexrelid AS root, ix.indexrelid AS idx
        FROM pg_index ix
        JOIN pg_class c ON c.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
        UNION ALL
        SELECT t.root, i.inhrelid FROM tree t JOIN pg_inherits i ON i.inhparent = t.idx
    )
    SELECT r.relname, pg_get_indexdef(t.root), ix.indisprimary, ix.indisunique,
           sum(pg_relation_size(t.idx))::bigint, sum(coalesce(s.idx_scan, 0))::bigint
    FROM tree t
    JOIN pg_class r ON r.oid = t.root
    JOIN pg_index ix ON ix.indexrelid = t.root
    LEFT JOIN pg_stat_all_indexes s ON s.indexrelid = t.idx
    GROUP BY r.relname, t.root, ix.indisprimary, ix.indisunique
    ORDER BY 6 DESC, 1
"""


@router.get("/{target_id}/workload-graph", response_model=WorkloadGraphResponse)
def get_workload_graph(target_id: uuid.UUID, hours: int = Query(168), until: datetime | None = Query(None)):
    """The map: tables as nodes (sized by bytes, coloured by open findings),
    join edges weighted by the DB time of the queries behind them, and FKs
    nobody joins on. meta.coverage_pct says how much of the tracked DB time
    the map could actually see. `until` ends the window in the past (the
    time slider)."""
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return collect_workload(target_id, hours, until=until).graph


@router.get(
    "/{target_id}/workload-graph/tables/{schema_name}/{table_name}",
    response_model=WorkloadTableDetailResponse,
)
def get_workload_table_detail(
    target_id: uuid.UUID,
    schema_name: str,
    table_name: str,
    hours: int = Query(168),
    until: datetime | None = Query(None),
):
    """The map's side panel for one table: the tracked queries that touch
    it (and how), its indexes with usage, and its open findings. `until`
    matches the map's time slider, so a past map gets that window's
    queries; indexes and findings are always current."""
    hours = validate_hours(hours)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    table = Table(schema_name, table_name)
    if not table_filter(target_id)(table):
        raise HTTPException(status_code=404, detail="Table is outside this connection's schema filter.")

    with store_conn() as conn, conn.cursor() as cur:
        queries = {q.tracked_id: q for q in load_queries(cur, target_id, hours, until)}
        accesses = load_relations(cur, target_id, " AND a.schema_name = %s AND a.table_name = %s", (schema_name, table_name))
        cur.execute(OPEN_TABLE_FINDINGS_SQL, (target_id,))
        findings = cur.fetchall()

    has_window_time = any(q.window_db_time_ms > 0 for q in queries.values())
    basis = "window" if has_window_time or until is not None else "lifetime"
    query_rows = []
    for access in accesses:
        query = queries.get(access.tracked_id)
        if query is None:
            continue
        db_time = query.window_db_time_ms if basis == "window" else query.lifetime_db_time_ms
        calls = query.window_calls if basis == "window" else query.lifetime_calls
        query_rows.append(
            {
                "queryid": query.queryid,
                "query": " ".join(query.text.split())[:400],
                "db_time_ms": round(db_time, 3),
                "calls": calls,
                "access_kinds": sorted(access.access_kinds),
                "filter_columns": sorted(access.filter_columns),
            }
        )
    query_rows.sort(key=lambda q: q["db_time_ms"], reverse=True)

    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            parent_map = fetch_parent_map(cur)
            hypertables = set()
            try:
                if is_timescaledb_installed(cur):
                    hypertables = set(fetch_chunk_map(cur).values())
            except psycopg.Error:
                # A TimescaleDB catalog mismatch only costs the "partial
                # usage" note, not the whole panel.
                if conn.broken:
                    raise
            cur.execute(TABLE_INDEXES_SQL, (schema_name, table_name))
            index_rows = cur.fetchall()
    except psycopg.Error as exc:
        raise target_unreachable(exc) from exc

    table_findings = []
    for f_schema, f_table, severity, finding_id, title, category in findings:
        physical = Table(f_schema, f_table)
        if parent_map.get(physical, physical) == table:
            table_findings.append({"id": finding_id, "title": title, "severity": severity, "category": category})

    return {
        "schema": schema_name,
        "table": table_name,
        "weight_basis": basis,
        "queries": query_rows[:TABLE_DETAIL_QUERY_LIMIT],
        "query_count": len(query_rows),
        "indexes": [
            {
                "name": name,
                "definition": definition,
                "is_primary": is_primary,
                "is_unique": is_unique,
                "size_bytes": size_bytes,
                "idx_scan": idx_scan,
            }
            for name, definition, is_primary, is_unique, size_bytes, idx_scan in index_rows
        ],
        "index_usage_partial": (schema_name, table_name) in hypertables,
        "findings": table_findings,
    }
