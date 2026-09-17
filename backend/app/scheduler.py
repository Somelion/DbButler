import json
import logging
from datetime import datetime, timezone

import psycopg
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from psycopg.types.json import Jsonb

from app.alerting import maybe_send_alert
from app.collectors import COLLECTORS
from app.crypto import decrypt
from app.db.store import store_conn
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.plan_fingerprint import build_explain_sql, build_fingerprint, hash_fingerprint, summarize_fingerprint
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

logger = logging.getLogger(__name__)

TABLE_METRICS_QUERY = """
    SELECT
        s.schemaname,
        s.relname,
        CASE WHEN (s.n_live_tup + s.n_dead_tup) > 0
             THEN 100.0 * s.n_dead_tup / (s.n_live_tup + s.n_dead_tup)
             ELSE 0 END AS dead_pct,
        pg_total_relation_size(c.oid) AS total_bytes
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
"""

# Top N by total_exec_time, same reasoning as pg_stat_statements.py's
# TOP_QUERIES_QUERY and Schema Lint/Index Advisor's own pg_stat_statements
# reads, just narrower (50 vs. 200) since these rows get persisted every
# cycle rather than read once — distinct queryids aren't naturally bounded
# the way table count is, so this caps Query History's storage growth by
# design. queryid is the stable identity pg_stat_statements itself uses.
QUERY_HISTORY_QUERY = """
    SELECT pss.queryid, pss.query, pss.calls, pss.total_exec_time, pss.rows
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.total_exec_time DESC
    LIMIT %s
"""
TOP_N_TRACKED_QUERIES = 50

# How long raw samples stay in metric_points/query_stat_snapshots before
# Trends'/Query History's history views just keep growing forever. No
# hourly/daily rollups yet (see docs) — this is the simple safeguard until
# chart volume at longer ranges justifies one.
RETENTION_DAYS = 30

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
    "replication advisor",
    "backup advisor",
    "pooler",
    "query intelligence",
]

# "table health" is deliberately absent here — compute_all_table_health_findings
# needs target_id (its growth-forecast check reads pgdba-store's own
# metric_points, not just the target via cur), unlike the other three
# categories' compute_fn(cur)-only signature, so it's wired up separately in
# run_deep_scan_cycle/run_table_health_deep_scan_now below via
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


def _connect_target(target_row) -> psycopg.Connection:
    _target_id, host, port, dbname, username, enc_password, sslmode = target_row
    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=username,
        password=decrypt(enc_password),
        sslmode=sslmode,
        connect_timeout=5,
    )


def _target_label(target_row) -> str:
    """A human-readable identifier for alert messages (app/alerting.py) —
    the target's own friendly `name` isn't in _active_targets'/_run_deep_
    scan_category's target_row tuple, so host/dbname stands in instead."""
    _target_id, host, _port, dbname, *_rest = target_row
    return f"{host}/{dbname}"


def _run_collector(target_row, collector):
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, value = "ok", None, None

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
            cur.execute(collector.query)
            row = cur.fetchone()
            value = row[0] if row else None
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("collector %s failed for target %s: %s", collector.name, target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        if value is not None:
            cur.execute(
                "INSERT INTO metric_points (target_id, metric_name, value) VALUES (%s, %s, %s)",
                (target_id, collector.metric_name, value),
            )
        cur.execute(
            """
            INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, collector.name, started_at, status, error),
        )
        conn.commit()


def _collect_table_metrics(target_row):
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, rows = "ok", None, []

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
            cur.execute(TABLE_METRICS_QUERY)
            rows = cur.fetchall()
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("table metrics collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        for schema_name, table_name, dead_pct, total_bytes in rows:
            labels = Jsonb({"schema": schema_name, "table": table_name})
            cur.execute(
                "INSERT INTO metric_points (target_id, metric_name, value, labels) VALUES (%s, %s, %s, %s)",
                (target_id, "table_dead_pct", dead_pct, labels),
            )
            cur.execute(
                "INSERT INTO metric_points (target_id, metric_name, value, labels) VALUES (%s, %s, %s, %s)",
                (target_id, "table_size_bytes", total_bytes, labels),
            )
        cur.execute(
            """
            INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, "table_metrics", started_at, status, error),
        )
        conn.commit()


def _collect_wait_events(target_row):
    """Persists one metric_points row per wait_event_type bucket
    (WAIT_EVENT_SNAPSHOT_QUERY, shared with the live snapshot endpoint —
    routers/wait_events.py) each cycle, feeding the Trends screen's Wait
    Events history. Piggybacks on table_metrics_cycle's own cadence (5 min
    by default) rather than a new scheduler_jobs row: both this and
    _collect_table_metrics are "moderate cardinality, moderate cadence"
    per-target detail collectors, unlike the 30s-cadence single-value
    COLLECTORS list above."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, rows = "ok", None, []

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
            cur.execute(WAIT_EVENT_SNAPSHOT_QUERY)
            rows = cur.fetchall()
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("wait event collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        for category, count in rows:
            labels = Jsonb({"category": category})
            cur.execute(
                "INSERT INTO metric_points (target_id, metric_name, value, labels) VALUES (%s, %s, %s, %s)",
                (target_id, "wait_event_count", count, labels),
            )
        cur.execute(
            """
            INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, "wait_events", started_at, status, error),
        )
        conn.commit()


def _collect_query_history(target_row):
    """Reads the top TOP_N_TRACKED_QUERIES pg_stat_statements rows and turns
    each one's cumulative counters into this interval's delta against the
    baseline recorded in tracked_queries. A query seen for the first time
    gets a baseline row but no snapshot yet (there's nothing to diff against
    — its lifetime-to-date total would misrepresent "this interval"). A
    query whose counters went backwards had pg_stat_statements_reset() run
    against it since the last cycle; that interval is skipped (a gap in the
    chart) rather than recorded as a large negative delta."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, rows = "ok", None, []

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
            if pg_stat_statements_enabled(cur):
                cur.execute(QUERY_HISTORY_QUERY, (TOP_N_TRACKED_QUERIES,))
                rows = cur.fetchall()
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("query history collector failed for target %s: %s", target_id, exc)

    with store_conn() as conn, conn.cursor() as cur:
        for queryid, query_text, calls, total_exec_time, row_count in rows:
            cur.execute(
                "SELECT id, last_calls, last_total_exec_ms FROM tracked_queries WHERE target_id = %s AND queryid = %s",
                (target_id, queryid),
            )
            existing = cur.fetchone()

            if existing is None:
                cur.execute(
                    """
                    INSERT INTO tracked_queries (target_id, queryid, query_text, last_calls, last_total_exec_ms)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (target_id, queryid, query_text, calls, total_exec_time),
                )
                continue

            tracked_id, last_calls, last_total_exec_ms = existing
            reset_detected = calls < last_calls or total_exec_time < last_total_exec_ms
            calls_delta = 0 if reset_detected else calls - last_calls

            cur.execute(
                """
                UPDATE tracked_queries
                SET query_text = %s, last_calls = %s, last_total_exec_ms = %s, last_seen_at = now()
                WHERE id = %s
                """,
                (query_text, calls, total_exec_time, tracked_id),
            )

            if calls_delta > 0:
                mean_exec_ms = (total_exec_time - last_total_exec_ms) / calls_delta
                cur.execute(
                    """
                    INSERT INTO query_stat_snapshots (tracked_query_id, calls, mean_exec_ms, rows)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (tracked_id, calls_delta, mean_exec_ms, row_count),
                )

        cur.execute(
            """
            INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, "query_history", started_at, status, error),
        )
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
    ordering."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error = "ok", None

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, query_text FROM tracked_queries WHERE target_id = %s", (target_id,))
        tracked = cur.fetchall()

    captures = []

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
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
                    # standalone) — skip it and reset the aborted transaction
                    # so the next query in this loop isn't affected.
                    target_conn.rollback()
                    continue

                plan_list = plan_payload if isinstance(plan_payload, list) else json.loads(plan_payload)
                fingerprint = build_fingerprint(plan_list[0]["Plan"])
                captures.append(
                    (tracked_id, hash_fingerprint(fingerprint), summarize_fingerprint(fingerprint), fingerprint)
                )
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("plan regression collector failed for target %s: %s", target_id, exc)

    if captures:
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
            conn.commit()

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO collector_runs (target_id, job_name, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, "plan_regression", started_at, status, error),
        )
        conn.commit()


def run_collection_cycle():
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        for collector in COLLECTORS:
            _run_collector(target_row, collector)


def run_table_metrics_cycle():
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        _collect_table_metrics(target_row)
        _collect_wait_events(target_row)


def run_query_history_cycle():
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        _collect_query_history(target_row)


def run_plan_regression_cycle():
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        _capture_query_plans(target_row)


def run_retention_cycle():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM metric_points WHERE collected_at < now() - (%s * interval '1 day')",
            (RETENTION_DAYS,),
        )
        cur.execute(
            "DELETE FROM query_stat_snapshots WHERE collected_at < now() - (%s * interval '1 day')",
            (RETENTION_DAYS,),
        )
        cur.execute(
            "DELETE FROM query_plan_snapshots WHERE collected_at < now() - (%s * interval '1 day')",
            (RETENTION_DAYS,),
        )
        # A query that's aged out of the top TOP_N_TRACKED_QUERIES and hasn't
        # resurfaced in RETENTION_DAYS is as good as forgotten — cascades its
        # (already-pruned-or-not) snapshots too.
        cur.execute(
            "DELETE FROM tracked_queries WHERE last_seen_at < now() - (%s * interval '1 day')",
            (RETENTION_DAYS,),
        )
        conn.commit()


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


def _run_deep_scan_category(target_row, category: str, compute_fn):
    """Runs one Advisor category's checks against a target and reconciles
    the result into deep_scan_findings (see _reconcile_category_findings)."""
    target_id = target_row[0]
    started_at = datetime.now(timezone.utc)
    status, error, findings = "ok", None, []

    try:
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
            findings = compute_fn(cur)
    except Exception as exc:
        status, error = "error", str(exc)
        logger.warning("deep scan (%s) failed for target %s: %s", category, target_id, exc)

    validated = [IndexFinding(**finding).model_dump(mode="json") for finding in findings]

    with store_conn() as conn, conn.cursor() as cur:
        newly_open = _reconcile_category_findings(cur, target_id, category, validated, status)
        cur.execute(
            """
            INSERT INTO deep_scan_runs (target_id, category, started_at, status, error)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (target_id, category, started_at, status, error),
        )
        conn.commit()

    maybe_send_alert(target_id, _target_label(target_row), newly_open)


def _table_health_compute_fn(target_id):
    """Binds target_id into a plain compute_fn(cur) closure so
    _run_deep_scan_category's generic single-argument call still works
    unchanged for this one category — see DEEP_SCAN_CHECKS' comment above."""
    return lambda cur: compute_all_table_health_findings(cur, target_id)


def run_deep_scan_cycle():
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        target_id = target_row[0]
        for category, compute_fn in DEEP_SCAN_CHECKS:
            _run_deep_scan_category(target_row, category, compute_fn)
        _run_deep_scan_category(target_row, "table health", _table_health_compute_fn(target_id))


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
    _run_deep_scan_category(target_row, "table health", _table_health_compute_fn(target_id))


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
        with _connect_target(target_row) as target_conn, target_conn.cursor() as cur:
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
    with store_conn() as conn:
        targets = _active_targets(conn)
    for target_row in targets:
        _collect_live_findings(target_row)


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
    _scheduler = BackgroundScheduler()
    _scheduler.start()

    reconcile()
    _scheduler.add_job(
        reconcile,
        "interval",
        minutes=RECONCILE_SELF_HEAL_MINUTES,
        id="reconcile_self_heal",
    )

    return _scheduler
