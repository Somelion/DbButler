import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.extension_advisor import (
    find_pgvector_unindexed_column_findings,
    find_postgis_unindexed_geometry_findings,
    find_timescaledb_uncompressed_hypertable_findings,
)
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import ExtensionAdvisorResponse, IndexFinding
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["extension-advisor"])

INSTALLED_EXTENSIONS_QUERY = "SELECT extname FROM pg_extension WHERE extname IN ('timescaledb', 'vector', 'postgis')"

# timescaledb_information.hypertables only exists once the extension is
# created — only ever run this once INSTALLED_EXTENSIONS_QUERY confirms it.
TIMESCALEDB_UNCOMPRESSED_HYPERTABLES_QUERY = """
    SELECT hypertable_schema, hypertable_name
    FROM timescaledb_information.hypertables
    WHERE NOT compression_enabled
"""

# pgvector's own type, not a catalog view — a column typed `vector` with no
# index referencing it at all (ivfflat/hnsw or otherwise).
PGVECTOR_UNINDEXED_COLUMN_QUERY = """
    SELECT n.nspname, c.relname, a.attname
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid AND c.relkind = 'r'
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_type t ON t.oid = a.atttypid
    WHERE t.typname = 'vector' AND a.attnum > 0 AND NOT a.attisdropped
      AND NOT EXISTS (
          SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND a.attnum = ANY(i.indkey)
      )
"""

# geometry_columns is PostGIS's own catalog view listing every geometry
# column; joined back to pg_index/pg_am to check specifically for a GiST
# index (the only index type spatial operators can use).
POSTGIS_UNINDEXED_GEOMETRY_QUERY = """
    SELECT gc.f_table_schema, gc.f_table_name, gc.f_geometry_column
    FROM geometry_columns gc
    JOIN pg_namespace n ON n.nspname = gc.f_table_schema
    JOIN pg_class c ON c.relname = gc.f_table_name AND c.relnamespace = n.oid
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = gc.f_geometry_column
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class ic ON ic.oid = i.indexrelid
        JOIN pg_am am ON am.oid = ic.relam
        WHERE i.indrelid = c.oid AND a.attnum = ANY(i.indkey) AND am.amname = 'gist'
    )
"""


@router.get("/{target_id}/extension-advisor", response_model=ExtensionAdvisorResponse)
def get_extension_advisor(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_extension_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return ExtensionAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_extension_advisor_findings(cur) -> list[dict]:
    """Runs all Extension Advisor checks against an already-open target
    cursor and returns raw finding dicts, unfiltered by archive state.
    Shared by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart. Every
    extension-specific query is gated on that extension actually being
    installed — the catalog views/types they read don't exist otherwise."""
    cur.execute(INSTALLED_EXTENSIONS_QUERY)
    installed = {row[0] for row in cur.fetchall()}

    findings: list[dict] = []

    if "timescaledb" in installed:
        cur.execute(TIMESCALEDB_UNCOMPRESSED_HYPERTABLES_QUERY)
        findings += find_timescaledb_uncompressed_hypertable_findings(cur.fetchall())

    if "vector" in installed:
        cur.execute(PGVECTOR_UNINDEXED_COLUMN_QUERY)
        findings += find_pgvector_unindexed_column_findings(cur.fetchall())

    if "postgis" in installed:
        cur.execute(POSTGIS_UNINDEXED_GEOMETRY_QUERY)
        findings += find_postgis_unindexed_geometry_findings(cur.fetchall())

    return findings
