import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, BackgroundTasks, HTTPException
from psycopg import sql

from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.pg_stat_statements import unavailable_message
from app.schema_filter import get_allowed_schemas
from app.schemas import (
    MaintenanceResult,
    SchemaMaintenanceJob,
    SchemaMaintenanceRequest,
    TableMaintenanceRequest,
    VacuumRequest,
)
from app.target_conn import MAINTENANCE_STATEMENT_TIMEOUT_MS, connect_to_target

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/targets", tags=["maintenance"])

# These run real commands against the target — VACUUM/ANALYZE/stat-reset,
# never DROP/ALTER/TRUNCATE. Table/schema names arrive as identifiers, which
# can't be bound as query parameters, so every statement is built with
# psycopg.sql.Identifier rather than string interpolation. VACUUM/ANALYZE
# get the long maintenance statement timeout but keep the default lock
# timeout: if something already holds a conflicting lock on the table, the
# user gets a quick "lock timeout" error instead of a request that silently
# queues (and makes everything after it queue too).
#
# A pg_monitor-only role (what the Connection screen recommends) can read
# everything but can't VACUUM/ANALYZE a table it doesn't own, or reset
# pg_stat_statements — these actions need real privileges: table ownership,
# or (PostgreSQL 17+) membership in the built-in pg_maintain role.


@router.post("/{target_id}/maintenance/analyze", response_model=MaintenanceResult)
def analyze_table(target_id: uuid.UUID, payload: TableMaintenanceRequest):
    stmt = sql.SQL("ANALYZE {}").format(sql.Identifier(payload.schema_name, payload.table_name))
    try:
        with connect_to_target(
            target_id, autocommit=True, statement_timeout_ms=MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn, conn.cursor() as cur:
            cur.execute(stmt)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MaintenanceResult(ok=True, message=f"Analyzed {payload.schema_name}.{payload.table_name}.")


@router.post("/{target_id}/maintenance/vacuum", response_model=MaintenanceResult)
def vacuum_table(target_id: uuid.UUID, payload: VacuumRequest):
    keyword = "VACUUM ANALYZE {}" if payload.analyze else "VACUUM {}"
    stmt = sql.SQL(keyword).format(sql.Identifier(payload.schema_name, payload.table_name))
    try:
        with connect_to_target(
            target_id, autocommit=True, statement_timeout_ms=MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn, conn.cursor() as cur:
            cur.execute(stmt)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    verb = "Vacuumed and analyzed" if payload.analyze else "Vacuumed"
    return MaintenanceResult(ok=True, message=f"{verb} {payload.schema_name}.{payload.table_name}.")


@router.post("/{target_id}/maintenance/reset-table-stats", response_model=MaintenanceResult)
def reset_table_stats(target_id: uuid.UUID, payload: TableMaintenanceRequest):
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = %s",
                (payload.schema_name, payload.table_name),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Table not found on target")
            cur.execute("SELECT pg_stat_reset_single_table_counters(%s)", (row[0],))
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MaintenanceResult(
        ok=True, message=f"Reset statistics for {payload.schema_name}.{payload.table_name}."
    )


@router.post("/{target_id}/maintenance/reset-query-stats", response_model=MaintenanceResult)
def reset_query_stats(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            # Also puts the extension's schema on the search_path when it
            # was installed somewhere else (app/pg_stat_statements.py).
            if not pg_stat_statements_enabled(cur):
                raise HTTPException(status_code=409, detail=unavailable_message(cur))
            cur.execute("SELECT pg_stat_statements_reset()")
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MaintenanceResult(ok=True, message="Query statistics reset.")


# --- Analyze / vacuum all tables of a schema (Table Health) ------------------
#
# Background jobs, polled by the screen: a big schema takes longer than
# nginx's 60s proxy timeout. One statement per table on one autocommit
# connection (VACUUM can't run in a transaction block anyway) — each table
# is done as soon as its statement returns, a failure (lock timeout, a
# dropped table) is recorded and the next table goes on.
#
#   analyze  ANALYZE: reads a sample of each table. Tables, partitioned
#            tables (their inheritance statistics) and materialized views.
#   vacuum   VACUUM (ANALYZE): marks dead rows reusable and refreshes the
#            statistics. Tables and materialized views; partitioned parents
#            are left out — each partition is in the list itself, and
#            vacuuming the parent would process every partition again.
#
# Both take SHARE UPDATE EXCLUSIVE: reads and writes carry on, only other
# maintenance/DDL on that table waits. Never VACUUM FULL.
#
# Neither fails on a table the role may not process: PostgreSQL skips it
# with a WARNING. Those warnings are caught as notices and reported as
# skipped, so "done" never claims tables that weren't touched.
#
# Jobs live in this process's memory (the backend runs one uvicorn worker),
# kept for an hour after they start. One job per schema at a time.

SCHEMA_RELKINDS = {"analyze": ("r", "p", "m"), "vacuum": ("r", "m")}
SCHEMA_STATEMENTS = {"analyze": "ANALYZE {}", "vacuum": "VACUUM (ANALYZE) {}"}

SCHEMA_TABLES_QUERY = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relkind = ANY(%s)
    ORDER BY c.relname
"""

SCHEMA_JOB_TTL = timedelta(hours=1)
_schema_jobs: dict[str, dict] = {}
_schema_jobs_lock = threading.Lock()


def _schema_job_view(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "target_id"}


def _prune_schema_jobs(now: datetime) -> None:
    with _schema_jobs_lock:
        for job_id in [j for j, job in _schema_jobs.items() if now - job["started_at"] > SCHEMA_JOB_TTL]:
            del _schema_jobs[job_id]


def _update_schema_job(job_id: str, **fields) -> None:
    with _schema_jobs_lock:
        if job_id in _schema_jobs:
            _schema_jobs[job_id].update(fields)


def _run_schema_maintenance(job_id: str, target_id, operation: str, schema_name: str, tables: list[str]) -> None:
    statement = SCHEMA_STATEMENTS[operation]
    skipped: list[str] = []
    failed: list[dict] = []
    completed = 0
    notices: list[str] = []
    try:
        with connect_to_target(
            target_id, autocommit=True, statement_timeout_ms=MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn, conn.cursor() as cur:
            conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
            for done, table in enumerate(tables):
                _update_schema_job(job_id, current=table, done=done)
                notices.clear()
                try:
                    cur.execute(sql.SQL(statement).format(sql.Identifier(schema_name, table)))
                except psycopg.Error as exc:
                    if conn.broken:
                        raise
                    failed.append({"table": table, "error": (exc.diag.message_primary or str(exc))[:300]})
                else:
                    if any("skipping" in message for message in notices):
                        skipped.append(table)
                    else:
                        completed += 1
                _update_schema_job(job_id, completed=completed, skipped=list(skipped), failed=list(failed))
    except Exception as exc:  # noqa: BLE001 — reported to the waiting screen
        logger.warning("%s of schema %s on target %s stopped: %s", operation, schema_name, target_id, exc)
        _update_schema_job(
            job_id,
            status="error",
            error=str(exc),
            current=None,
            done=completed + len(skipped) + len(failed),
            finished_at=datetime.now(timezone.utc),
        )
        return
    _update_schema_job(job_id, status="done", current=None, done=len(tables), finished_at=datetime.now(timezone.utc))


def _start_schema_job(target_id: uuid.UUID, operation: str, schema_name: str, background_tasks: BackgroundTasks) -> dict:
    """The same operation already running on this schema returns that job;
    a different one is refused (409), so a schema never has two at once."""
    allowed = get_allowed_schemas(target_id)
    if allowed and schema_name not in allowed:
        raise HTTPException(status_code=404, detail="That schema is outside this connection's schema filter.")

    now = datetime.now(timezone.utc)
    _prune_schema_jobs(now)
    with _schema_jobs_lock:
        for job in _schema_jobs.values():
            if job["target_id"] == target_id and job["schema_name"] == schema_name and job["status"] == "running":
                if job["operation"] == operation:
                    return _schema_job_view(job)
                raise HTTPException(
                    status_code=409,
                    detail=f"A {job['operation']} of {schema_name} is still running; try again when it's done.",
                )

    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_TABLES_QUERY, (schema_name, list(SCHEMA_RELKINDS[operation])))
            tables = [row[0] for row in cur.fetchall()]
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc
    if not tables:
        raise HTTPException(status_code=404, detail=f"No tables in schema {schema_name}.")

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "target_id": target_id,
        "operation": operation,
        "schema_name": schema_name,
        "status": "running",
        "total": len(tables),
        "done": 0,
        "completed": 0,
        "current": None,
        "skipped": [],
        "failed": [],
        "error": None,
        "started_at": now,
        "finished_at": None,
    }
    with _schema_jobs_lock:
        _schema_jobs[job_id] = job
    background_tasks.add_task(_run_schema_maintenance, job_id, target_id, operation, schema_name, tables)
    return _schema_job_view(job)


@router.post("/{target_id}/maintenance/analyze-schema", response_model=SchemaMaintenanceJob)
def analyze_schema(target_id: uuid.UUID, payload: SchemaMaintenanceRequest, background_tasks: BackgroundTasks):
    """Starts ANALYZE on every table, partitioned table and materialized
    view in one schema; poll GET …/schema-jobs/{job_id}."""
    return _start_schema_job(target_id, "analyze", payload.schema_name, background_tasks)


@router.post("/{target_id}/maintenance/vacuum-schema", response_model=SchemaMaintenanceJob)
def vacuum_schema(target_id: uuid.UUID, payload: SchemaMaintenanceRequest, background_tasks: BackgroundTasks):
    """Starts VACUUM (ANALYZE) on every table and materialized view in one
    schema; poll GET …/schema-jobs/{job_id}."""
    return _start_schema_job(target_id, "vacuum", payload.schema_name, background_tasks)


@router.get("/{target_id}/maintenance/schema-jobs/{job_id}", response_model=SchemaMaintenanceJob)
def get_schema_job(target_id: uuid.UUID, job_id: str):
    with _schema_jobs_lock:
        job = _schema_jobs.get(job_id)
        view = _schema_job_view(job) if job is not None and job["target_id"] == target_id else None
    if view is None:
        raise HTTPException(status_code=404, detail="This job has finished more than an hour ago or doesn't exist.")
    return view
