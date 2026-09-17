"""Replication Advisor analyzers — pure functions over pg_stat_replication /
pg_replication_slots rows (routers/replication_advisor.py). A complete blind
spot before this: nothing else in the app reads either view. Only produces
findings when replication is actually configured on the target — an
ordinary standalone database has empty rows for both, and this category
stays quiet (see ui/src/Advisor.jsx's empty-state message for this tab).
"""

from app.schemas import CategoryStatus
from app.severity import replication_lag_severity, replication_slot_severity, worse

CATEGORY = "replication advisor"


def _replica_identity(application_name, client_addr, pid):
    """pg_stat_replication has no single reliably-unique, human-readable
    column: application_name defaults to 'walreceiver' for an unconfigured
    physical standby (colliding across multiple replicas that didn't set
    their own), client_addr is NULL for a local/unix-socket connection, and
    pid is unique right now but changes on every reconnect. Combining
    name+address covers the common case; pid is only a last-resort
    fallback."""
    if application_name and client_addr:
        return f"{application_name}-{client_addr}"
    return application_name or client_addr or f"pid-{pid}"


def find_replication_lag_findings(rows) -> list[dict]:
    """rows: (pid, application_name, client_addr, state, sync_state,
    replay_lag_seconds) from pg_stat_replication — one row per connected WAL
    sender, physical standby or logical subscriber alike (Postgres exposes
    both identically here, lag columns included), so this one check covers
    what the roadmap originally split into separate "physical" and
    "logical" lag checks. replay_lag_seconds is NULL until a replica has
    sent enough feedback to compute it — reported as `unknown` severity
    (and skipped, same as `healthy`) rather than guessed at."""
    findings = []
    for pid, application_name, client_addr, state, sync_state, replay_lag_seconds in rows:
        severity = replication_lag_severity(replay_lag_seconds)
        if severity in ("healthy", "unknown"):
            continue
        identity = _replica_identity(application_name, client_addr, pid)
        findings.append(
            {
                "id": f"replication-lag-{identity}",
                "category": CATEGORY,
                "severity": severity,
                "title": f"Replica {identity} is {replay_lag_seconds:.0f}s behind",
                "summary": (
                    f"This replica's replay lag is {replay_lag_seconds:.0f} seconds (state: {state}, "
                    f"sync_state: {sync_state}) — queries against it may return stale data, and it "
                    "drifts further from being a usable failover target the longer this persists."
                ),
                "detail": (
                    f"pid={pid} application_name={application_name or '(unset)'} "
                    f"client_addr={client_addr or '(local)'} state={state} sync_state={sync_state} "
                    f"replay_lag_seconds={replay_lag_seconds:.1f}"
                ),
                "suggested_action": (
                    "Check the replica's own resource usage (I/O, CPU, network) and whether a "
                    "long-running query is holding back WAL replay there; sustained lag under normal "
                    "load usually means the replica is undersized for the primary's write rate."
                ),
            }
        )
    return findings


def find_inactive_slot_findings(rows) -> list[dict]:
    """rows: (slot_name, slot_type, active, wal_status, retained_bytes) from
    pg_replication_slots. An inactive slot keeps retaining WAL for whatever
    consumer used to read it — a documented, easy-to-miss failure mode
    distinct from a simply-lagging live replica: nothing here is actively
    connected, so nothing shows up as "slow," it just quietly fills disk
    until the slot is dropped or its consumer reconnects."""
    findings = []
    for slot_name, slot_type, active, wal_status, retained_bytes in rows:
        severity = replication_slot_severity(active, wal_status, retained_bytes)
        if severity == "healthy":
            continue
        retained_mb = retained_bytes / (1024 * 1024)

        if wal_status == "lost":
            suggested_action = (
                "This slot has already lost WAL it needed — its consumer can no longer resume from "
                f"here regardless. Drop and recreate it: SELECT pg_drop_replication_slot('{slot_name}');"
            )
        else:
            suggested_action = (
                "If this slot's consumer (a replica, or a logical replication/CDC subscriber) is "
                "permanently gone, drop it — otherwise reconnect its consumer soon, before wal_status "
                f"reaches 'lost': SELECT pg_drop_replication_slot('{slot_name}');"
            )

        findings.append(
            {
                "id": f"inactive-replication-slot-{slot_name}",
                "category": CATEGORY,
                "severity": severity,
                "title": f"Replication slot {slot_name} is inactive and retaining WAL",
                "summary": (
                    f"This {slot_type} slot has no connected consumer but is still retaining "
                    f"{retained_mb:,.0f} MB of WAL (wal_status: {wal_status}) — WAL keeps accumulating "
                    "on disk until either the slot is dropped or its consumer reconnects."
                ),
                "detail": (
                    f"slot_name={slot_name} slot_type={slot_type} active=false wal_status={wal_status} "
                    f"retained_bytes={retained_bytes}"
                ),
                "suggested_action": suggested_action,
                "recommended_ddl": f"SELECT pg_drop_replication_slot('{slot_name}');",
            }
        )
    return findings


def find_subscription_apply_worker_down_findings(rows) -> list[dict]:
    """rows: (subname,) for logical replication subscriptions that are
    enabled (subenabled=true) but have no running apply worker in
    pg_stat_subscription (its main, relid-IS-NULL row) — see
    SUBSCRIPTION_APPLY_WORKER_QUERY in routers/replication_advisor.py. A
    logical replication conflict (e.g. a unique-constraint violation the
    incoming row would break) halts the apply worker silently: the
    subscription still shows enabled, but nothing is actually being
    replicated until someone notices and resolves it by hand. Advisor-tab
    and nightly-deep-scan only, not folded into the Dashboard's
    Replication tile/live findings cycle — this is a distinct, rarer
    failure mode from the lag/inactive-slot checks that tile already
    covers, running on a target-only cursor with no separate live-check
    plumbing of its own yet."""
    findings = []
    for (subname,) in rows:
        findings.append(
            {
                "id": f"subscription-apply-worker-down-{subname}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"Subscription {subname} is enabled but its apply worker isn't running",
                "summary": (
                    f"{subname} is enabled but has no running apply worker — most often a logical "
                    "replication conflict (e.g. a unique-constraint violation) halted it silently. "
                    "Nothing is being replicated through this subscription until it's resolved."
                ),
                "detail": f"subname={subname} subenabled=true apply_worker_pid=none",
                "suggested_action": (
                    "Check the subscriber's log for the conflict that stopped it (often a "
                    "unique-constraint violation) and resolve it — the apply worker restarts "
                    "automatically once the conflict is gone, no manual restart needed."
                ),
                "recommended_ddl": None,
            }
        )
    return findings


def build_replication_status(replication_rows, slot_rows) -> tuple[CategoryStatus, list[dict]]:
    """Rolls both Replication Advisor checks into one Dashboard tile — reuses
    the exact same finding-producing functions above (rather than a
    parallel severity calculation), so the Dashboard tile and the Advisor
    tab can never disagree about what's actually wrong. Unlike an ordinary
    standalone database's Pooler tile (absent entirely unless a PgBouncer
    connection is configured), this tile always renders: replication being
    unconfigured is itself worth a quiet, visible "—" rather than vanishing,
    since a DBA who *expects* a standby glancing at the Dashboard should see
    that absence immediately, not have to go check the Advisor tab to learn
    there isn't one."""
    findings = find_replication_lag_findings(replication_rows) + find_inactive_slot_findings(slot_rows)
    severity = "healthy"
    for finding in findings:
        severity = worse(severity, finding["severity"])

    if not replication_rows and not slot_rows:
        return (
            CategoryStatus(
                key="replication",
                label="Replication",
                severity="unknown",
                value="—",
                detail="no replication configured",
            ),
            findings,
        )

    replica_count = len(replication_rows)
    if replica_count:
        detail = f"{replica_count} connected replica{'s' if replica_count != 1 else ''}"
    else:
        detail = f"{len(slot_rows)} replication slot{'s' if len(slot_rows) != 1 else ''}, no replica connected"

    return (
        CategoryStatus(key="replication", label="Replication", severity=severity, value=str(replica_count), detail=detail),
        findings,
    )
