import uuid

import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql

from app.schemas import MaintenanceResult, TableMaintenanceRequest, VacuumRequest
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["maintenance"])

# These run real commands against the target — VACUUM/ANALYZE/stat-reset,
# never DROP/ALTER/TRUNCATE. Table/schema names arrive as identifiers, which
# can't be bound as query parameters, so every statement is built with
# psycopg.sql.Identifier rather than string interpolation.
#
# A pg_monitor-only role (what the Connection screen recommends) can read
# everything but can't VACUUM/ANALYZE a table it doesn't own, or reset
# pg_stat_statements — these actions need real privileges: table ownership,
# or (PostgreSQL 17+) membership in the built-in pg_maintain role.


@router.post("/{target_id}/maintenance/analyze", response_model=MaintenanceResult)
def analyze_table(target_id: uuid.UUID, payload: TableMaintenanceRequest):
    stmt = sql.SQL("ANALYZE {}").format(sql.Identifier(payload.schema_name, payload.table_name))
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(stmt)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MaintenanceResult(ok=True, message=f"Analyzed {payload.schema_name}.{payload.table_name}.")


@router.post("/{target_id}/maintenance/vacuum", response_model=MaintenanceResult)
def vacuum_table(target_id: uuid.UUID, payload: VacuumRequest):
    keyword = "VACUUM ANALYZE {}" if payload.analyze else "VACUUM {}"
    stmt = sql.SQL(keyword).format(sql.Identifier(payload.schema_name, payload.table_name))
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
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
            cur.execute("SELECT pg_stat_statements_reset()")
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return MaintenanceResult(ok=True, message="Query statistics reset.")
