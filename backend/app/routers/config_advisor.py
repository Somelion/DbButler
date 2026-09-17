import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from app.config_advisor import (
    find_autovacuum_disabled_findings,
    find_checksums_disabled_findings,
    find_fsync_off_findings,
    find_full_page_writes_off_findings,
    find_pg_stat_statements_eviction_findings,
    find_random_page_cost_findings,
    find_synchronous_commit_off_findings,
    find_version_eol_findings,
)
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import ConfigAdvisorResponse, IndexFinding
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["config-advisor"])

# pg_stat_statements_info (and its `dealloc` column) doesn't exist before
# PostgreSQL 14 — version-gated rather than probed-and-caught, same
# reasoning as dashboard.py's checkpoint-stats branch: a failed query aborts
# the rest of the transaction on the same cursor.
PG_STAT_STATEMENTS_INFO_MIN_VERSION = 140000

PER_TABLE_AUTOVACUUM_DISABLED_QUERY = """
    SELECT n.nspname AS schema_name, c.relname AS table_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND EXISTS (
          SELECT 1 FROM unnest(c.reloptions) AS opt
          WHERE opt = 'autovacuum_enabled=false'
      )
"""


@router.get("/{target_id}/config-advisor", response_model=ConfigAdvisorResponse)
def get_config_advisor(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_config_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return ConfigAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_config_advisor_findings(cur) -> list[dict]:
    """Runs all Configuration Advisor checks against an already-open target
    cursor and returns raw finding dicts, unfiltered by archive state. Shared
    by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart."""
    cur.execute("SHOW autovacuum")
    autovacuum_enabled = cur.fetchone()[0] == "on"

    cur.execute(PER_TABLE_AUTOVACUUM_DISABLED_QUERY)
    per_table_rows = cur.fetchall()

    cur.execute("SHOW fsync")
    fsync_enabled = cur.fetchone()[0] == "on"

    cur.execute("SHOW random_page_cost")
    random_page_cost = float(cur.fetchone()[0])

    cur.execute("SHOW synchronous_commit")
    synchronous_commit = cur.fetchone()[0]

    cur.execute("SHOW full_page_writes")
    full_page_writes = cur.fetchone()[0] == "on"

    cur.execute("SHOW server_version_num")
    version_num = int(cur.fetchone()[0])
    major_version = version_num // 10000

    # pg_control_init() is superuser-only by default and isn't granted to
    # pg_monitor (this app's recommended role) — checked with
    # has_function_privilege() first rather than attempting the call and
    # catching a permission error, since a failed query on this cursor
    # would abort the rest of this function's shared transaction.
    cur.execute("SELECT has_function_privilege('pg_control_init()', 'EXECUTE')")
    data_page_checksum_version = None
    if cur.fetchone()[0]:
        cur.execute("SELECT data_page_checksum_version FROM pg_control_init()")
        data_page_checksum_version = cur.fetchone()[0]

    pg_stat_statements_findings = []
    if pg_stat_statements_enabled(cur) and version_num >= PG_STAT_STATEMENTS_INFO_MIN_VERSION:
        cur.execute("SELECT dealloc, stats_reset FROM pg_stat_statements_info")
        dealloc, stats_reset = cur.fetchone()
        cur.execute("SHOW pg_stat_statements.max")
        pg_stat_statements_max = int(cur.fetchone()[0])
        pg_stat_statements_findings = find_pg_stat_statements_eviction_findings(
            dealloc, stats_reset, pg_stat_statements_max
        )

    return (
        find_autovacuum_disabled_findings(autovacuum_enabled, per_table_rows)
        + find_fsync_off_findings(fsync_enabled)
        + find_random_page_cost_findings(random_page_cost)
        + find_synchronous_commit_off_findings(synchronous_commit)
        + find_full_page_writes_off_findings(full_page_writes)
        + find_version_eol_findings(major_version, datetime.now(timezone.utc).date())
        + find_checksums_disabled_findings(data_page_checksum_version)
        + pg_stat_statements_findings
    )
