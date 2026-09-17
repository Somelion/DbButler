import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.replication_advisor import (
    find_inactive_slot_findings,
    find_replication_lag_findings,
    find_subscription_apply_worker_down_findings,
)
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import IndexFinding, ReplicationAdvisorResponse
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["replication-advisor"])

# One row per connected WAL sender — physical standby or logical subscriber
# alike, Postgres exposes both identically here, lag columns included.
# Empty on an ordinary standalone database with no replicas.
REPLICATION_STATUS_QUERY = """
    SELECT pid, application_name, client_addr::text, state, sync_state,
           EXTRACT(EPOCH FROM replay_lag) AS replay_lag_seconds
    FROM pg_stat_replication
"""

# retained_bytes uses pg_last_wal_replay_lsn() instead of pg_current_wal_lsn()
# when the target itself is a standby (pg_is_in_recovery()) — the latter
# errors ("recovery is in progress") there, but a cascading standby can
# still have its own downstream slots worth checking. wal_status
# ('reserved'/'extended'/'unreserved'/'lost') requires PostgreSQL 13+ — in
# practice this app is sometimes pointed at an older target despite that
# being the documented minimum, so it's version-gated below rather than
# assumed.
REPLICATION_SLOTS_QUERY = """
    SELECT
        slot_name,
        slot_type,
        active,
        wal_status,
        pg_wal_lsn_diff(
            CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_lsn() END,
            restart_lsn
        ) AS retained_bytes
    FROM pg_replication_slots
"""

# Pre-PostgreSQL 13 fallback — wal_status doesn't exist yet, so NULL is
# substituted; replication_slot_severity() already treats wal_status=None
# as "fall back to the retained-WAL-bytes floor" rather than needing a
# separate code path here.
REPLICATION_SLOTS_QUERY_LEGACY = """
    SELECT
        slot_name,
        slot_type,
        active,
        NULL AS wal_status,
        pg_wal_lsn_diff(
            CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_lsn() END,
            restart_lsn
        ) AS retained_bytes
    FROM pg_replication_slots
"""

REPLICATION_SLOTS_WAL_STATUS_MIN_VERSION = 130000


def fetch_replication_slots(cur) -> list[tuple]:
    """Version-gated rather than probed-and-caught, same reasoning as
    dashboard.py's checkpoint-stats branch: a failed query aborts the rest
    of the transaction on the same cursor. Shared by
    compute_replication_advisor_findings below and dashboard.py's
    compute_health so the two never pick different queries for the same
    target."""
    cur.execute("SHOW server_version_num")
    version_num = int(cur.fetchone()[0])
    cur.execute(
        REPLICATION_SLOTS_QUERY if version_num >= REPLICATION_SLOTS_WAL_STATUS_MIN_VERSION else REPLICATION_SLOTS_QUERY_LEGACY
    )
    return cur.fetchall()

# pg_subscription is a shared catalog (one copy per cluster, not per
# database) — subdbid narrows it to the current database's own
# subscriptions, otherwise this would also see (and false-positive on)
# every other database's subscriptions, which pg_stat_subscription never
# has worker rows for from here regardless of their actual state. The main
# apply worker's own row in pg_stat_subscription has relid IS NULL
# (per-table sync workers have relid set); a NULL pid there means no apply
# worker is currently running for an otherwise-enabled subscription. Doesn't
# select subconninfo (the one pg_subscription column normal users can't
# read) — subname/subenabled are unrestricted.
SUBSCRIPTION_APPLY_WORKER_QUERY = """
    SELECT s.subname
    FROM pg_subscription s
    LEFT JOIN pg_stat_subscription st ON st.subid = s.oid AND st.relid IS NULL
    WHERE s.subenabled
      AND st.pid IS NULL
      AND s.subdbid = (SELECT oid FROM pg_database WHERE datname = current_database())
"""


@router.get("/{target_id}/replication-advisor", response_model=ReplicationAdvisorResponse)
def get_replication_advisor(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_replication_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return ReplicationAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_replication_advisor_findings(cur) -> list[dict]:
    """Runs both Replication Advisor checks against an already-open target
    cursor and returns raw finding dicts, unfiltered by archive state. Shared
    by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart."""
    cur.execute(REPLICATION_STATUS_QUERY)
    replication_rows = cur.fetchall()

    slot_rows = fetch_replication_slots(cur)

    cur.execute(SUBSCRIPTION_APPLY_WORKER_QUERY)
    subscription_rows = cur.fetchall()

    return (
        find_replication_lag_findings(replication_rows)
        + find_inactive_slot_findings(slot_rows)
        + find_subscription_apply_worker_down_findings(subscription_rows)
    )
