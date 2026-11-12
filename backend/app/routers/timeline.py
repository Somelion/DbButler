"""Incident Time Machine: one correlated timeline per target, built from
what the store already keeps — metric lines, lock incidents
(lock_snapshots), plan-shape changes, findings opening and resolving, and
server events (restarts, stats resets, configuration changes) — plus "what
was going on at this moment" and an optional AI post-mortem of a window.

Store reads only; the target is never contacted here.
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.ai_providers import AiProviderError, call_ai
from app.crypto import decrypt
from app.db.store import store_conn
from app.schemas import TimelineExplainRequest, TimelineExplainResponse, TimelineMomentResponse, TimelineResponse
from app.time_machine import EXPLAIN_PROMPT, SnapshotSummary, group_incidents, incident_facts, root_blockers

router = APIRouter(prefix="/api/targets", tags=["time-machine"])

MAX_HOURS = 24 * 30
TARGET_POINTS = 300  # buckets per metric line
MAX_MARKERS = 400
MOMENT_LOCK_LOOKBACK = timedelta(seconds=60)
MOMENT_QUERY_LOOKAHEAD = timedelta(minutes=15)
MOMENT_NEARBY = timedelta(minutes=30)
MAX_EXPLAIN_WINDOW = timedelta(hours=24)
MILLISECOND = timedelta(milliseconds=1)
EXPLAIN_JOB_TTL = timedelta(hours=1)

# (metric_name, label filter, label, unit) — the lines drawn on the timeline.
SERIES = (
    ("connection_count", None, "Connections", "count"),
    ("cache_hit_ratio", None, "Cache hit rate", "%"),
    ("wait_event_count", "Lock", "Sessions waiting on locks", "count"),
    ("worst_table_dead_pct", None, "Worst table dead rows", "%"),
)

SERIES_SQL = """
    SELECT date_bin(%(bucket)s, collected_at, %(origin)s) AS bucket, avg(value)
    FROM metric_points
    WHERE target_id = %(target_id)s AND metric_name = %(metric)s
      AND collected_at >= %(start)s AND collected_at <= %(end)s
      {label_filter}
    GROUP BY 1
    ORDER BY 1
"""

LOCK_SNAPSHOTS_SQL = """
    SELECT collected_at, blocked_count, max_wait_seconds
    FROM lock_snapshots
    WHERE target_id = %s AND collected_at >= %s AND collected_at <= %s
    ORDER BY collected_at
"""

PLAN_CHANGES_SQL = """
    SELECT p.collected_at, tq.queryid, p.plan_summary
    FROM query_plan_snapshots p
    JOIN tracked_queries tq ON tq.id = p.tracked_query_id
    WHERE tq.target_id = %s AND p.collected_at >= %s AND p.collected_at <= %s
      AND EXISTS (
          SELECT 1 FROM query_plan_snapshots e
          WHERE e.tracked_query_id = p.tracked_query_id AND e.collected_at < p.collected_at
      )
    ORDER BY p.collected_at DESC
    LIMIT %s
"""

FINDINGS_OPENED_SQL = """
    SELECT first_seen_at, finding_id, category, finding->>'title', finding->>'severity'
    FROM deep_scan_findings
    WHERE target_id = %s AND first_seen_at >= %s AND first_seen_at <= %s
    ORDER BY first_seen_at DESC
    LIMIT %s
"""

FINDINGS_RESOLVED_SQL = """
    SELECT resolved_at, finding_id, category, finding->>'title', finding->>'severity'
    FROM deep_scan_findings
    WHERE target_id = %s AND resolved_at >= %s AND resolved_at <= %s
    ORDER BY resolved_at DESC
    LIMIT %s
"""

EVENTS_SQL = """
    SELECT occurred_at, kind, detail
    FROM target_events
    WHERE target_id = %s AND occurred_at >= %s AND occurred_at <= %s
    ORDER BY occurred_at DESC
    LIMIT %s
"""


def _window(hours: int, until: datetime | None) -> tuple[datetime, datetime]:
    hours = max(1, min(hours, MAX_HOURS))
    end = until or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end - timedelta(hours=hours), end


def _bucket(start: datetime, end: datetime) -> timedelta:
    """Bucket width giving about TARGET_POINTS points, never under the 30s
    collection cadence."""
    return max(timedelta(seconds=30), (end - start) / TARGET_POINTS)


def _load_series(cur, target_id, start, end) -> list[dict]:
    bucket = _bucket(start, end)
    series = []
    for metric, label_value, label, unit in SERIES:
        label_filter = "AND labels->>'category' = %(category)s" if label_value else ""
        cur.execute(
            SERIES_SQL.format(label_filter=label_filter),
            {
                "bucket": bucket,
                "origin": start,
                "target_id": target_id,
                "metric": metric,
                "start": start,
                "end": end,
                "category": label_value,
            },
        )
        points = [{"ts": ts, "value": round(value, 3)} for ts, value in cur.fetchall()]
        series.append({"metric": metric if not label_value else f"{metric}:{label_value}", "label": label, "unit": unit, "points": points})
    return series


def _load_incidents(cur, target_id, start, end) -> list[dict]:
    cur.execute(LOCK_SNAPSHOTS_SQL, (target_id, start, end))
    return group_incidents([SnapshotSummary(*row) for row in cur.fetchall()])


def _load_markers(cur, target_id, start, end) -> list[dict]:
    markers = []
    cur.execute(PLAN_CHANGES_SQL, (target_id, start, end, MAX_MARKERS))
    for at, queryid, summary in cur.fetchall():
        markers.append(
            {"at": at, "kind": "plan_change", "title": "Query plan changed", "detail": summary, "severity": None, "ref": str(queryid)}
        )
    for sql, kind, verb in ((FINDINGS_OPENED_SQL, "finding_opened", "Opened"), (FINDINGS_RESOLVED_SQL, "finding_resolved", "Resolved")):
        cur.execute(sql, (target_id, start, end, MAX_MARKERS))
        for at, finding_id, category, title, severity in cur.fetchall():
            markers.append(
                {
                    "at": at,
                    "kind": kind,
                    "title": f"{verb}: {title or finding_id}",
                    "detail": category,
                    "severity": severity,
                    "ref": finding_id,
                }
            )
    cur.execute(EVENTS_SQL, (target_id, start, end, MAX_MARKERS))
    for at, kind, detail in cur.fetchall():
        markers.append(_event_marker(at, kind, detail or {}))
    markers.sort(key=lambda m: m["at"], reverse=True)
    return markers[:MAX_MARKERS]


def _event_marker(at, kind: str, detail: dict) -> dict:
    if kind == "config_change":
        changes = detail.get("changes", [])
        text = "; ".join(f"{c['name']}: {c['old']} → {c['new']}" for c in changes[:12])
        return {"at": at, "kind": kind, "title": f"Configuration changed ({len(changes)})", "detail": text, "severity": None, "ref": None}
    titles = {"restart": "Server restarted", "stats_reset": "Statistics reset"}
    return {"at": at, "kind": kind, "title": titles.get(kind, kind), "detail": None, "severity": None, "ref": None}


@router.get("/{target_id}/timeline", response_model=TimelineResponse)
def get_timeline(target_id: uuid.UUID, hours: int = Query(24), until: datetime | None = Query(None)):
    start, end = _window(hours, until)
    with store_conn() as conn, conn.cursor() as cur:
        series = _load_series(cur, target_id, start, end)
        incidents = _load_incidents(cur, target_id, start, end)
        markers = _load_markers(cur, target_id, start, end)
    return {"start": start, "end": end, "series": series, "incidents": incidents, "markers": markers}


@router.get("/{target_id}/timeline/moment", response_model=TimelineMomentResponse)
def get_timeline_moment(target_id: uuid.UUID, at: datetime = Query(...)):
    """What was going on at one moment: the lock tree (the nearest snapshot
    at or just before it, with the neighbouring snapshots' times for
    stepping through an incident), the top queries of the Query History
    interval containing it, and the plan changes, findings and events
    around it."""
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    with store_conn() as conn, conn.cursor() as cur:
        # `< at + 1ms`, not `<= at`: snapshot times have microseconds and a
        # browser Date keeps milliseconds, so a timestamp the UI got from
        # this API (next_snapshot_at, an incident's start) comes back up to
        # 999µs early — and must still find its own snapshot.
        cur.execute(
            """
            SELECT collected_at, blocked_count, max_wait_seconds, sessions FROM lock_snapshots
            WHERE target_id = %s AND collected_at < %s AND collected_at >= %s
            ORDER BY collected_at DESC LIMIT 1
            """,
            (target_id, at + MILLISECOND, at - MOMENT_LOCK_LOOKBACK),
        )
        snapshot_row = cur.fetchone()
        reference = snapshot_row[0] if snapshot_row else at
        cur.execute(
            "SELECT max(collected_at) FROM lock_snapshots WHERE target_id = %s AND collected_at < %s",
            (target_id, reference),
        )
        previous_at = cur.fetchone()[0]
        cur.execute(
            "SELECT min(collected_at) FROM lock_snapshots WHERE target_id = %s AND collected_at > %s",
            (target_id, reference),
        )
        next_at = cur.fetchone()[0]

        # A query_stat_snapshots row at T covers the collection interval
        # ending at T: the first cycle at or after `at` is the one containing it.
        cur.execute(
            """
            SELECT min(s.collected_at) FROM query_stat_snapshots s
            JOIN tracked_queries t ON t.id = s.tracked_query_id
            WHERE t.target_id = %s AND s.collected_at >= %s AND s.collected_at <= %s
            """,
            (target_id, at, at + MOMENT_QUERY_LOOKAHEAD),
        )
        interval_end = cur.fetchone()[0]
        top_queries = []
        if interval_end is not None:
            cur.execute(
                """
                SELECT t.queryid, t.query_text, s.calls, s.mean_exec_ms
                FROM query_stat_snapshots s JOIN tracked_queries t ON t.id = s.tracked_query_id
                WHERE t.target_id = %s AND s.collected_at = %s
                ORDER BY s.calls * s.mean_exec_ms DESC
                LIMIT 10
                """,
                (target_id, interval_end),
            )
            top_queries = [
                {
                    "queryid": str(queryid),
                    "query": " ".join(text.split())[:300],
                    "calls": calls,
                    "mean_exec_ms": round(mean_ms, 3),
                    "db_time_ms": round(calls * mean_ms, 3),
                }
                for queryid, text, calls, mean_ms in cur.fetchall()
            ]

        nearby = _load_markers(cur, target_id, at - MOMENT_NEARBY, at + MOMENT_NEARBY)
        cur.execute(
            """
            SELECT finding_id, category, finding->>'title', finding->>'severity', first_seen_at
            FROM deep_scan_findings
            WHERE target_id = %s AND first_seen_at <= %s AND (resolved_at IS NULL OR resolved_at > %s)
            ORDER BY CASE finding->>'severity' WHEN 'critical' THEN 0 WHEN 'attention' THEN 1 ELSE 2 END, first_seen_at
            LIMIT 20
            """,
            (target_id, at, at),
        )
        open_findings = [
            {"id": fid, "category": category, "title": title or fid, "severity": severity, "since": since}
            for fid, category, title, severity, since in cur.fetchall()
        ]

    lock_tree = None
    if snapshot_row is not None:
        collected_at, blocked_count, max_wait, sessions = snapshot_row
        lock_tree = {
            "collected_at": collected_at,
            "blocked_count": blocked_count,
            "max_wait_seconds": max_wait,
            "root_pids": root_blockers(sessions),
            "sessions": sessions,
        }
    return {
        "at": at,
        "lock_tree": lock_tree,
        "previous_snapshot_at": previous_at,
        "next_snapshot_at": next_at,
        "query_interval_end": interval_end,
        "top_queries": top_queries,
        "nearby": nearby,
        "open_findings": open_findings,
    }


# "Explain" runs in the background and the UI polls for it: an AI provider
# may take up to two minutes (app/ai_providers.py), longer than nginx waits
# for a response (ui/nginx.conf proxy_read_timeout 60s) — same reason AI
# Query Analysis is asynchronous. Jobs live in this process's memory: the
# backend runs as a single uvicorn process, and a result only has to
# survive until the user's dialog reads it.
_explain_jobs: dict[str, dict] = {}
_explain_jobs_lock = threading.Lock()

# What sessions waited on, and the applications/states of the root blockers
# (blocking someone, blocked by no one in that snapshot), aggregated in SQL
# — never pulling every snapshot's session list into memory.
LOCK_FACTS_SQL = """
    WITH s AS (
        SELECT l.id, e AS session
        FROM lock_snapshots l, jsonb_array_elements(l.sessions) e
        WHERE l.target_id = %(target_id)s AND l.collected_at >= %(start)s AND l.collected_at <= %(end)s
    ),
    blocking AS (
        SELECT DISTINCT s.id, b::int AS pid
        FROM s, jsonb_array_elements_text(s.session->'blocked_by') b
    ),
    roots AS (
        SELECT s.session
        FROM s JOIN blocking ON blocking.id = s.id AND blocking.pid = (s.session->>'pid')::int
        WHERE jsonb_array_length(s.session->'blocked_by') = 0
    )
    SELECT
        (SELECT array_agg(DISTINCT session->>'waiting_on') FROM s WHERE session->>'waiting_on' IS NOT NULL),
        (SELECT array_agg(DISTINCT coalesce(nullif(session->>'application_name', ''), '(no application_name)')) FROM roots),
        (SELECT array_agg(DISTINCT coalesce(session->>'state', 'unknown')) FROM roots),
        (SELECT count(*) FROM s)
"""


def _prune_explain_jobs(now: datetime) -> None:
    with _explain_jobs_lock:
        for job_id in [j for j, job in _explain_jobs.items() if now - job["created"] > EXPLAIN_JOB_TTL]:
            del _explain_jobs[job_id]


def _explain_job_view(job_id: str, job: dict) -> dict:
    return {key: job.get(key) for key in ("status", "narrative", "facts", "error")} | {"job_id": job_id}


@router.post("/{target_id}/timeline/explain", response_model=TimelineExplainResponse)
def explain_window(target_id: uuid.UUID, request: TimelineExplainRequest, background_tasks: BackgroundTasks):
    """Starts an AI post-mortem of a window, from facts only: incident
    counts and durations, lock modes and table names, blocking applications,
    plan-shape summaries, finding titles, events and metric ranges — never
    query text or data. Poll GET /timeline/explain/{job_id}; the result
    includes the facts sent, so the user sees exactly what left the
    building."""
    start, end = request.start, request.end
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start or end - start > MAX_EXPLAIN_WINDOW:
        raise HTTPException(status_code=422, detail="Pick a window of up to 24 hours.")

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT provider, endpoint_url, model_name, encrypted_api_key FROM ai_settings WHERE id = 1")
        settings_row = cur.fetchone()
    if settings_row is None:
        raise HTTPException(status_code=409, detail="No AI provider is configured. Set one up in Settings.")
    provider, endpoint_url, _model, encrypted_api_key = settings_row
    if provider == "gemini" and not encrypted_api_key:
        raise HTTPException(status_code=409, detail="No Gemini API key is configured. Add one in Settings.")
    if provider == "local" and not endpoint_url:
        raise HTTPException(status_code=409, detail="No local model endpoint URL is configured. Add one in Settings.")

    now = datetime.now(timezone.utc)
    _prune_explain_jobs(now)
    job_id = uuid.uuid4().hex
    with _explain_jobs_lock:
        _explain_jobs[job_id] = {"status": "pending", "created": now, "target_id": target_id}
    background_tasks.add_task(_run_explain, job_id, target_id, start, end, settings_row)
    return _explain_job_view(job_id, _explain_jobs[job_id])


@router.get("/{target_id}/timeline/explain/{job_id}", response_model=TimelineExplainResponse)
def get_explain_job(target_id: uuid.UUID, job_id: str):
    with _explain_jobs_lock:
        job = _explain_jobs.get(job_id)
    if job is None or job["target_id"] != target_id:
        raise HTTPException(status_code=404, detail="This explanation has expired or doesn't exist.")
    return _explain_job_view(job_id, job)


def _finish_job(job_id: str, **fields) -> None:
    with _explain_jobs_lock:
        if job_id in _explain_jobs:
            _explain_jobs[job_id].update(fields)


def _run_explain(job_id: str, target_id, start: datetime, end: datetime, settings_row) -> None:
    try:
        facts = build_window_facts(target_id, start, end)
    except Exception as exc:  # noqa: BLE001 — any failure is reported to the waiting dialog
        _finish_job(job_id, status="error", error=f"Couldn't gather the facts: {exc}")
        return
    provider, endpoint_url, model_name, encrypted_api_key = settings_row
    api_key = decrypt(encrypted_api_key) if encrypted_api_key else None
    try:
        narrative = call_ai(provider, api_key, endpoint_url, model_name, EXPLAIN_PROMPT.format(facts=facts))
    except AiProviderError as exc:
        _finish_job(job_id, status="error", error=str(exc), facts=facts)
        return
    _finish_job(job_id, status="done", narrative=narrative.strip(), facts=facts)


def build_window_facts(target_id, start: datetime, end: datetime) -> str:
    with store_conn() as conn, conn.cursor() as cur:
        incidents = _load_incidents(cur, target_id, start, end)
        cur.execute(LOCK_FACTS_SQL, {"target_id": target_id, "start": start, "end": end})
        waiting_on, blocker_apps, blocker_states, session_rows = cur.fetchone()
        markers = _load_markers(cur, target_id, start, end)
        series = _load_series(cur, target_id, start, end)
        cur.execute(EVENTS_SQL, (target_id, start, end, MAX_MARKERS))
        events = [{"at": at, "kind": kind, "detail": detail or {}} for at, kind, detail in cur.fetchall()]

    lock_details = (
        [
            {
                "waiting_on": sorted(waiting_on or [])[:10],
                "blocker_apps": sorted(blocker_apps or [])[:10],
                "blocker_states": sorted(blocker_states or []),
            }
        ]
        if session_rows
        else []
    )
    metrics = {}
    for line in series:
        values = [p["value"] for p in line["points"]]
        if values:
            metrics[line["label"]] = {
                "min": round(min(values), 1),
                "max": round(max(values), 1),
                "avg": round(sum(values) / len(values), 1),
            }
    return incident_facts(
        start,
        end,
        incidents,
        lock_details,
        [{"at": m["at"], "summary": m["detail"]} for m in markers if m["kind"] == "plan_change"],
        [
            {"at": m["at"], "severity": m["severity"], "title": m["title"].removeprefix("Opened: ")}
            for m in markers
            if m["kind"] == "finding_opened"
        ],
        events,
        metrics,
    )
