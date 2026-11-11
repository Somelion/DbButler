import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from psycopg.types.json import Jsonb

from app.alerting import maybe_send_alert
from app.cache_hit import BLOCK_COUNTERS_QUERY, BlockCounters, interval_hit_ratio
from app.collectors import COLLECTORS
from app.config import settings
from app.crypto import decrypt
from app.db.store import store_conn
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.plan_fingerprint import build_explain_sql, build_fingerprint, hash_fingerprint, summarize_fingerprint
from app.query_history_delta import Baseline, QueryCounters, compute_interval
from app.routers.backup_advisor import compute_backup_advisor_findings
from app.routers.config_advisor import compute_config_advisor_findings
from app.routers.dashboard import compute_health
from app.routers.extension_advisor import compute_extension_advisor_findings
from app.routers.index_advisor import compute_index_advisor_findings
from app.routers.pre_upgrade_advisor import compute_pre_upgrade_advisor_findings
from app.routers.replication_advisor import compute_replication_advisor_findings
from app.routers.schema_lint import compute_schema_lint_findings
from app.routers.security_advisor import compute_security_advisor_findings
from app.routers.table_health import compute_all_table_health_findings
from app.routers.wait_events import WAIT_EVENT_SNAPSHOT_QUERY
from app.schemas import IndexFinding
from app.target_backoff import TargetBackoff
from app.target_conn import open_target_connection

logger = logging.getLogger(__name__)

# One row per user table per cycle into table_stat_snapshots. The counters
# beyond dead_pct/total_bytes aren't charted yet; they're the history later
# workload features need (scans, writes and I/O per table over time).
TABLE_METRICS_QUERY = """
    SELECT
        s.schemaname,
        s.relname,
        CASE WHEN (s.n_live_tup + s.n_dead_tup) > 0
             THEN 100.0 * s.n_dead_tup / (s.n_live_tup + s.n_dead_tup)
             ELSE 0 END AS dead_pct,
        pg_total_relation_size(c.oid) AS total_bytes,
        s.n_live_tup,
        s.n_dead_tup,
        s.n_mod_since_analyze,
        s.seq_scan,
        s.seq_tup_read,
        s.idx_scan,
        s.idx_tup_fetch,
        s.n_tup_ins,
        s.n_tup_upd,
        s.n_tup_del,
        s.n_tup_hot_upd,
        io.heap_blks_read,
        io.heap_blks_hit
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    LEFT JOIN pg_statio_user_tables io ON io.relid = s.relid
"""

TABLE_SNAPSHOT_INSERT = """
    INSERT INTO table_stat_snapshots (
        target_id, schema_name, table_name, dead_pct, total_bytes, n_live_tup, n_dead_tup,
        n_mod_since_analyze, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch, n_tup_ins,
        n_tup_upd, n_tup_del, n_tup_hot_upd, heap_blks_read, heap_blks_hit
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

INDEX_METRICS_QUERY = """
    SELECT schemaname, relname, indexrelname, idx_scan, idx_tup_read, idx_tup_fetch,
           pg_relation_size(indexrelid)
    FROM pg_stat_user_indexes
"""

INDEX_SNAPSHOT_INSERT = """
    INSERT INTO index_stat_snapshots (
        target_id, schema_name, table_name, index_name, idx_scan, idx_tup_read, idx_tup_fetch, size_bytes
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

# Top N by total_exec_time, same reasoning as pg_stat_statements.py's
# TOP_QUERIES_QUERY and Schema Lint/Index Advisor's own pg_stat_statements
# reads, just narrower (50 vs. 200) since these rows get persisted every
# cycle rather than read once — distinct queryids aren't naturally bounded
# the way table count is, so this caps Query History's storage growth by
# design.
#
# pg_stat_statements keys its rows by (userid, dbid, queryid, toplevel), so
# one queryid can have several rows (run by different roles, or both
# directly and from inside a function). They're summed into one row per
# queryid here: diffing one of them against a baseline taken from another
# produced false "reset" gaps and garbage deltas. The text shown is the one
# from the heaviest row.
QUERY_HISTORY_QUERY = """
    SELECT
        pss.queryid,
        (array_agg(pss.query ORDER BY pss.total_exec_time DESC))[1] AS query,
        sum(pss.calls)::bigint AS calls,
        sum(pss.total_exec_time)::float8 AS total_exec_time,
        sum(pss.rows)::bigint AS rows
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database() AND pss.queryid IS NOT NULL
    GROUP BY pss.queryid
    ORDER BY sum(pss.total_exec_time) DESC
    LIMIT %s
"""
TOP_N_TRACKED_QUERIES = 50

# How long history stays in the store before run_retention_cycle prunes it.
# No hourly/daily rollups yet (see docs) — this is the simple safeguard until
# chart volume at longer ranges justifies one.
RETENTION_DAYS = 30
# collector_runs is an operational self-log (nothing in the UI reads it); a
# week is plenty to see why a collector has been failing.
COLLECTOR_RUNS_RETENTION_DAYS = 7
# hardware_profiles is append-only on every save from the Hardware Profile
# screen; the newest one per target is "current", a few older ones are kept
# as history.
HARDWARE_PROFILES_KEEP = 20

# Every store table run_retention_cycle prunes, in the order it runs them.
# %(days)s is the table's retention; a statement with %(target_id)s runs once
# per target so its delete uses the (target_id, collected_at) index instead
# of scanning the whole table. Tables deliberately never pruned are listed in
# RETENTION_EXEMPT_TABLES (tests check every store table is in one or the
# other).
RETENTION_RULES: list[tuple[str, str, int]] = [
    (
        "metric_points",
        "DELETE FROM metric_points WHERE collected_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "table_stat_snapshots",
        "DELETE FROM table_stat_snapshots WHERE target_id = %(target_id)s "
        "AND collected_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "index_stat_snapshots",
        "DELETE FROM index_stat_snapshots WHERE target_id = %(target_id)s "
        "AND collected_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "query_stat_snapshots",
        "DELETE FROM query_stat_snapshots WHERE collected_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "query_plan_snapshots",
        "DELETE FROM query_plan_snapshots WHERE collected_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    # A query that's aged out of the top TOP_N_TRACKED_QUERIES and hasn't
    # resurfaced in RETENTION_DAYS is as good as forgotten — cascades its
    # (already-pruned-or-not) snapshots too.
    (
        "tracked_queries",
        "DELETE FROM tracked_queries WHERE last_seen_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "collector_runs",
        "DELETE FROM collector_runs WHERE finished_at < now() - (%(days)s * interval '1 day')",
        COLLECTOR_RUNS_RETENTION_DAYS,
    ),
    # The newest run per (target, category) is always kept: Table Health
    # shows "last deep scan" from it even if the nightly job has been off
    # for longer than the retention window.
    (
        "deep_scan_runs",
        "DELETE FROM deep_scan_runs d WHERE d.finished_at < now() - (%(days)s * interval '1 day') "
        "AND EXISTS (SELECT 1 FROM deep_scan_runs n WHERE n.target_id = d.target_id "
        "AND n.category = d.category AND n.finished_at > d.finished_at)",
        RETENTION_DAYS,
    ),
    # Open findings are never pruned, however old. A resolved one that comes
    # back after being pruned is reported as new, the same as a reopened one.
    (
        "deep_scan_findings",
        "DELETE FROM deep_scan_findings WHERE status = 'resolved' "
        "AND resolved_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    # These two hold real query text, plans and AI responses — not kept
    # longer than the rest of the history.
    (
        "query_analyses",
        "DELETE FROM query_analyses WHERE created_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "index_coverage_analyses",
        "DELETE FROM index_coverage_analyses WHERE created_at < now() - (%(days)s * interval '1 day')",
        RETENTION_DAYS,
    ),
    (
        "hardware_profiles",
        "DELETE FROM hardware_profiles WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER (PARTITION BY target_id ORDER BY created_at DESC) AS rn "
        "FROM hardware_profiles) ranked WHERE rn > %(days)s)",
        HARDWARE_PROFILES_KEEP,
    ),
]

# Configuration and things a user created on purpose — never pruned by age.
RETENTION_EXEMPT_TABLES = frozenset(
    {
        "targets",
        "pgbouncer_connections",
        "archived_findings",
        "tracked_indexes",
        "scheduler_jobs",
        "ai_settings",
        "alert_settings",
        "dashboard_settings",
        "schema_migrations",
    }
)

# Every job's cadence now lives in the scheduler_jobs table (editable from the
# Settings screen, hot-swapped in below via reconcile()) rather than as a
# module constant — see docs/DATA_MODEL.md. These two module-level bits of
# state exist only to make that hotswap efficient/safe:
#   _scheduler                — set once in start_scheduler(), so a router
#                                (routers/scheduler_settings.py) can trigger a
#                                reconcile() without the instance being
#                                threaded through FastAPI dependency injection.
#   _last_applied_signatures   — the (job_kind, interval_seconds, cron_expr,
#                                enabled) tuple most recently applied to the
#                                live scheduler for each job_name. reconcile()
#                                only touches a job whose signature actually
#                                changed (or that's missing live) — see its
#                                docstring for why an unconditional
#                                add_job(replace_existing=True) on every tick
#                                would be a bug, not just extra work.
_scheduler: BackgroundScheduler | None = None
_last_applied_signatures: dict[str, tuple] = {}

# Targets whose connect failed recently are skipped by the frequent jobs for
# a while (app/target_backoff.py). The nightly deep scan ignores it.
_backoff = TargetBackoff()

# The previous pg_stat_database block-counter sample per target, for the
# interval cache hit rate (app/cache_hit.py). In memory only: after a
# restart the first sample just starts a new interval.
_cache_baselines: dict = {}
_cache_baselines_lock = threading.Lock()

RECONCILE_SELF_HEAL_MINUTES = 5

# Jobs whose very first scheduling (process startup, nothing live yet) should
# fire immediately rather than waiting a full interval — matches this app's
# original startup behavior before cadence moved into the database. Deep scan
# and retention are cheap to leave on their normal cadence from a cold start.
IMMEDIATE_ON_FIRST_SCHEDULE = {
    "collection_cycle",
    "table_metrics_cycle",
    "query_history_cycle",
    "plan_regression_cycle",
    "live_findings_cycle",
}

# Every category routers/dashboard.py::compute_health can emit a Finding
# under — reconciled explicitly by name (not inferred from whichever
# categories happen to appear in a given cycle's findings) so a category
# that's gone healthy again still gets its stale open finding resolved; see
# _collect_live_findings below. "pooler" only ever appears for a target with
# a PgBouncer connection configured, "query intelligence" only for the
# always-possible pg_stat_statements-not-installed finding — both are still
# safe to reconcile unconditionally even when absent every cycle.
#
# "replication advisor" and "backup advisor" also appear in DEEP_SCAN_CHECKS
# below (compute_health calls the exact same finding functions those two
# Advisor tabs use — see app/replication_advisor.py::build_replication_status,
# app/backup_advisor.py::build_backup_status). Reconciling them from both the
# fast live cycle (this one, default 60s) and the nightly deep scan is
# intentional, not a duplicate-write bug: both sources always compute the
# same finding set for the same target, so this just means a broken replica
# or failed WAL archiving alerts within a minute instead of waiting for the
# next nightly run.
DASHBOARD_FINDING_CATEGORIES = [
    "connections",
    "locks",
    "bloat",
    "cache",
    "checkpoints",
    "wraparound",
    "autovacuum",
    "xmin horizon",
    "replication advisor",
    "backup advisor",
    "pooler",
    "query intelligence",
]

# "table health" is deliberately absent here — compute_all_table_health_findings
# needs target_id (its growth-forecast check reads pgdba-store's own
# table_stat_snapshots, not just the target via cur), unlike the other
# categories' compute_fn(cur)-only signature, so it's added separately in
# _deep_scan_target/run_table_health_deep_scan_now below via
# _table_health_compute_fn rather than forcing an unused target_id parameter
# onto every other category.
DEEP_SCAN_CHECKS = [
    ("index advisor", compute_index_advisor_findings),
    ("schema lint", compute_schema_lint_findings),
    ("configuration advisor", compute_config_advisor_findings),
    ("replication advisor", compute_replication_advisor_findings),
    ("backup advisor", compute_backup_advisor_findings),
    ("pre-upgrade advisor", compute_pre_upgrade_advisor_findings),
    ("security advisor", compute_security_advisor_findings),
    ("extension advisor", compute_extension_advisor_findings),
]


def _active_targets(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, host, port, dbname, username, encrypted_password, sslmode "
            "FROM targets WHERE is_active"
        )
        return cur.fetchall()


def _connect_target(target_row, autocommit: bool = True) -> psycopg.Connection:
    """Same session limits (statement/lock/idle timeouts) and
    application_name as every request-path connection — see
    app/target_conn.py::open_target_connection. Autocommit by default: the
    scheduler only reads, so there's no transaction worth holding open, and
    one failed statement doesn't abort the rest of the cycle's queries."""
    _target_id, host, port, dbname, username, enc_password, sslmode = target_row
    return open_target_connection(
        host, port, dbname, username, decrypt(enc_password), sslmode, autocommit=autocommit
    )


@contextmanager
def _target_session(target_row):
    """One autocommit connection for one target's whole cycle, feeding the
    connect backoff: a failed connect pushes the target's next attempt out,
    a successful one clears it."""
    target_id = target_row[0]
    try:
        conn = _connect_target(target_row)
    except psycopg.OperationalError:
        delay = _backoff.record_failure(target_id)
        logger.info("target %s unreachable; scheduler jobs skip it for %ss", target_id, delay)
        raise
    _backoff.record_success(target_id)
    with conn:
        yield conn


def _target_label(target_row) -> str:
    """A human-readable identifier for alert messages (app/alerting.py) —
    the target's own friendly `name` isn't in _active_targets'/_run_deep_
    scan_category's target_row tuple, so host/dbname stands in instead."""
    _target_id, host, _port, dbname, *_rest = target_row
    return f"{host}/{dbname}"


def _record_collector_run(cur, target_id, job_name: str, started_at, errors: list[str]) -> None:
    cur.execute(
        """
        INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (target_id, job_name, started_at, "error" if errors else "ok", "; ".join(errors) or None),
    )


def _run_query(target_conn, cur, label: str, errors: list[str], query: str, params=None) -> list | None:
    """Runs one read on an autocommit connection, recording a failure in
    `errors` and carrying on with the next query. A dropped connection isn't
    worth carrying on with, so that's re-raised."""
    try:
        cur.execute(query, params)
        return cur.fetchall()
    except psycopg.Error as exc:
        if target_conn.broken:
            raise
        errors.append(f"{label}: {exc}")
        logger.warning("%s failed on %s/%s: %s", label, target_conn.info.host, target_conn.info.dbname, exc)
        return None


def _sample_cache_hit_ratio(target_id, counters_row) -> float | None:
    if counters_row is None:
        return None
    current = BlockCounters(*counters_row)
    with _cache_baselines_lock:
        ratio, baseline = interval_hit_ratio(_cache_baselines.get(target_id), current)
        _cache_baselines[target_id] = baseline
    return ratio


def _collect_point_metrics(target_row):
    """The single-value COLLECTORS plus the interval cache hit rate, all on
    one connection, into metric_points."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []
    values: list[tuple[str, float]] = []

    try:
        with _target_session(target_row) as target_conn, target_conn.cursor() as cur:
            for collector in COLLECTORS:
                rows = _run_query(target_conn, cur, collector.name, errors, collector.query)
                if rows and rows[0][0] is not None:
                    values.append((collector.metric_name, rows[0][0]))
            rows = _run_query(target_conn, cur, "cache_hit_ratio", errors, BLOCK_COUNTERS_QUERY)
            ratio = _sample_cache_hit_ratio(target_id, rows[0] if rows else None)
            if ratio is not None:
                values.append(("cache_hit_ratio", ratio))
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("collection cycle failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        if values:
            cur.executemany(
                "INSERT INTO metric_points (target_id, metric_name, value) VALUES (%s, %s, %s)",
                [(target_id, metric_name, value) for metric_name, value in values],
            )
        _record_collector_run(cur, target_id, "collection", started_at, errors)
        conn.commit()


def _collect_table_metrics(target_row):
    """Per-table and per-index snapshots plus the wait-event breakdown, on
    one connection. The wait events (WAIT_EVENT_SNAPSHOT_QUERY, shared with
    the live endpoint in routers/wait_events.py) feed the Trends screen's
    Wait Events history; they ride along on this job's cadence (5 min by
    default) since, like the table snapshots, they're "moderate cardinality,
    moderate cadence" detail rather than the 30s single-value COLLECTORS."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []
    table_rows = index_rows = wait_rows = None

    try:
        with _target_session(target_row) as target_conn, target_conn.cursor() as cur:
            table_rows = _run_query(target_conn, cur, "table_metrics", errors, TABLE_METRICS_QUERY)
            index_rows = _run_query(target_conn, cur, "index_metrics", errors, INDEX_METRICS_QUERY)
            wait_rows = _run_query(target_conn, cur, "wait_events", errors, WAIT_EVENT_SNAPSHOT_QUERY)
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("table metrics collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        if table_rows:
            cur.executemany(TABLE_SNAPSHOT_INSERT, [(target_id, *row) for row in table_rows])
        if index_rows:
            cur.executemany(INDEX_SNAPSHOT_INSERT, [(target_id, *row) for row in index_rows])
        if wait_rows:
            cur.executemany(
                "INSERT INTO metric_points (target_id, metric_name, value, labels) VALUES (%s, %s, %s, %s)",
                [(target_id, "wait_event_count", count, Jsonb({"category": category})) for category, count in wait_rows],
            )
        _record_collector_run(cur, target_id, "table_metrics", started_at, errors)
        conn.commit()


def _collect_query_history(target_row):
    """Reads the top TOP_N_TRACKED_QUERIES queryids and turns each one's
    cumulative counters into this interval's delta against the baseline
    recorded in tracked_queries — the rules for a first sighting, a reset, a
    baseline from an older version and an idle interval are in
    app/query_history_delta.py::compute_interval."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []
    rows = []

    try:
        with _target_session(target_row) as target_conn, target_conn.cursor() as cur:
            if pg_stat_statements_enabled(cur):
                rows = _run_query(
                    target_conn, cur, "query_history", errors, QUERY_HISTORY_QUERY, (TOP_N_TRACKED_QUERIES,)
                ) or []
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("query history collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        baselines = {}
        if rows:
            cur.execute(
                """
                SELECT queryid, id, last_calls, last_total_exec_ms, last_rows
                FROM tracked_queries WHERE target_id = %s AND queryid = ANY(%s)
                """,
                (target_id, [row[0] for row in rows]),
            )
            baselines = {
                queryid: (tracked_id, Baseline(last_calls, last_total_exec_ms, last_rows))
                for queryid, tracked_id, last_calls, last_total_exec_ms, last_rows in cur.fetchall()
            }

        for queryid, query_text, calls, total_exec_time, row_count in rows:
            current = QueryCounters(calls or 0, total_exec_time or 0.0, row_count or 0)
            existing = baselines.get(queryid)
            if existing is None:
                cur.execute(
                    """
                    INSERT INTO tracked_queries (target_id, queryid, query_text, last_calls, last_total_exec_ms, last_rows)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (target_id, queryid, query_text, *current),
                )
                continue

            tracked_id, baseline = existing
            interval = compute_interval(baseline, current)
            cur.execute(
                """
                UPDATE tracked_queries
                SET query_text = %s, last_calls = %s, last_total_exec_ms = %s, last_rows = %s, last_seen_at = now()
                WHERE id = %s
                """,
                (query_text, *current, tracked_id),
            )
            if interval is not None:
                cur.execute(
                    """
                    INSERT INTO query_stat_snapshots (tracked_query_id, calls, mean_exec_ms, rows)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (tracked_id, *interval),
                )

        _record_collector_run(cur, target_id, "query_history", started_at, errors)
        conn.commit()


def _capture_query_plans(target_row):
    """For each of this target's already-tracked queries (Query History's
    top TOP_N_TRACKED_QUERIES), runs a non-executing EXPLAIN
    (app/plan_fingerprint.py::build_explain_sql) and stores a new
    query_plan_snapshots row only when the plan's structural fingerprint
    actually changed since the last capture — the raw material
    routers/plan_regressions.py compares against query_stat_snapshots'
    latency history to flag a regression. Reads tracked_queries and writes
    query_plan_snapshots as two separate store_conn passes around the target
    EXPLAIN work, same shape as _collect_query_history's target-then-store
    ordering. Each EXPLAIN is its own autocommit statement, so the locks it
    takes for planning are released straight away rather than held until all
    of them are done."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    errors: list[str] = []

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, query_text FROM tracked_queries WHERE target_id = %s", (target_id,))
        tracked = cur.fetchall()

    captures = []

    try:
        with _target_session(target_row) as target_conn, target_conn.cursor() as cur:
            cur.execute("SHOW server_version_num")
            version_num = int(cur.fetchone()[0])

            for tracked_id, query_text in tracked:
                explain_sql = build_explain_sql(query_text, version_num)
                if explain_sql is None:
                    continue

                try:
                    cur.execute(explain_sql)
                    plan_payload = cur.fetchone()[0]
                except psycopg.Error:
                    # Not every pg_stat_statements entry is EXPLAIN-able (a
                    # utility statement, or query text that no longer parses
                    # standalone) — skip it. Autocommit means the failure
                    # doesn't affect the next EXPLAIN, unless the connection
                    # itself is gone.
                    if target_conn.broken:
                        raise
                    continue

                plan_list = plan_payload if isinstance(plan_payload, list) else json.loads(plan_payload)
                fingerprint = build_fingerprint(plan_list[0]["Plan"])
                captures.append(
                    (tracked_id, hash_fingerprint(fingerprint), summarize_fingerprint(fingerprint), fingerprint)
                )
    except Exception as exc:
        errors.append(str(exc))
        logger.warning("plan regression collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        for tracked_id, fingerprint_hash, plan_summary, fingerprint in captures:
            cur.execute(
                """
                SELECT fingerprint_hash FROM query_plan_snapshots
                WHERE tracked_query_id = %s ORDER BY collected_at DESC LIMIT 1
                """,
                (tracked_id,),
            )
            last = cur.fetchone()
            if last is not None and last[0] == fingerprint_hash:
                continue
            cur.execute(
                """
                INSERT INTO query_plan_snapshots (tracked_query_id, fingerprint_hash, plan_summary, plan_json)
                VALUES (%s, %s, %s, %s)
                """,
                (tracked_id, fingerprint_hash, plan_summary, Jsonb(fingerprint)),
            )
        _record_collector_run(cur, target_id, "plan_regression", started_at, errors)
        conn.commit()


def _run_guarded(fn: Callable, target_row) -> None:
    try:
        fn(target_row)
    except Exception:
        # Typically the store write after a target read. One target's failure
        # must not stop the others.
        logger.exception("%s failed for target %s", fn.__name__, target_row[0])


def _for_each_target(fn: Callable, *, respect_backoff: bool = True) -> None:
    """Runs fn(target_row) for every active target, up to
    settings.scheduler_max_parallel_targets at a time, so one slow or
    unreachable target no longer delays every other target's collection.
    Targets in connect backoff are skipped unless respect_backoff is False."""
    with store_conn() as conn:
        targets = _active_targets(conn)
    if respect_backoff:
        targets = [target_row for target_row in targets if _backoff.should_attempt(target_row[0])]
    if not targets:
        return
    if len(targets) == 1:
        _run_guarded(fn, targets[0])
        return
    workers = max(1, min(settings.scheduler_max_parallel_targets, len(targets)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=fn.__name__) as pool:
        for target_row in targets:
            pool.submit(_run_guarded, fn, target_row)


def run_collection_cycle():
    _for_each_target(_collect_point_metrics)


def run_table_metrics_cycle():
    _for_each_target(_collect_table_metrics)


def run_query_history_cycle():
    _for_each_target(_collect_query_history)


def run_plan_regression_cycle():
    _for_each_target(_capture_query_plans)


def run_retention_cycle():
    """Applies RETENTION_RULES, each in its own short transaction so one
    failing delete doesn't undo or block the others."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM targets")
        target_ids = [row[0] for row in cur.fetchall()]

    for table, statement, days in RETENTION_RULES:
        param_sets = (
            [{"days": days, "target_id": target_id} for target_id in target_ids]
            if "%(target_id)s" in statement
            else [{"days": days}]
        )
        try:
            with store_conn() as conn, conn.cursor() as cur:
                for params in param_sets:
                    cur.execute(statement, params)
                conn.commit()
        except psycopg.Error as exc:
            logger.warning("retention for %s failed: %s", table, exc)


def _reconcile_category_findings(cur, target_id, category: str, findings: list[dict], run_status: str) -> list[dict]:
    """Upserts already-validated finding dicts into deep_scan_findings for
    one (target_id, category) pair and resolves whatever's no longer
    reported, but ONLY when run_status == "ok" — a query failure must never
    silently read as "problem's gone," so that category's existing findings
    are left untouched until a future run actually confirms them clear.

    Returns whichever findings are newly open this run — never seen before,
    or reopened after having previously been resolved — the raw material
    for Actionable Alerting (app/alerting.py::maybe_send_alert), which only
    wants to hear about a problem once when it first appears, not every
    cycle it stays open. Shared by _run_deep_scan_category (one category per
    call, nightly-cadence Advisor checks) and _collect_live_findings
    (several categories per call, since compute_health's checks span many
    at once, on a much faster cadence)."""
    newly_open = []
    for finding in findings:
        cur.execute(
            "SELECT status FROM deep_scan_findings WHERE target_id = %s AND finding_id = %s",
            (target_id, finding["id"]),
        )
        prior = cur.fetchone()
        if prior is None or prior[0] == "resolved":
            newly_open.append(finding)

        cur.execute(
            """
            INSERT INTO deep_scan_findings (target_id, finding_id, category, finding)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (target_id, finding_id) DO UPDATE
                SET finding = EXCLUDED.finding, status = 'open',
                    last_seen_at = now(), resolved_at = NULL
            """,
            (target_id, finding["id"], category, Jsonb(finding)),
        )

    if run_status == "ok":
        seen_ids = [finding["id"] for finding in findings]
        if seen_ids:
            cur.execute(
                """
                UPDATE deep_scan_findings
                SET status = 'resolved', resolved_at = now()
                WHERE target_id = %s AND category = %s AND status = 'open'
                  AND NOT (finding_id = ANY(%s))
                """,
                (target_id, category, seen_ids),
            )
        else:
            cur.execute(
                """
                UPDATE deep_scan_findings
                SET status = 'resolved', resolved_at = now()
                WHERE target_id = %s AND category = %s AND status = 'open'
                """,
                (target_id, category),
            )

    return newly_open


def _record_deep_scan_result(target_row, category: str, started_at, status: str, error, findings: list[dict]) -> None:
    target_id = target_row[0]
    with store_conn() as conn, conn.cursor() as cur:
        newly_open = _reconcile_category_findings(cur, target_id, category, findings, status)
        cur.execute(
            """
            INSERT INTO deep_scan_runs (target_id, category, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, category, started_at, status, error),
        )
        conn.commit()

    maybe_send_alert(target_id, _target_label(target_row), newly_open)


def _run_deep_scan_category(target_row, category: str, compute_fn, target_conn: psycopg.Connection) -> None:
    """Runs one Advisor category's checks on an already-open target
    connection and reconciles the result into deep_scan_findings (see
    _reconcile_category_findings)."""
    started_at = datetime.now(timezone.utc)
    status, error, findings = "ok", None, []

    try:
        with target_conn.cursor() as cur:
            findings = compute_fn(cur)
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("deep scan (%s) failed for target %s: %s", category, target_row[0], exc)

    validated = [IndexFinding(**finding).model_dump(mode="json") for finding in findings]
    _record_deep_scan_result(target_row, category, started_at, status, error, validated)


def _table_health_compute_fn(target_id):
    """Binds target_id into a plain compute_fn(cur) closure so
    _run_deep_scan_category's generic single-argument call still works
    unchanged for this one category — see DEEP_SCAN_CHECKS' comment above."""
    return lambda cur: compute_all_table_health_findings(cur, target_id)


def _deep_scan_checks(target_id) -> list:
    return [*DEEP_SCAN_CHECKS, ("table health", _table_health_compute_fn(target_id))]


def _run_deep_scan_checks(target_row, checks) -> None:
    """Every category in `checks` for one target on one autocommit
    connection (a failing category doesn't abort the next one). If the
    target can't be reached, each category is recorded as failed, so its
    open findings stay open rather than reading as resolved."""
    started_at = datetime.now(timezone.utc)
    try:
        target_conn = _connect_target(target_row)
    except Exception as exc:
        logger.warning("deep scan could not connect to target %s: %s", target_row[0], exc)
        for category, _compute_fn in checks:
            _record_deep_scan_result(target_row, category, started_at, "error", str(exc), [])
        return
    with target_conn:
        for category, compute_fn in checks:
            _run_deep_scan_category(target_row, category, compute_fn, target_conn)


def _deep_scan_target(target_row) -> None:
    _run_deep_scan_checks(target_row, _deep_scan_checks(target_row[0]))


def run_deep_scan_cycle():
    # Nightly: tried for every target even if it's in connect backoff — a
    # brief outage at the wrong moment shouldn't cost a whole night's scan.
    _for_each_target(_deep_scan_target, respect_backoff=False)


def run_table_health_deep_scan_now(target_id) -> None:
    """Manual trigger for Table Health's "Run Deep Scan Now" button — runs
    only the table-health category, immediately, for one target, via the
    exact same per-category runner the nightly job uses, so results land in
    the same deep_scan_findings/deep_scan_runs history the cron job writes
    to (the router reads that history back afterward to build its response)."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, host, port, dbname, username, encrypted_password, sslmode FROM targets WHERE id = %s",
            (target_id,),
        )
        target_row = cur.fetchone()
    if target_row is None:
        raise ValueError("Target not found")
    _run_deep_scan_checks(target_row, [("table health", _table_health_compute_fn(target_id))])


def _collect_live_findings(target_row):
    """Runs compute_health (the same live checks the Dashboard and Diagnose
    Now screens show) and reconciles the result into deep_scan_findings —
    the persisted findings history Actionable Alerting needs to tell "a
    problem just started" from "still the same problem as last cycle" —
    on a much faster cadence than the nightly deep scan (default 60s vs.
    once a night). Loops DASHBOARD_FINDING_CATEGORIES explicitly rather
    than only categories present in this cycle's findings, so a category
    that's gone healthy again still gets its stale open finding resolved;
    see _reconcile_category_findings."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, findings = "ok", None, []

    try:
        with _target_session(target_row) as target_conn, target_conn.cursor() as cur:
            findings = [f.model_dump(mode="json") for f in compute_health(cur, target_id).findings]
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("live findings collector failed for target %s: %s", target_id, exc)

    by_category: dict[str, list[dict]] = {}
    for finding in findings:
        by_category.setdefault(finding["category"], []).append(finding)

    newly_open = []
    with store_conn() as conn, conn.cursor() as cur:
        for category in DASHBOARD_FINDING_CATEGORIES:
            newly_open += _reconcile_category_findings(cur, target_id, category, by_category.get(category, []), status)

        cur.execute(
            """
            INSERT INTO deep_scan_runs (target_id, category, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, "dashboard", started_at, status, error),
        )
        conn.commit()

    maybe_send_alert(target_id, _target_label(target_row), newly_open)


def run_live_findings_cycle():
    _for_each_target(_collect_live_findings)


_JOB_FUNCS = {
    "collection_cycle": run_collection_cycle,
    "table_metrics_cycle": run_table_metrics_cycle,
    "query_history_cycle": run_query_history_cycle,
    "plan_regression_cycle": run_plan_regression_cycle,
    "live_findings_cycle": run_live_findings_cycle,
    "retention_cycle": run_retention_cycle,
    "deep_scan": run_deep_scan_cycle,
}

# Public so routers/scheduler_settings.py can validate a job_name without
# reaching into the private job->callable map above.
KNOWN_JOB_NAMES = frozenset(_JOB_FUNCS)


def _build_trigger(job_kind: str, interval_seconds: int | None, cron_expr: str | None):
    if job_kind == "interval":
        return IntervalTrigger(seconds=interval_seconds)
    return CronTrigger.from_crontab(cron_expr)


def reconcile() -> None:
    """Re-reads scheduler_jobs and applies any changed row to the live
    APScheduler instance via add_job(id=job_name, replace_existing=True) —
    the hotswap: routers/scheduler_settings.py calls this synchronously right
    after writing a new interval/cron, so the change takes effect without a
    backend restart. Also run once at startup and on a background self-heal
    interval (RECONCILE_SELF_HEAL_MINUTES) in case a direct call is ever
    missed.

    Only a job whose (job_kind, interval_seconds, cron_expr, enabled)
    signature actually changed — or that's missing from the live scheduler —
    gets re-added. An unconditional add_job(replace_existing=True) on every
    row, every self-heal tick, would be a real bug: for an *interval*
    trigger, replace_existing recomputes next_run_time as now + interval at
    the moment add_job runs, so an unchanged job (e.g. hourly retention)
    would have its next run pushed further into the future on every self-heal
    pass and would never actually fire once self-heal is running. Cron
    triggers aren't affected (they always resolve to the same wall-clock
    occurrence regardless of when add_job is called), but interval jobs are.
    """
    if _scheduler is None:
        return

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT job_name, job_kind, interval_seconds, cron_expr, enabled FROM scheduler_jobs")
        rows = cur.fetchall()

    live_job_ids = {job.id for job in _scheduler.get_jobs()}

    for job_name, job_kind, interval_seconds, cron_expr, enabled in rows:
        func = _JOB_FUNCS.get(job_name)
        if func is None:
            continue

        signature = (job_kind, interval_seconds, cron_expr, enabled)
        is_live = job_name in live_job_ids
        if is_live and _last_applied_signatures.get(job_name) == signature:
            continue

        if not enabled:
            if is_live:
                _scheduler.remove_job(job_name)
            _last_applied_signatures[job_name] = signature
            continue

        trigger = _build_trigger(job_kind, interval_seconds, cron_expr)
        add_job_kwargs = {}
        if not is_live and job_name in IMMEDIATE_ON_FIRST_SCHEDULE:
            add_job_kwargs["next_run_time"] = datetime.now(timezone.utc)
        _scheduler.add_job(func, trigger, id=job_name, replace_existing=True, **add_job_kwargs)
        _last_applied_signatures[job_name] = signature


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    # A run that's still going when the next one is due is skipped
    # (max_instances=1), and several missed runs collapse into one
    # (coalesce) instead of firing back to back.
    _scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})
    _scheduler.start()

    reconcile()
    _scheduler.add_job(
        reconcile,
        "interval",
        minutes=RECONCILE_SELF_HEAL_MINUTES,
        id="reconcile_self_heal",
    )

    return _scheduler
