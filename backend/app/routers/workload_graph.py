"""Workload Map: how the target's tables are really connected — joined in
the tracked queries' plans (scheduler.py::_capture_query_plans ->
app/workload_extract.py), compared against the declared foreign keys.

Read-only: store reads plus catalog SELECTs on one autocommit target
connection. The graph itself is assembled by app/workload_graph.py.
"""

import uuid

import psycopg
from fastapi import APIRouter, HTTPException, Query

from app.db.store import store_conn
from app.schema_filter import get_allowed_schemas
from app.schemas import WorkloadGraphResponse, WorkloadTableDetailResponse
from app.target_conn import connect_to_target
from app.timescaledb_health import fetch_chunk_map, is_timescaledb_installed
from app.workload_catalog import (
    fetch_foreign_keys,
    fetch_index_leading_columns,
    fetch_parent_map,
    is_user_schema,
)
from app.workload_extract import Table
from app.workload_graph import (
    JoinRow,
    QueryInfo,
    RelationRow,
    StatRow,
    build_workload_graph,
    fold_table_stats,
    worst_severity,
)

router = APIRouter(prefix="/api/targets", tags=["workload-map"])

MAX_HOURS = 24 * 30  # the store's retention window
TABLE_DETAIL_QUERY_LIMIT = 15

TRACKED_QUERIES_SQL = """
    SELECT
        tq.id, tq.queryid, tq.query_text,
        coalesce(w.db_time_ms, 0), coalesce(w.calls, 0),
        tq.last_total_exec_ms, tq.last_calls,
        e.status, e.checked_at
    FROM tracked_queries tq
    LEFT JOIN (
        SELECT s.tracked_query_id, sum(s.calls * s.mean_exec_ms) AS db_time_ms, sum(s.calls)::bigint AS calls
        FROM query_stat_snapshots s
        JOIN tracked_queries t ON t.id = s.tracked_query_id
        WHERE t.target_id = %(target_id)s
          AND s.collected_at >= now() - (%(hours)s * interval '1 hour')
        GROUP BY s.tracked_query_id
    ) w ON w.tracked_query_id = tq.id
    LEFT JOIN query_workload_extracts e ON e.tracked_query_id = tq.id
    WHERE tq.target_id = %(target_id)s
      AND tq.last_seen_at >= now() - (%(hours)s * interval '1 hour')
"""

RELATIONS_SQL = """
    SELECT a.tracked_query_id, a.schema_name, a.table_name, a.access_kinds, a.filter_columns
    FROM query_relation_access a
    JOIN tracked_queries tq ON tq.id = a.tracked_query_id
    WHERE tq.target_id = %s
"""

JOINS_SQL = """
    SELECT j.tracked_query_id, j.left_schema, j.left_table, j.right_schema, j.right_table,
           j.left_columns, j.right_columns, j.join_type, j.join_strategy
    FROM query_join_edges j
    JOIN tracked_queries tq ON tq.id = j.tracked_query_id
    WHERE tq.target_id = %s
"""

# Every row of one table-metrics cycle shares one collected_at, so "the
# first and last cycle in the window" are two index lookups, not a sort
# over every snapshot in a 30-day window.
TABLE_STATS_AT_SQL = """
    SELECT schema_name, table_name, collected_at, total_bytes, n_live_tup, n_dead_tup,
           seq_scan, seq_tup_read, idx_scan, n_tup_ins, n_tup_upd, n_tup_del, n_tup_hot_upd
    FROM table_stat_snapshots
    WHERE target_id = %(target_id)s
      AND collected_at = (
          SELECT {agg}(collected_at) FROM table_stat_snapshots
          WHERE target_id = %(target_id)s AND collected_at >= now() - (%(hours)s * interval '1 hour')
      )
"""

OPEN_TABLE_FINDINGS_SQL = """
    SELECT finding->>'schema_name', finding->>'table_name', finding->>'severity',
           finding_id, finding->>'title', category
    FROM deep_scan_findings
    WHERE target_id = %s AND status = 'open' AND finding->>'table_name' IS NOT NULL
"""

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


def _validate_hours(hours: int) -> int:
    return max(1, min(hours, MAX_HOURS))


def _table_filter(target_id: uuid.UUID):
    allowed = get_allowed_schemas(target_id)

    def include(table: Table) -> bool:
        return is_user_schema(table.schema) and (allowed is None or table.schema in allowed)

    return include


def _load_queries(cur, target_id, hours) -> list[QueryInfo]:
    cur.execute(TRACKED_QUERIES_SQL, {"target_id": target_id, "hours": hours})
    return [
        QueryInfo(
            tracked_id=row[0],
            queryid=row[1],
            text=row[2],
            window_db_time_ms=float(row[3] or 0),
            window_calls=int(row[4] or 0),
            lifetime_db_time_ms=float(row[5] or 0),
            lifetime_calls=int(row[6] or 0),
            status=row[7],
            checked_at=row[8],
        )
        for row in cur.fetchall()
    ]


def _load_stats(cur, target_id, hours, agg: str) -> dict[Table, StatRow]:
    cur.execute(TABLE_STATS_AT_SQL.format(agg=agg), {"target_id": target_id, "hours": hours})
    return {Table(row[0], row[1]): StatRow(*row[2:]) for row in cur.fetchall()}


def _load_store_data(target_id, hours):
    with store_conn() as conn, conn.cursor() as cur:
        queries = _load_queries(cur, target_id, hours)
        cur.execute(RELATIONS_SQL, (target_id,))
        relations = [RelationRow(r[0], Table(r[1], r[2]), list(r[3]), list(r[4])) for r in cur.fetchall()]
        cur.execute(JOINS_SQL, (target_id,))
        joins = [
            JoinRow(r[0], Table(r[1], r[2]), Table(r[3], r[4]), list(r[5]), list(r[6]), r[7], r[8])
            for r in cur.fetchall()
        ]
        latest = _load_stats(cur, target_id, hours, "max")
        earliest = _load_stats(cur, target_id, hours, "min")
        cur.execute(OPEN_TABLE_FINDINGS_SQL, (target_id,))
        findings = cur.fetchall()
    return queries, relations, joins, latest, earliest, findings


def _target_unreachable(exc: psycopg.Error) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Could not reach target: {exc}")


@router.get("/{target_id}/workload-graph", response_model=WorkloadGraphResponse)
def get_workload_graph(target_id: uuid.UUID, hours: int = Query(168)):
    """The map: tables as nodes (sized by bytes, coloured by open findings),
    join edges weighted by the DB time of the queries behind them, and FKs
    nobody joins on. meta.coverage_pct says how much of the tracked DB time
    the map could actually see."""
    hours = _validate_hours(hours)
    include_table = _table_filter(target_id)
    queries, relations, joins, latest, earliest, findings = _load_store_data(target_id, hours)

    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            parent_map = fetch_parent_map(cur)
            foreign_keys = fetch_foreign_keys(cur)
            leading = fetch_index_leading_columns(cur)
    except psycopg.Error as exc:
        raise _target_unreachable(exc) from exc

    table_stats, span_hours = fold_table_stats(latest, earliest, parent_map)
    by_table: dict[Table, list[str]] = {}
    for schema, table, severity, *_rest in findings:
        physical = Table(schema, table)
        by_table.setdefault(parent_map.get(physical, physical), []).append(severity)
    severities = {table: worst_severity(values) for table, values in by_table.items()}

    return build_workload_graph(
        hours=hours,
        queries=queries,
        relations=relations,
        joins=joins,
        foreign_keys=foreign_keys,
        index_leading_columns=leading,
        table_stats=table_stats,
        stats_window_hours=span_hours,
        severities=severities,
        include_table=include_table,
    )


@router.get(
    "/{target_id}/workload-graph/tables/{schema_name}/{table_name}",
    response_model=WorkloadTableDetailResponse,
)
def get_workload_table_detail(target_id: uuid.UUID, schema_name: str, table_name: str, hours: int = Query(168)):
    """The map's side panel for one table: the tracked queries that touch
    it (and how), its indexes with usage, and its open findings."""
    hours = _validate_hours(hours)
    table = Table(schema_name, table_name)
    if not _table_filter(target_id)(table):
        raise HTTPException(status_code=404, detail="Table is outside this connection's schema filter.")

    with store_conn() as conn, conn.cursor() as cur:
        queries = {q.tracked_id: q for q in _load_queries(cur, target_id, hours)}
        cur.execute(
            RELATIONS_SQL + " AND a.schema_name = %s AND a.table_name = %s",
            (target_id, schema_name, table_name),
        )
        accesses = cur.fetchall()
        cur.execute(OPEN_TABLE_FINDINGS_SQL, (target_id,))
        findings = cur.fetchall()

    basis = "window" if any(q.window_db_time_ms > 0 for q in queries.values()) else "lifetime"
    query_rows = []
    for tracked_id, _schema, _table, access_kinds, filter_columns in accesses:
        query = queries.get(tracked_id)
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
                "access_kinds": sorted(access_kinds),
                "filter_columns": sorted(filter_columns),
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
        raise _target_unreachable(exc) from exc

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
