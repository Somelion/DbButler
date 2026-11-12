"""Advisor → Refactoring: schema changes suggested by the tracked workload
(app/refactoring_advisor.py). Computed per request from the Workload Map's
data (app/workload_data.py) plus a few catalog reads for the tables the
workload touches — same "recompute, don't persist" pattern as the other
Advisor tabs. Read-only; nothing here runs DDL.
"""

import logging
import uuid

import psycopg
from fastapi import APIRouter, Query

from app.config import settings
from app.db.store import store_conn
from app.refactoring_advisor import (
    ColumnStats,
    IndexInfo,
    RefactorInput,
    TableInfo,
    compute_refactoring_findings,
)
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import IndexFinding, RefactoringAdvisorResponse
from app.timescaledb_health import fetch_chunk_map, is_timescaledb_installed
from app.workload_data import collect_workload, table_filter, validate_hours
from app.workload_extract import Table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/targets", tags=["refactoring-advisor"])

_WANT = "JOIN unnest(%s::text[], %s::text[]) AS want(schema_name, table_name)"

TABLES_SQL = f"""
    SELECT n.nspname, c.relname, c.relkind,
           (SELECT option_value::int FROM pg_options_to_table(c.reloptions) WHERE option_name = 'fillfactor')
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    {_WANT} ON want.schema_name = n.nspname AND want.table_name = c.relname
    WHERE c.relkind IN ('r', 'p')
"""

COLUMNS_SQL = f"""
    SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), t.typcategory
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_type t ON t.oid = a.atttypid
    {_WANT} ON want.schema_name = n.nspname AND want.table_name = c.relname
    WHERE a.attnum > 0 AND NOT a.attisdropped
    ORDER BY n.nspname, c.relname, a.attnum
"""

# Key columns only (ord <= indnkeyatts); an expression column comes back
# as NULL.
INDEXES_SQL = f"""
    SELECT n.nspname, c.relname, i.relname, am.amname, ix.indisunique, ix.indisprimary, ix.indisvalid,
           ix.indpred IS NOT NULL,
           EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = ix.indexrelid AND k.contype IN ('p', 'u', 'x')),
           ARRAY(
               SELECT a.attname
               FROM unnest(ix.indkey::int2[]) WITH ORDINALITY k(attnum, ord)
               LEFT JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum AND k.attnum <> 0
               WHERE k.ord <= ix.indnkeyatts
               ORDER BY k.ord
           ),
           pg_relation_size(ix.indexrelid),
           pg_get_indexdef(ix.indexrelid)
    FROM pg_index ix
    JOIN pg_class i ON i.oid = ix.indexrelid
    JOIN pg_class c ON c.oid = ix.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_am am ON am.oid = i.relam
    {_WANT} ON want.schema_name = n.nspname AND want.table_name = c.relname
"""

# Planner statistics: average width, how common the two most common values
# are, and the number of distinct values. The values themselves are
# deliberately not read — they can be personal data. A partitioned parent
# only has inherited = true rows; a plain table's own (non-inherited) row
# wins when both exist.
COLUMN_STATS_SQL = f"""
    SELECT DISTINCT ON (s.schemaname, s.tablename, s.attname)
           s.schemaname, s.tablename, s.attname, s.avg_width,
           s.most_common_freqs[1], s.most_common_freqs[2], s.n_distinct
    FROM pg_stats s
    {_WANT} ON want.schema_name = s.schemaname AND want.table_name = s.tablename
    ORDER BY s.schemaname, s.tablename, s.attname, s.inherited
"""

INDEX_STATS_AT_SQL = """
    SELECT schema_name, table_name, index_name, idx_scan, collected_at
    FROM index_stat_snapshots
    WHERE target_id = %(target_id)s
      AND collected_at = (
          SELECT {agg}(collected_at) FROM index_stat_snapshots
          WHERE target_id = %(target_id)s AND collected_at >= now() - (%(hours)s * interval '1 hour')
      )
"""


def _safe_rows(cur, sql: str, params) -> list[tuple]:
    """One catalog read that's allowed to fail (missing privileges on
    pg_stats, a TimescaleDB catalog mismatch) without failing the tab: the
    rules that need it simply find nothing."""
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except psycopg.Error as exc:
        if cur.connection.broken:
            raise
        logger.info("refactoring advisor catalog read failed: %s", exc)
        return []


def _target_reads(include_table):
    def read(cur, parent_map, relations, foreign_keys) -> tuple[dict[Table, TableInfo], bool]:
        tables = {r.table for r in relations}
        for fk in foreign_keys:
            tables.update((fk["child"], fk["parent"]))
        tables = sorted(t for t in tables if include_table(t))
        params = ([t.schema for t in tables], [t.name for t in tables])

        timescaledb = False
        hypertables: set[tuple[str, str]] = set()
        try:
            timescaledb = is_timescaledb_installed(cur)
            if timescaledb:
                hypertables = set(fetch_chunk_map(cur).values())
        except psycopg.Error:
            if cur.connection.broken:
                raise

        infos: dict[Table, TableInfo] = {}
        for schema, name, relkind, fillfactor in _safe_rows(cur, TABLES_SQL, params):
            infos[Table(schema, name)] = TableInfo(relkind, fillfactor, (schema, name) in hypertables)
        for schema, name, column, type_name, category in _safe_rows(cur, COLUMNS_SQL, params):
            if Table(schema, name) in infos:
                infos[Table(schema, name)].columns.append((column, type_name, category))
        for row in _safe_rows(cur, INDEXES_SQL, params):
            schema, name, index, method, unique, primary, valid, partial, constraint, keys, size, definition = row
            if Table(schema, name) in infos:
                infos[Table(schema, name)].indexes.append(
                    IndexInfo(index, method, list(keys), unique, primary, valid, partial, constraint, size, definition)
                )
        for schema, name, column, width, top, second, distinct in _safe_rows(cur, COLUMN_STATS_SQL, params):
            if Table(schema, name) in infos:
                infos[Table(schema, name)].column_stats[column] = ColumnStats(width, top, second, distinct)
        return infos, timescaledb

    return read


def _index_scan_deltas(target_id, hours) -> tuple[dict[tuple[Table, str], int], float | None]:
    """idx_scan growth per index between the first and last snapshot cycle
    in the window, and how many hours that actually spans. Empty with fewer
    than two cycles: "no growth" from a single snapshot would read as "never
    used". The rules also refuse a span that's too short to mean anything
    (refactoring_advisor.py::_Context.index_evidence)."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(INDEX_STATS_AT_SQL.format(agg="max"), {"target_id": target_id, "hours": hours})
        latest_rows = cur.fetchall()
        cur.execute(INDEX_STATS_AT_SQL.format(agg="min"), {"target_id": target_id, "hours": hours})
        earliest_rows = cur.fetchall()
    if not latest_rows or not earliest_rows or latest_rows[0][4] == earliest_rows[0][4]:
        return {}, None
    span_hours = (latest_rows[0][4] - earliest_rows[0][4]).total_seconds() / 3600
    earliest = {(Table(s, t), i): scans for s, t, i, scans, _at in earliest_rows}
    deltas = {}
    for schema, table, index, last, _at in latest_rows:
        first = earliest.get((Table(schema, table), index))
        if last is None or first is None:
            continue
        # A counter that went backwards was reset: what's known is since then.
        deltas[(Table(schema, table), index)] = last - first if last >= first else last
    return deltas, span_hours


@router.get("/{target_id}/refactoring-advisor", response_model=RefactoringAdvisorResponse)
def get_refactoring_advisor(target_id: uuid.UUID, hours: int = Query(168)):
    hours = validate_hours(hours)
    context = collect_workload(target_id, hours, target_reads=_target_reads(table_filter(target_id)))
    tables, timescaledb = context.target_extra
    index_deltas, index_window_hours = _index_scan_deltas(target_id, hours)
    findings = compute_refactoring_findings(
        RefactorInput(
            graph=context.graph,
            queries=context.queries,
            relations=context.relations,
            joins=context.joins,
            table_stats=context.table_stats,
            tables=tables,
            index_scan_deltas=index_deltas,
            index_window_hours=index_window_hours,
            partition_min_bytes=settings.refactor_partition_min_table_mb * 1024 * 1024,
            timescaledb_installed=timescaledb,
            include_table=context.include_table,
        )
    )
    archived = get_archived_finding_ids(target_id)
    return RefactoringAdvisorResponse(
        findings=[IndexFinding(**f) for f in findings if f["id"] not in archived],
        meta=context.graph["meta"],
    )
