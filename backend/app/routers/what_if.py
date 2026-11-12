"""What-if indexes (HypoPG): estimate what a candidate index would do for
the tracked workload without building it. See app/what_if.py.

The whole run is ONE read-only transaction. Hypothetical indexes live in a
backend's memory, so every statement must reach the same backend — a
transaction pins it even behind a transaction-pooling PgBouncer, where
autocommit statements could each land on a different server connection
(the index created on one, the EXPLAINs run on another, and the leftover
index later skewing the scheduler's own plan captures through the pool).
Each statement that may fail runs in its own savepoint, so one unplannable
query doesn't abort the rest. The trade-off: the AccessShareLocks EXPLAIN
takes are held until the run ends (seconds); a short SET LOCAL
lock_timeout bounds any wait, and AccessShareLock blocks no ordinary reads
or writes — only DDL that would queue behind the run for its duration.
"""

import json
import logging
import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.schemas import WhatIfRequest, WhatIfResponse
from app.sql_ident import quote_ident
from app.target_conn import connect_to_target
from app.what_if import (
    MAX_CANDIDATES,
    MAX_COLUMNS,
    MAX_PLANS_RETURNED,
    change_pct,
    cost_explain_sql,
    hypothetical_index_sql,
    plan_total_cost,
    plan_uses_index,
    recommended_index_ddl,
    summarize_candidate,
)
from app.workload_data import load_queries, load_relations, table_filter, target_unreachable, validate_hours
from app.workload_extract import Table

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/targets", tags=["what-if"])

MAX_QUERIES = 50
# Per statement, inside the run's transaction (see the module docstring).
WHAT_IF_LOCK_TIMEOUT = "2s"
WHAT_IF_STATEMENT_TIMEOUT = "15s"

HYPOPG_INSTALLED_SQL = """
    SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'hypopg'
"""
HYPOPG_AVAILABLE_SQL = "SELECT 1 FROM pg_available_extensions WHERE name = 'hypopg'"
TABLE_COLUMNS_SQL = """
    SELECT a.attname
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p', 'm')
      AND a.attnum > 0 AND NOT a.attisdropped
"""

NOT_INSTALLED_MESSAGE = (
    "The HypoPG extension isn't installed on this database. It's what lets PostgreSQL plan with an index "
    "that doesn't exist. {how}"
)


class _HypoPG:
    """HypoPG's functions, qualified with the schema the extension was
    installed into — it needn't be on the role's search_path."""

    def __init__(self, schema: str):
        prefix = f"{quote_ident(schema)}."
        self.create = f"SELECT indexrelid, indexname FROM {prefix}hypopg_create_index(%s)"
        self.size = f"SELECT {prefix}hypopg_relation_size(%s)"
        self.drop = f"SELECT {prefix}hypopg_drop_index(%s)"
        self.reset = f"SELECT {prefix}hypopg_reset()"


def _in_savepoint(conn, cur, sql: str, params=None):
    """Runs one statement inside a savepoint and returns its first row, or
    None if it failed (the transaction carries on). A broken connection
    still raises."""
    try:
        with conn.transaction():
            cur.execute(sql, params)
            return cur.fetchone()
    except psycopg.Error:
        if conn.broken:
            raise
        return None


def _explain(conn, cur, sql: str):
    row = _in_savepoint(conn, cur, sql)
    if row is None:
        return None
    payload = row[0]
    return payload if isinstance(payload, list) else json.loads(payload)


def _validate(request: WhatIfRequest) -> None:
    if not request.candidates:
        raise HTTPException(status_code=422, detail="Add at least one candidate index.")
    if len(request.candidates) > MAX_CANDIDATES:
        raise HTTPException(status_code=422, detail=f"At most {MAX_CANDIDATES} candidates per run.")
    for candidate in request.candidates:
        if not candidate.columns or len(candidate.columns) > MAX_COLUMNS:
            raise HTTPException(status_code=422, detail=f"Each candidate needs 1–{MAX_COLUMNS} columns.")
        if len(set(candidate.columns)) != len(candidate.columns):
            raise HTTPException(status_code=422, detail="A column is listed twice in one candidate.")


@router.post("/{target_id}/what-if", response_model=WhatIfResponse)
def run_what_if(target_id: uuid.UUID, request: WhatIfRequest):
    _validate(request)
    hours = validate_hours(request.hours)
    include = table_filter(target_id)
    for candidate in request.candidates:
        if not include(Table(candidate.schema_name, candidate.table_name)):
            raise HTTPException(
                status_code=404,
                detail=f"{candidate.schema_name}.{candidate.table_name} is outside this connection's schema filter.",
            )

    with store_conn() as conn, conn.cursor() as cur:
        queries = load_queries(cur, target_id, hours)
        relations = load_relations(cur, target_id)
    basis_window = any(q.window_db_time_ms > 0 for q in queries)
    by_id = {q.tracked_id: q for q in queries}
    touching: dict[Table, set] = {}
    for row in relations:
        if row.tracked_id in by_id:
            touching.setdefault(row.table, set()).add(row.tracked_id)

    def weight(q):
        return q.window_db_time_ms if basis_window else q.lifetime_db_time_ms

    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur, conn.transaction():
            cur.execute("SET TRANSACTION READ ONLY")
            # Set here as well as in the session options: behind a PgBouncer
            # that refuses `options`, the session would have no limits.
            cur.execute(f"SET LOCAL lock_timeout = '{WHAT_IF_LOCK_TIMEOUT}'")
            cur.execute(f"SET LOCAL statement_timeout = '{WHAT_IF_STATEMENT_TIMEOUT}'")
            cur.execute(HYPOPG_INSTALLED_SQL)
            installed = cur.fetchone()
            if installed is None:
                cur.execute(HYPOPG_AVAILABLE_SQL)
                available = cur.fetchone() is not None
                how = (
                    "It's packaged on this server: run CREATE EXTENSION hypopg; as a superuser (or a role allowed "
                    "to create extensions) and try again."
                    if available
                    else "It isn't packaged on this server either: install the hypopg package for your PostgreSQL "
                    "version (e.g. postgresql-16-hypopg from apt.postgresql.org), then CREATE EXTENSION hypopg;."
                )
                return WhatIfResponse(
                    hypopg_installed=False,
                    hypopg_available=available,
                    message=NOT_INSTALLED_MESSAGE.format(how=how),
                    queries_considered=0,
                    queries_skipped=0,
                    candidates=[],
                )
            hypopg = _HypoPG(installed[0])

            cur.execute("SHOW server_version_num")
            version = int(cur.fetchone()[0])

            # Queries to cost per candidate: the tracked ones touching its
            # table (from the Workload Map's extraction); every planned
            # tracked query when the extraction knows none.
            wanted: dict[int, set] = {}
            for index, candidate in enumerate(request.candidates):
                table = Table(candidate.schema_name, candidate.table_name)
                wanted[index] = touching.get(table) or {q.tracked_id for q in queries if q.status == "ok"}
            ranked = sorted(set().union(*wanted.values()), key=lambda t: weight(by_id[t]), reverse=True)[:MAX_QUERIES]

            baseline = {}
            skipped = 0
            for tracked_id in ranked:
                sql = cost_explain_sql(by_id[tracked_id].text, version)
                plan = _explain(conn, cur, sql) if sql else None
                if plan is None:
                    skipped += 1
                else:
                    baseline[tracked_id] = plan

            results = []
            try:
                for index, candidate in enumerate(request.candidates):
                    results.append(
                        _cost_candidate(conn, cur, hypopg, candidate, wanted[index], baseline, by_id, weight, version)
                    )
            finally:
                if not conn.broken:
                    _in_savepoint(conn, cur, hypopg.reset)
    except psycopg.Error as exc:
        raise target_unreachable(exc) from exc

    return WhatIfResponse(
        hypopg_installed=True,
        hypopg_available=True,
        message=None,
        queries_considered=len(baseline),
        queries_skipped=skipped,
        candidates=results,
    )


def _cost_candidate(conn, cur, hypopg, candidate, wanted, baseline, by_id, weight, version) -> dict:
    schema, table, columns = candidate.schema_name, candidate.table_name, candidate.columns
    result = {
        "schema_name": schema,
        "table_name": table,
        "columns": columns,
        "ddl": recommended_index_ddl(schema, table, columns),
        "estimated_size_bytes": None,
        "error": None,
        "verdict": "unused",
        "improved": 0,
        "regressed": 0,
        "unchanged": 0,
        "weighted_improvement_pct": 0.0,
        "queries": [],
    }

    cur.execute(TABLE_COLUMNS_SQL, (schema, table))
    existing = {row[0] for row in cur.fetchall()}
    if not existing:
        result["error"] = f"Table {schema}.{table} doesn't exist."
        return result
    missing = [c for c in columns if c not in existing]
    if missing:
        result["error"] = f"No column {', '.join(missing)} on {schema}.{table}."
        return result

    try:
        with conn.transaction():
            cur.execute(hypopg.create, (hypothetical_index_sql(schema, table, columns),))
            index_oid, index_name = cur.fetchone()
    except psycopg.Error as exc:
        if conn.broken:
            raise
        result["error"] = f"HypoPG couldn't create this index: {str(exc).strip().splitlines()[0]}"
        return result

    # From here on the hypothetical index exists: whatever happens, drop it
    # before the next candidate is costed.
    rows = []
    try:
        size_row = _in_savepoint(conn, cur, hypopg.size, (index_oid,))
        result["estimated_size_bytes"] = size_row[0] if size_row else None
        for tracked_id in wanted:
            before = baseline.get(tracked_id)
            if before is None:
                continue
            after = _explain(conn, cur, cost_explain_sql(by_id[tracked_id].text, version))
            if after is None:
                continue
            base_cost, new_cost = plan_total_cost(before), plan_total_cost(after)
            query = by_id[tracked_id]
            rows.append(
                {
                    "queryid": query.queryid,
                    "query": " ".join(query.text.split())[:400],
                    "db_time_ms": round(weight(query), 3),
                    "base_cost": round(base_cost, 2),
                    "new_cost": round(new_cost, 2),
                    "change_pct": change_pct(base_cost, new_cost),
                    "uses_index": plan_uses_index(after, index_name),
                    "_plans": (before, after),
                }
            )
    finally:
        if not conn.broken:
            _in_savepoint(conn, cur, hypopg.drop, (index_oid,))

    rows.sort(key=lambda r: (not r["uses_index"], r["change_pct"]))
    for position, row in enumerate(rows):
        before, after = row.pop("_plans")
        if row["uses_index"] and position < MAX_PLANS_RETURNED:
            row["plan_before"], row["plan_after"] = before, after
    result.update(summarize_candidate(rows))
    result["queries"] = rows
    return result
