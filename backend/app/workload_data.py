"""Loads everything the workload features work from — the store's per-query
extractions, DB time, table statistics and open findings, plus the
target's partition parents, foreign keys and index leading columns — and
assembles the Workload Map graph from it. Shared by routers/workload_graph.py
(the map) and routers/refactoring_advisor.py (rules over the same data).

Read-only: store reads plus catalog SELECTs on one autocommit target
connection, which callers can extend with their own reads (`target_reads`)
so a request never opens two connections.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from fastapi import HTTPException

from app.db.store import store_conn
from app.schema_filter import get_allowed_schemas
from app.target_conn import connect_to_target
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

MAX_HOURS = 24 * 30  # the store's retention window

TRACKED_QUERIES_SQL = """
    SELECT
        tq.id, tq.queryid, tq.query_text,
        coalesce(w.db_time_ms, 0), coalesce(w.calls, 0),
        tq.last_total_exec_ms, tq.last_calls,
        e.status, e.checked_at, e.extractor_version
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
    SELECT a.tracked_query_id, a.schema_name, a.table_name, a.access_kinds, a.filter_columns,
           a.referenced_columns
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


@dataclass
class WorkloadContext:
    hours: int
    include_table: Callable[[Table], bool]
    queries: list[QueryInfo]
    relations: list[RelationRow]
    joins: list[JoinRow]
    parent_map: dict[Table, Table]
    foreign_keys: list[dict]
    index_leading_columns: set[tuple[Table, str]]
    table_stats: dict[Table, dict]
    stats_window_hours: float | None
    severities: dict[Table, str]
    findings: list[tuple]
    graph: dict
    target_extra: object = None


def validate_hours(hours: int) -> int:
    return max(1, min(hours, MAX_HOURS))


def table_filter(target_id: uuid.UUID) -> Callable[[Table], bool]:
    allowed = get_allowed_schemas(target_id)

    def include(table: Table) -> bool:
        return is_user_schema(table.schema) and (allowed is None or table.schema in allowed)

    return include


def load_queries(cur, target_id, hours) -> list[QueryInfo]:
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
            extractor_version=row[9],
        )
        for row in cur.fetchall()
    ]


def load_relations(cur, target_id, extra_where: str = "", params: tuple = ()) -> list[RelationRow]:
    cur.execute(RELATIONS_SQL + extra_where, (target_id, *params))
    return [
        RelationRow(r[0], Table(r[1], r[2]), list(r[3]), list(r[4]), list(r[5] or [])) for r in cur.fetchall()
    ]


def _load_stats(cur, target_id, hours, agg: str) -> dict[Table, StatRow]:
    cur.execute(TABLE_STATS_AT_SQL.format(agg=agg), {"target_id": target_id, "hours": hours})
    return {Table(row[0], row[1]): StatRow(*row[2:]) for row in cur.fetchall()}


def target_unreachable(exc: psycopg.Error) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Could not reach target: {exc}")


def collect_workload(target_id: uuid.UUID, hours: int, target_reads: Callable | None = None) -> WorkloadContext:
    """Everything the map needs, and the map itself. target_reads(cur,
    parent_map, relations, foreign_keys) runs on the same autocommit target
    connection afterwards; its return value lands in
    WorkloadContext.target_extra."""
    hours = validate_hours(hours)
    include_table = table_filter(target_id)

    with store_conn() as conn, conn.cursor() as cur:
        queries = load_queries(cur, target_id, hours)
        relations = load_relations(cur, target_id)
        cur.execute(JOINS_SQL, (target_id,))
        joins = [
            JoinRow(r[0], Table(r[1], r[2]), Table(r[3], r[4]), list(r[5]), list(r[6]), r[7], r[8])
            for r in cur.fetchall()
        ]
        latest = _load_stats(cur, target_id, hours, "max")
        earliest = _load_stats(cur, target_id, hours, "min")
        cur.execute(OPEN_TABLE_FINDINGS_SQL, (target_id,))
        findings = cur.fetchall()

    target_extra = None
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            parent_map = fetch_parent_map(cur)
            foreign_keys = fetch_foreign_keys(cur)
            leading = fetch_index_leading_columns(cur)
            if target_reads is not None:
                target_extra = target_reads(cur, parent_map, relations, foreign_keys)
    except psycopg.Error as exc:
        raise target_unreachable(exc) from exc

    table_stats, span_hours = fold_table_stats(latest, earliest, parent_map)
    by_table: dict[Table, list[str]] = {}
    for schema, table, severity, *_rest in findings:
        physical = Table(schema, table)
        by_table.setdefault(parent_map.get(physical, physical), []).append(severity)
    severities = {table: worst_severity(values) for table, values in by_table.items()}

    graph = build_workload_graph(
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
    return WorkloadContext(
        hours=hours,
        include_table=include_table,
        queries=queries,
        relations=relations,
        joins=joins,
        parent_map=parent_map,
        foreign_keys=foreign_keys,
        index_leading_columns=leading,
        table_stats=table_stats,
        stats_window_hours=span_hours,
        severities=severities,
        findings=findings,
        graph=graph,
        target_extra=target_extra,
    )
