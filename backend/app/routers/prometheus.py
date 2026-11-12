"""Prometheus scrape endpoint: the metrics DbButler already collects and its
open-finding counts, per target, for an existing Grafana/Alertmanager.

Protected by METRICS_TOKEN only (app/auth.py::require_metrics_token) —
never AUTH_TOKEN — and off (404) until that's set. Served at /metrics on
the backend's own port and at /api/metrics through the UI's nginx.

Store reads only: a scrape never touches a monitored database. Values are
the latest reading within METRIC_FRESHNESS; an older one is left out
rather than repeated, so a stalled collector shows up as an absent series
(and dbbutler_target_up = 0), not a flat line.
"""

from fastapi import APIRouter, Depends, Response

from app.auth import require_metrics_token
from app.db.store import store_conn
from app.prometheus import CONTENT_TYPE, Family, render
from app.version import APP_VERSION

router = APIRouter(tags=["prometheus"])

METRIC_FRESHNESS = "15 minutes"
LOCK_FRESHNESS = "60 seconds"
TARGET_UP_WINDOW = "10 minutes"

# metric_points name -> (exposed name, help)
POINT_METRICS = {
    "connection_count": ("dbbutler_connections", "Sessions connected to the database (pg_stat_activity)."),
    "cache_hit_ratio": ("dbbutler_cache_hit_percent", "Buffer cache hit rate between the last two samples, percent."),
    "database_size_bytes": ("dbbutler_database_size_bytes", "pg_database_size of the monitored database."),
    "worst_table_dead_pct": (
        "dbbutler_worst_table_dead_tuple_percent",
        "Dead-tuple percentage of the worst table in the database.",
    ),
    "worst_table_xid_age": ("dbbutler_database_xid_age", "age(datfrozenxid): transactions until a forced anti-wraparound vacuum."),
    "database_mxid_age": ("dbbutler_database_mxid_age", "mxid_age(datminmxid) of the database."),
}

SEVERITIES = ("critical", "attention")

TARGETS_SQL = "SELECT id, name, dbname, coalesce(detected_pg_version, '') FROM targets WHERE is_active ORDER BY name"

FINDINGS_SQL = """
    SELECT target_id, coalesce(finding->>'severity', 'unknown'), category, count(*)
    FROM deep_scan_findings
    WHERE status = 'open'
    GROUP BY 1, 2, 3
"""

LATEST_POINTS_SQL = f"""
    SELECT DISTINCT ON (target_id, metric_name) target_id, metric_name, value
    FROM metric_points
    WHERE metric_name = ANY(%s) AND collected_at > now() - interval '{METRIC_FRESHNESS}'
    ORDER BY target_id, metric_name, collected_at DESC
"""

LATEST_LOCKS_SQL = f"""
    SELECT DISTINCT ON (target_id) target_id, blocked_count, coalesce(max_wait_seconds, 0)
    FROM lock_snapshots
    WHERE collected_at > now() - interval '{LOCK_FRESHNESS}'
    ORDER BY target_id, collected_at DESC
"""

LATEST_RUNS_SQL = """
    SELECT DISTINCT ON (target_id, job_name) target_id, job_name, status = 'ok', extract(epoch FROM finished_at),
           finished_at > now() - %s::interval
    FROM collector_runs
    WHERE finished_at > now() - interval '1 hour'
    ORDER BY target_id, job_name, finished_at DESC
"""


def collect_families() -> list[Family]:
    info = Family("dbbutler_info", "DbButler itself; the value is always 1.")
    info.add({"version": APP_VERSION}, 1)
    target_info = Family("dbbutler_target_info", "A monitored database; the value is always 1.")
    up = Family("dbbutler_target_up", f"1 if the last collection cycle in the past {TARGET_UP_WINDOW} succeeded.")
    findings = Family("dbbutler_open_findings", "Open findings by severity and category.")
    by_severity = Family("dbbutler_open_findings_by_severity", "Open findings by severity (0 when none).")
    blocked = Family("dbbutler_lock_blocked_sessions", "Sessions waiting on a lock in the latest lock snapshot (0 when none).")
    max_wait = Family("dbbutler_lock_max_wait_seconds", "Longest current lock wait in the latest lock snapshot.")
    run_ok = Family("dbbutler_collector_last_run_ok", "1 if the job's latest run (past hour) succeeded for this target.")
    run_at = Family("dbbutler_collector_last_run_timestamp_seconds", "When the job last ran for this target (Unix time).")
    points = {name: Family(exposed, help_text) for name, (exposed, help_text) in POINT_METRICS.items()}

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(TARGETS_SQL)
        targets = cur.fetchall()
        cur.execute(FINDINGS_SQL)
        finding_rows = cur.fetchall()
        cur.execute(LATEST_POINTS_SQL, (list(POINT_METRICS),))
        point_rows = cur.fetchall()
        cur.execute(LATEST_LOCKS_SQL)
        lock_rows = {row[0]: row[1:] for row in cur.fetchall()}
        cur.execute(LATEST_RUNS_SQL, (TARGET_UP_WINDOW,))
        run_rows = cur.fetchall()

    labels = {}
    for target_id, name, dbname, version in targets:
        labels[target_id] = {"target_id": str(target_id), "target": name, "dbname": dbname}
        target_info.add({**labels[target_id], "pg_version": version}, 1)

    severity_totals = {target_id: dict.fromkeys(SEVERITIES, 0) for target_id in labels}
    for target_id, severity, category, count in finding_rows:
        if target_id not in labels:
            continue
        findings.add({**labels[target_id], "severity": severity, "category": category}, count)
        totals = severity_totals[target_id]
        totals[severity] = totals.get(severity, 0) + count
    for target_id, totals in severity_totals.items():
        for severity, count in totals.items():
            by_severity.add({**labels[target_id], "severity": severity}, count)

    for target_id, metric_name, value in point_rows:
        if target_id in labels:
            points[metric_name].add(labels[target_id], value)

    for target_id, target_labels in labels.items():
        count, wait = lock_rows.get(target_id, (0, 0))
        blocked.add(target_labels, count)
        if count:
            max_wait.add(target_labels, wait)

    collection_ok = dict.fromkeys(labels, False)
    for target_id, job_name, ok, finished_epoch, recent in run_rows:
        if target_id not in labels:
            continue
        # "collector", not "job": Prometheus owns the job label and would
        # rename ours to exported_job.
        run_ok.add({**labels[target_id], "collector": job_name}, ok)
        run_at.add({**labels[target_id], "collector": job_name}, finished_epoch)
        if job_name == "collection" and ok and recent:
            collection_ok[target_id] = True
    for target_id, ok in collection_ok.items():
        up.add(labels[target_id], ok)

    return [info, target_info, up, *points.values(), by_severity, findings, blocked, max_wait, run_ok, run_at]


def _metrics() -> Response:
    return Response(content=render(collect_families()), media_type=CONTENT_TYPE)


@router.get("/metrics", dependencies=[Depends(require_metrics_token)], include_in_schema=False)
def metrics():
    return _metrics()


@router.get("/api/metrics", dependencies=[Depends(require_metrics_token)])
def api_metrics():
    """Same as /metrics, reachable through the UI's nginx (which only
    proxies /api/)."""
    return _metrics()
