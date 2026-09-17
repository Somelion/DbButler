import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from app.backup_advisor import build_backup_status
from app.collectors import CACHE_HIT_RATIO
from app.pg_stat_statements import installed_but_too_old
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.pgbouncer_conn import connect_to_pgbouncer, get_pgbouncer_credentials
from app.pgbouncer_status import build_pooler_status
from app.replication_advisor import build_replication_status
from app.routers.advisor_archive import get_archived_finding_ids
from app.routers.backup_advisor import ARCHIVER_STATUS_QUERY, MAX_WAL_SIZE_QUERY, WAL_DIR_SIZE_QUERY
from app.routers.replication_advisor import REPLICATION_STATUS_QUERY, fetch_replication_slots
from app.routers.table_health import TABLE_HEALTH_QUERY
from app.schemas import CategoryStatus, DashboardResponse, Finding
from app.severity import (
    autovacuum_staleness_severity,
    cache_hit_severity,
    checkpoint_severity,
    connection_pool_severity,
    dead_pct_severity,
    idle_in_tx_severity,
    long_running_query_severity,
    wraparound_severity,
    worse,
)
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["dashboard"])

# A row per currently-running autovacuum worker — backend_type is stable
# across every PostgreSQL version this app supports (9.6+), unlike the
# columns pg_stat_progress_vacuum exposes.
AUTOVACUUM_RUNNING_QUERY = "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'autovacuum worker'"

ACTIVITY_SUMMARY_QUERY = """
    SELECT
        pid,
        state,
        EXTRACT(EPOCH FROM (now() - state_change))::bigint AS state_duration_seconds,
        pg_blocking_pids(pid) AS blocked_by_pids
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
"""

# PostgreSQL 17 moved checkpoint stats out of pg_stat_bgwriter into their own
# pg_stat_checkpointer view, renaming the columns in the process
# (checkpoints_timed/checkpoints_req -> num_timed/num_requested). Branch on
# server_version_num rather than probing-and-catching, since a failed query
# aborts the rest of the transaction on the same cursor.
CHECKPOINT_QUERY_LEGACY = "SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter"
CHECKPOINT_QUERY_PG17 = "SELECT num_timed, num_requested FROM pg_stat_checkpointer"


def _fetch_checkpoint_stats(cur):
    cur.execute("SHOW server_version_num")
    version_num = int(cur.fetchone()[0])
    cur.execute(CHECKPOINT_QUERY_PG17 if version_num >= 170000 else CHECKPOINT_QUERY_LEGACY)
    return cur.fetchone()


def _fetch_pooler_status(target_id: uuid.UUID, dbname: str) -> tuple[CategoryStatus | None, Finding | None]:
    """None, None when this target has no PgBouncer connection configured
    (the common case). A configured-but-currently-unreachable PgBouncer
    surfaces as its own tile/finding rather than either crashing the whole
    Dashboard or silently vanishing — this is a separate connection
    (app/pgbouncer_conn.py) from the target's own, so its own failures must
    never be mistaken for the target itself being unreachable (the
    get_dashboard endpoint's except psycopg.Error is scoped to the target
    connection only, above)."""
    if get_pgbouncer_credentials(target_id) is None:
        return None, None

    try:
        with connect_to_pgbouncer(target_id) as conn, conn.cursor() as cur:
            cur.execute("SHOW POOLS")
            columns = [desc[0] for desc in cur.description]
            pool_rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except psycopg.Error as exc:
        return (
            CategoryStatus(key="pooler", label="Pooler", severity="unknown", value="—", detail="PgBouncer unreachable"),
            Finding(
                id="pooler-unreachable",
                category="pooler",
                severity="unknown",
                title="Could not reach PgBouncer's admin console",
                summary=(
                    "A PgBouncer connection is configured for this target but the last attempt to read "
                    "pool status failed."
                ),
                detail=str(exc),
                suggested_action=(
                    "Check the PgBouncer connection settings on the Connections screen (host/port/"
                    "credentials, and that this user is a member of PgBouncer's stats_users or "
                    "admin_users)."
                ),
            ),
        )

    return build_pooler_status(pool_rows, dbname)


@router.get("/{target_id}/dashboard", response_model=DashboardResponse)
def get_dashboard(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            return compute_health(cur, target_id)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc


def compute_health(cur, target_id: uuid.UUID) -> DashboardResponse:
    """Runs the full set of live health checks against an already-open target
    cursor. Shared by the dashboard endpoint and the Diagnose Now runbook so
    the two never drift apart. target_id is only needed for the optional
    Pooler category, which reads a separate PgBouncer connection
    (app/pgbouncer_conn.py) rather than anything reachable via cur."""
    cur.execute(ACTIVITY_SUMMARY_QUERY)
    activity_rows = cur.fetchall()

    cur.execute("SHOW max_connections")
    max_connections = int(cur.fetchone()[0])

    cur.execute(TABLE_HEALTH_QUERY)
    table_rows = cur.fetchall()

    cur.execute(CACHE_HIT_RATIO.query)
    cache_hit_row = cur.fetchone()

    checkpoint_row = _fetch_checkpoint_stats(cur)

    query_stats_enabled = pg_stat_statements_enabled(cur)

    categories: list[CategoryStatus] = []
    findings: list[Finding] = []

    if not query_stats_enabled:
        if installed_but_too_old(cur):
            findings.append(
                Finding(
                    id="query-stats-disabled",
                    category="query intelligence",
                    severity="unknown",
                    title="Detailed query stats aren't available on this PostgreSQL version",
                    summary="This server is older than PostgreSQL 13, which PostgreDba's query stats require.",
                    detail="pg_stat_statements is installed, but the server predates the 13+ column names this app relies on.",
                    suggested_action="Upgrade the target to PostgreSQL 13 or newer to use this feature.",
                )
            )
        else:
            findings.append(
                Finding(
                    id="query-stats-disabled",
                    category="query intelligence",
                    severity="unknown",
                    title="Detailed query stats are turned off",
                    summary="Turning on one setting lets PostgreDba show you your slowest queries.",
                    detail="pg_stat_statements is not created on this database.",
                    suggested_action=(
                        "Add pg_stat_statements to shared_preload_libraries, restart PostgreSQL, "
                        "then run: CREATE EXTENSION pg_stat_statements;"
                    ),
                )
            )

    # Connections / idle-in-transaction / pool exhaustion
    idle_tx = [row for row in activity_rows if row[1] == "idle in transaction"]
    worst_idle = max(idle_tx, key=lambda row: row[2] or 0, default=None)
    conn_severity = idle_in_tx_severity(worst_idle[2] if worst_idle else None)

    active = [row for row in activity_rows if row[1] == "active"]
    worst_active = max(active, key=lambda row: row[2] or 0, default=None)
    conn_severity = worse(conn_severity, long_running_query_severity(worst_active[2] if worst_active else None))

    # +1 for this dashboard's own connection, which ACTIVITY_SUMMARY_QUERY
    # excludes (WHERE pid <> pg_backend_pid()) but which still counts against
    # max_connections.
    live_session_count = len(activity_rows) + 1
    pool_pct = 100.0 * live_session_count / max_connections if max_connections else 0.0
    pool_severity = connection_pool_severity(pool_pct)
    conn_severity = worse(conn_severity, pool_severity)

    categories.append(
        CategoryStatus(
            key="connections",
            label="Connections",
            severity=conn_severity,
            value=str(live_session_count),
            detail=f"active sessions (of {max_connections} max)",
        )
    )
    if worst_idle and idle_in_tx_severity(worst_idle[2]) != "healthy":
        pid, _, duration, _ = worst_idle
        findings.append(
            Finding(
                id=f"idle-tx-{pid}",
                category="connections",
                severity=idle_in_tx_severity(duration),
                title="A session has been idle inside a transaction",
                summary=(
                    f"Session {pid} has held an open transaction for {duration}s. "
                    "This can block cleanup (VACUUM) on tables it has touched."
                ),
                detail=f"pid={pid} state=idle in transaction state_duration_seconds={duration}",
                suggested_action="Open Activity and consider terminating this session if it's not expected to resume.",
            )
        )
    if worst_active and long_running_query_severity(worst_active[2]) != "healthy":
        pid, _, duration, _ = worst_active
        findings.append(
            Finding(
                id=f"long-running-query-{pid}",
                category="connections",
                severity=long_running_query_severity(duration),
                title="A query has been running for a long time",
                summary=(
                    f"Session {pid} has been actively running the same query for {duration}s — either "
                    "an expected long batch job, or one stuck behind lock contention or a bad plan."
                ),
                detail=f"pid={pid} state=active state_duration_seconds={duration}",
                suggested_action="Open Activity to see the query text and check whether it's expected to still be running.",
            )
        )
    if pool_severity != "healthy":
        findings.append(
            Finding(
                id="connection-pool-high",
                category="connections",
                severity=pool_severity,
                title="Connections are approaching the max_connections limit",
                summary=(
                    f"{live_session_count} of {max_connections} connection slots are in use "
                    f"({pool_pct:.0f}%). Once the limit is reached, new connections are refused "
                    "outright."
                ),
                detail=f"active_sessions={live_session_count} max_connections={max_connections}",
                suggested_action=(
                    "Consider a connection pooler like PgBouncer in front of the database, "
                    "which lets far more clients share a much smaller number of real backend "
                    "connections."
                ),
            )
        )

    # Locks
    blocked = [row for row in activity_rows if row[3]]
    lock_severity = "critical" if blocked else "healthy"
    categories.append(
        CategoryStatus(
            key="locks", label="Locks", severity=lock_severity, value=str(len(blocked)), detail="blocked sessions"
        )
    )
    if blocked:
        pids = [row[0] for row in blocked]
        findings.append(
            Finding(
                id="locks-blocked",
                category="locks",
                severity=lock_severity,
                title="A query is being blocked",
                summary=f"{len(blocked)} session(s) are waiting on a lock held by another session.",
                detail=f"blocked pids={pids}",
                suggested_action="Open Activity to see which session is blocking and consider cancelling or terminating it.",
            )
        )

    # Bloat — worst table across the target
    worst_bloat = None
    worst_pct = -1.0
    for row in table_rows:
        schema_name, table_name, live, dead = row[0], row[1], row[2] or 0, row[3] or 0
        total = live + dead
        pct = (100.0 * dead / total) if total > 0 else 0.0
        if pct > worst_pct:
            worst_pct, worst_bloat = pct, (schema_name, table_name, live, dead, pct)
    bloat_severity = dead_pct_severity(worst_pct) if worst_bloat else "unknown"
    categories.append(
        CategoryStatus(
            key="bloat",
            label="Bloat",
            severity=bloat_severity,
            value=f"{worst_pct:.0f}%" if worst_bloat else "—",
            detail=f"{worst_bloat[1]} table" if worst_bloat else "no tables yet",
        )
    )
    if worst_bloat and bloat_severity != "healthy":
        schema_name, table_name, live, dead, pct = worst_bloat
        findings.append(
            Finding(
                id=f"bloat-{schema_name}-{table_name}",
                category="bloat",
                severity=bloat_severity,
                title=f"Table {schema_name}.{table_name} is accumulating dead rows",
                summary=(
                    f"About {pct:.0f}% of this table has not been cleaned up yet. "
                    "This can slow it down over time."
                ),
                detail=f"dead_tup={dead} live_tup={live} dead_pct={pct:.1f}",
                suggested_action="Run VACUUM on this table, or lower its autovacuum_vacuum_scale_factor if this recurs.",
                schema_name=schema_name,
                table_name=table_name,
            )
        )

    # Cache hit rate
    cache_pct = cache_hit_row[0] if cache_hit_row else None
    cache_severity = cache_hit_severity(cache_pct)
    categories.append(
        CategoryStatus(
            key="cache",
            label="Cache Hit Rate",
            severity=cache_severity,
            value=f"{cache_pct:.1f}%" if cache_pct is not None else "—",
            detail="target: above 95%",
        )
    )
    if cache_pct is not None and cache_severity != "healthy":
        findings.append(
            Finding(
                id="cache-low",
                category="cache",
                severity=cache_severity,
                title="Cache hit rate has dropped",
                summary=(
                    f"Only {cache_pct:.1f}% of reads are being served from memory (target: above 95%). "
                    "Queries may be hitting disk more than usual."
                ),
                detail=f"cache_hit_ratio={cache_pct:.2f}",
                suggested_action="This can be temporary after a restart or bulk load — watch the Dashboard tile's trend.",
            )
        )

    # Checkpoints
    timed, requested = checkpoint_row if checkpoint_row else (0, 0)
    timed, requested = timed or 0, requested or 0
    checkpoint_sev = checkpoint_severity(timed, requested)
    total_checkpoints = timed + requested
    ratio_display = f"{100.0 * timed / total_checkpoints:.0f}%" if total_checkpoints else "—"
    categories.append(
        CategoryStatus(
            key="checkpoints",
            label="Checkpoints",
            severity=checkpoint_sev,
            value=ratio_display,
            detail="scheduled vs forced",
        )
    )
    if checkpoint_sev not in ("healthy", "unknown"):
        findings.append(
            Finding(
                id="checkpoints-forced",
                category="checkpoints",
                severity=checkpoint_sev,
                title="Checkpoints are mostly being forced, not scheduled",
                summary=(
                    f"{ratio_display} of checkpoints happened on schedule; the rest were forced by write "
                    "pressure, which can hurt write performance."
                ),
                detail=f"checkpoints_timed={timed} checkpoints_req={requested}",
                suggested_action="Consider raising max_wal_size — full config tuning support is coming soon.",
            )
        )

    # Wraparound — oldest table transaction age across the target
    worst_wrap = None
    for row in table_rows:
        xid_age = row[8]
        if worst_wrap is None or xid_age > worst_wrap[2]:
            worst_wrap = (row[0], row[1], xid_age)
    wrap_severity = wraparound_severity(worst_wrap[2]) if worst_wrap else "unknown"
    categories.append(
        CategoryStatus(
            key="wraparound",
            label="Wraparound",
            severity=wrap_severity,
            value=f"{worst_wrap[2]:,}" if worst_wrap else "—",
            detail="oldest transaction age",
        )
    )
    if worst_wrap and wrap_severity != "healthy":
        schema_name, table_name, xid_age = worst_wrap
        findings.append(
            Finding(
                id=f"wraparound-{schema_name}-{table_name}",
                category="wraparound",
                severity=wrap_severity,
                title=f"Table {schema_name}.{table_name} is approaching transaction ID wraparound",
                summary=f"This table's transaction age is {xid_age:,}, which is getting close to the safety limit.",
                detail=f"xid_age={xid_age}",
                suggested_action="Make sure autovacuum is running on this table; never disable autovacuum freeze.",
                schema_name=schema_name,
                table_name=table_name,
            )
        )

    # Autovacuum — a live worker count plus the staleness of the least-
    # recently-vacuumed table with live rows. Reuses table_rows (already
    # fetched above for Bloat/Wraparound) rather than a second table scan.
    cur.execute(AUTOVACUUM_RUNNING_QUERY)
    autovacuum_workers_running = cur.fetchone()[0]

    now = datetime.now(timezone.utc)
    worst_stale = None  # (schema_name, table_name, days_since_or_None, rank)
    for row in table_rows:
        live = row[2] or 0
        if live <= 0:
            continue
        last_vacuum_ts = row[5] or row[4]  # prefer autovacuum, fall back to manual vacuum
        days_since = (now - last_vacuum_ts).total_seconds() / 86400 if last_vacuum_ts else None
        rank = float("inf") if days_since is None else days_since
        if worst_stale is None or rank > worst_stale[3]:
            worst_stale = (row[0], row[1], days_since, rank)

    if worst_stale is None:
        autovacuum_severity, autovacuum_value, autovacuum_detail = "unknown", "—", "no tables with data yet"
    else:
        stale_schema, stale_table, days_since, _ = worst_stale
        autovacuum_severity = autovacuum_staleness_severity(days_since)
        autovacuum_value = "never" if days_since is None else f"{days_since:.0f}d"
        autovacuum_detail = (
            f"{stale_table} never vacuumed" if days_since is None else f"{stale_table} last vacuumed {days_since:.0f}d ago"
        )
    if autovacuum_workers_running:
        autovacuum_detail += f" · {autovacuum_workers_running} running now"

    categories.append(
        CategoryStatus(
            key="autovacuum", label="Autovacuum", severity=autovacuum_severity, value=autovacuum_value, detail=autovacuum_detail
        )
    )
    if worst_stale is not None and autovacuum_severity != "healthy":
        stale_schema, stale_table, days_since, _ = worst_stale
        findings.append(
            Finding(
                id=f"autovacuum-stale-{stale_schema}-{stale_table}",
                category="autovacuum",
                severity=autovacuum_severity,
                title=(
                    f"Table {stale_schema}.{stale_table} has never been vacuumed"
                    if days_since is None
                    else f"Table {stale_schema}.{stale_table} hasn't been vacuumed in {days_since:.0f} days"
                ),
                summary=(
                    (
                        f"{stale_table} has live rows but no vacuum — manual or automatic — has ever run on it."
                        if days_since is None
                        else f"It's been {days_since:.0f} days since {stale_table} was last vacuumed (autovacuum or manual)."
                    )
                    + " A table that goes too long without vacuuming accumulates dead rows and risks falling "
                    "behind on transaction ID freezing."
                ),
                detail=(
                    f"schema={stale_schema} table={stale_table} "
                    f"days_since_last_vacuum={'never' if days_since is None else f'{days_since:.1f}'} "
                    f"autovacuum_workers_running={autovacuum_workers_running}"
                ),
                suggested_action="Open Table Health and run Vacuum on this table, and confirm autovacuum is enabled for it.",
                schema_name=stale_schema,
                table_name=stale_table,
            )
        )

    # Replication and Backup & WAL Health — same finding-producing functions
    # the Advisor tabs use (app/replication_advisor.py, app/backup_advisor.py),
    # rolled into one tile each here. Filtered through advisor_archive so a
    # finding archived from the Advisor tab doesn't reappear on the Dashboard.
    archived_ids = get_archived_finding_ids(target_id)

    cur.execute(REPLICATION_STATUS_QUERY)
    replication_rows = cur.fetchall()
    replication_slot_rows = fetch_replication_slots(cur)
    replication_category, replication_findings = build_replication_status(replication_rows, replication_slot_rows)
    categories.append(replication_category)
    findings.extend(Finding(**f) for f in replication_findings if f["id"] not in archived_ids)

    cur.execute("SHOW archive_mode")
    archive_mode = cur.fetchone()[0]
    cur.execute(ARCHIVER_STATUS_QUERY)
    failed_count, last_archived_time, last_failed_time, last_failed_wal = cur.fetchone()
    cur.execute(WAL_DIR_SIZE_QUERY)
    wal_dir_bytes = cur.fetchone()[0]
    cur.execute(MAX_WAL_SIZE_QUERY)
    max_wal_size_bytes = cur.fetchone()[0]
    backup_category, backup_findings = build_backup_status(
        archive_mode, failed_count, last_archived_time, last_failed_time, last_failed_wal, wal_dir_bytes, max_wal_size_bytes
    )
    categories.append(backup_category)
    findings.extend(Finding(**f) for f in backup_findings if f["id"] not in archived_ids)

    # Pooler (PgBouncer) — optional, only present when configured for this target
    cur.execute("SELECT current_database()")
    current_dbname = cur.fetchone()[0]
    pooler_category, pooler_finding = _fetch_pooler_status(target_id, current_dbname)
    if pooler_category:
        categories.append(pooler_category)
    if pooler_finding:
        findings.append(pooler_finding)

    overall = "healthy"
    for category in categories:
        if category.severity != "unknown":
            overall = worse(overall, category.severity)

    return DashboardResponse(overall_severity=overall, categories=categories, findings=findings)
