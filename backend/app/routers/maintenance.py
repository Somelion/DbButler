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
    AnalyzeSchemaJob,
    AnalyzeSchemaRequest,
    MaintenanceResult,
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


# --- Analyze all tables of a schema (Table Health's "Analyze all") -----------
#
# A background job, polled by the screen: ANALYZE reads a sample of every
# table, and a big schema takes longer than nginx's 60s proxy timeout. One
# ANALYZE per table on one autocommit connection — each table's statistics
# land as soon as it's done, a failure (lock timeout, a dropped table) is
# recorded and the next table goes on. ANALYZE takes SHARE UPDATE EXCLUSIVE:
# reads and writes carry on, only other maintenance/DDL on that table waits.
#
# ANALYZE doesn't fail on a table the role may not analyze: it skips it with
# a WARNING. Those warnings are caught as notices and reported as skipped, so
# "done" never claims tables that weren't touched.
#
# Jobs live in this process's memory (the backend runs one uvicorn worker),
# kept for an hour after they start.

SCHEMA_TABLES_QUERY = """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'm')
    ORDER BY c.relname
"""

ANALYZE_JOB_TTL = timedelta(hours=1)
_analyze_jobs: dict[str, dict] = {}
_analyze_jobs_lock = threading.Lock()


def _analyze_job_view(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "target_id"}


def _prune_analyze_jobs(now: datetime) -> None:
    with _analyze_jobs_lock:
        for job_id in [j for j, job in _analyze_jobs.items() if now - job["started_at"] > ANALYZE_JOB_TTL]:
            del _analyze_jobs[job_id]


def _update_analyze_job(job_id: str, **fields) -> None:
    with _analyze_jobs_lock:
        if job_id in _analyze_jobs:
            _analyze_jobs[job_id].update(fields)


def _run_analyze_schema(job_id: str, target_id: uuid.UUID, schema_name: str, tables: list[str]) -> None:
    skipped: list[str] = []
    failed: list[dict] = []
    analyzed = 0
    notices: list[str] = []
    try:
        with connect_to_target(
            target_id, autocommit=True, statement_timeout_ms=MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn, conn.cursor() as cur:
            conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
            for done, table in enumerate(tables):
                _update_analyze_job(job_id, current=table, done=done)
                notices.clear()
                try:
                    cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(schema_name, table)))
                except psycopg.Error as exc:
                    if conn.broken:
                        raise
                    failed.append({"table": table, "error": (exc.diag.message_primary or str(exc))[:300]})
                else:
                    if any("skipping" in message for message in notices):
                        skipped.append(table)
                    else:
                        analyzed += 1
                _update_analyze_job(job_id, analyzed=analyzed, skipped=list(skipped), failed=list(failed))
    except Exception as exc:  # noqa: BLE001 — reported to the waiting screen
        logger.warning("analyze of schema %s on target %s stopped: %s", schema_name, target_id, exc)
        _update_analyze_job(
            job_id,
            status="error",
            error=str(exc),
            current=None,
            done=analyzed + len(skipped) + len(failed),
            finished_at=datetime.now(timezone.utc),
        )
        return
    _update_analyze_job(
        job_id, status="done", current=None, done=len(tables), finished_at=datetime.now(timezone.utc)
    )


@router.post("/{target_id}/maintenance/analyze-schema", response_model=AnalyzeSchemaJob)
def analyze_schema(target_id: uuid.UUID, payload: AnalyzeSchemaRequest, background_tasks: BackgroundTasks):
    """Starts ANALYZE on every table, partitioned table and materialized
    view in one schema; poll GET …/analyze-schema/{job_id}. A schema that
    already has a job running for this target returns that job instead of
    starting a second one."""
    allowed = get_allowed_schemas(target_id)
    if allowed and payload.schema_name not in allowed:
        raise HTTPException(status_code=404, detail="That schema is outside this connection's schema filter.")

    now = datetime.now(timezone.utc)
    _prune_analyze_jobs(now)
    with _analyze_jobs_lock:
        for job in _analyze_jobs.values():
            if job["target_id"] == target_id and job["schema_name"] == payload.schema_name and job["status"] == "running":
                return _analyze_job_view(job)

    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(SCHEMA_TABLES_QUERY, (payload.schema_name,))
            tables = [row[0] for row in cur.fetchall()]
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc
    if not tables:
        raise HTTPException(status_code=404, detail=f"No tables in schema {payload.schema_name}.")

    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "target_id": target_id,
        "schema_name": payload.schema_name,
        "status": "running",
        "total": len(tables),
        "done": 0,
        "analyzed": 0,
        "current": None,
        "skipped": [],
        "failed": [],
        "error": None,
        "started_at": now,
        "finished_at": None,
    }
    with _analyze_jobs_lock:
        _analyze_jobs[job_id] = job
    background_tasks.add_task(_run_analyze_schema, job_id, target_id, payload.schema_name, tables)
    return _analyze_job_view(job)


@router.get("/{target_id}/maintenance/analyze-schema/{job_id}", response_model=AnalyzeSchemaJob)
def get_analyze_schema_job(target_id: uuid.UUID, job_id: str):
    with _analyze_jobs_lock:
        job = _analyze_jobs.get(job_id)
        view = _analyze_job_view(job) if job is not None and job["target_id"] == target_id else None
    if view is None:
        raise HTTPException(status_code=404, detail="This job has finished more than an hour ago or doesn't exist.")
    return view
