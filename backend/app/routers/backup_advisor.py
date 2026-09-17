import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.backup_advisor import (
    find_archive_mode_off_findings,
    find_wal_archiving_failure_findings,
    find_wal_dir_size_findings,
)
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import BackupAdvisorResponse, IndexFinding
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["backup-advisor"])

# pg_stat_archiver is a single, un-keyed row of cumulative counters since
# its own last reset (pg_stat_reset_shared('archiver')) — not per-WAL-file
# history, just the latest success/failure snapshot.
ARCHIVER_STATUS_QUERY = """
    SELECT failed_count, last_archived_time, last_failed_time, last_failed_wal
    FROM pg_stat_archiver
"""

# pg_ls_waldir() lists pg_wal's own contents over SQL, with no filesystem
# access needed — by default restricted to superusers and pg_monitor
# members, the role this app already recommends everywhere else.
WAL_DIR_SIZE_QUERY = "SELECT COALESCE(sum(size), 0) FROM pg_ls_waldir()"

MAX_WAL_SIZE_QUERY = "SELECT pg_size_bytes(current_setting('max_wal_size'))"


@router.get("/{target_id}/backup-advisor", response_model=BackupAdvisorResponse)
def get_backup_advisor(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_backup_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return BackupAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_backup_advisor_findings(cur) -> list[dict]:
    """Runs all Backup & WAL Health checks against an already-open target
    cursor and returns raw finding dicts, unfiltered by archive state. Shared
    by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart."""
    cur.execute("SHOW archive_mode")
    archive_mode = cur.fetchone()[0]

    cur.execute(ARCHIVER_STATUS_QUERY)
    failed_count, last_archived_time, last_failed_time, last_failed_wal = cur.fetchone()

    cur.execute(WAL_DIR_SIZE_QUERY)
    wal_dir_bytes = cur.fetchone()[0]

    cur.execute(MAX_WAL_SIZE_QUERY)
    max_wal_size_bytes = cur.fetchone()[0]

    return (
        find_archive_mode_off_findings(archive_mode)
        + find_wal_archiving_failure_findings(
            archive_mode, failed_count, last_archived_time, last_failed_time, last_failed_wal
        )
        + find_wal_dir_size_findings(wal_dir_bytes, max_wal_size_bytes)
    )
