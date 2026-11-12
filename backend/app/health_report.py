"""Weekly health report: one page per target summing up a week of what the
store already holds — findings opened and resolved, metric ranges, the
heaviest queries, lock incidents, plan changes, table growth, server
events and collector health.

build_report_data() reads the store (never the target) and returns plain
JSON-able data, stored as health_reports.data. render_report_html() and
report_summary_lines() turn that data into the downloadable page and the
webhook message; both are pure.
"""

import html
from datetime import datetime, timedelta

from app.routers.plan_regressions import get_plan_regressions
from app.severity import SEVERITY_RANK
from app.time_machine import SnapshotSummary, group_incidents, mask_query
from app.units import format_bytes, format_seconds

REPORT_PERIOD = timedelta(days=7)
TOP_FINDINGS = 10
TOP_QUERIES = 10
TOP_TABLES = 5
TOP_INCIDENTS = 5
QUERY_PREVIEW_CHARS = 300

# (metric_name, label, unit)
REPORT_METRICS = (
    ("connection_count", "Connections", "count"),
    ("cache_hit_ratio", "Cache hit rate", "%"),
    ("database_size_bytes", "Database size", "bytes"),
    ("worst_table_dead_pct", "Worst table dead rows", "%"),
    ("worst_table_xid_age", "Transaction ID age", "count"),
)

TARGET_SQL = "SELECT id, name, host, port, dbname, detected_pg_version FROM targets WHERE id = %s"

OPEN_FINDINGS_SQL = """
    SELECT finding_id, category, finding->>'severity', finding->>'title', finding->>'summary',
           finding->>'suggested_action', first_seen_at
    FROM deep_scan_findings
    WHERE target_id = %s AND status = 'open'
"""

OPENED_SQL = """
    SELECT finding_id, category, finding->>'severity', finding->>'title', first_seen_at
    FROM deep_scan_findings
    WHERE target_id = %s AND first_seen_at >= %s AND first_seen_at < %s
    ORDER BY first_seen_at DESC
"""

RESOLVED_COUNT_SQL = """
    SELECT count(*) FROM deep_scan_findings
    WHERE target_id = %s AND resolved_at >= %s AND resolved_at < %s
"""

METRIC_STATS_SQL = """
    SELECT metric_name, min(value), avg(value), max(value)
    FROM metric_points
    WHERE target_id = %s AND metric_name = ANY(%s) AND collected_at >= %s AND collected_at < %s
    GROUP BY metric_name
"""

METRIC_EDGE_SQL = """
    SELECT DISTINCT ON (metric_name) metric_name, value
    FROM metric_points
    WHERE target_id = %s AND metric_name = ANY(%s) AND collected_at >= %s AND collected_at < %s
    ORDER BY metric_name, collected_at {direction}
"""

TOP_QUERIES_SQL = """
    SELECT tq.queryid, tq.query_text, sum(s.calls)::bigint, sum(s.calls * s.mean_exec_ms)
    FROM query_stat_snapshots s
    JOIN tracked_queries tq ON tq.id = s.tracked_query_id
    WHERE tq.target_id = %s AND s.collected_at >= %s AND s.collected_at < %s
    GROUP BY tq.queryid, tq.query_text
    ORDER BY 4 DESC
    LIMIT %s
"""

QUERY_TOTALS_SQL = """
    SELECT tq.queryid, sum(s.calls * s.mean_exec_ms)
    FROM query_stat_snapshots s
    JOIN tracked_queries tq ON tq.id = s.tracked_query_id
    WHERE tq.target_id = %s AND s.collected_at >= %s AND s.collected_at < %s AND tq.queryid = ANY(%s)
    GROUP BY tq.queryid
"""

TOTAL_DB_TIME_SQL = """
    SELECT coalesce(sum(s.calls * s.mean_exec_ms), 0)
    FROM query_stat_snapshots s
    JOIN tracked_queries tq ON tq.id = s.tracked_query_id
    WHERE tq.target_id = %s AND s.collected_at >= %s AND s.collected_at < %s
"""

PLAN_CHANGES_COUNT_SQL = """
    SELECT count(*)
    FROM query_plan_snapshots p
    JOIN tracked_queries tq ON tq.id = p.tracked_query_id
    WHERE tq.target_id = %s AND p.collected_at >= %s AND p.collected_at < %s
      AND EXISTS (
          SELECT 1 FROM query_plan_snapshots e
          WHERE e.tracked_query_id = p.tracked_query_id AND e.collected_at < p.collected_at
      )
"""

LOCK_SNAPSHOTS_SQL = """
    SELECT collected_at, blocked_count, max_wait_seconds
    FROM lock_snapshots
    WHERE target_id = %s AND collected_at >= %s AND collected_at < %s
    ORDER BY collected_at
"""

EVENTS_SQL = """
    SELECT occurred_at, kind, detail
    FROM target_events
    WHERE target_id = %s AND occurred_at >= %s AND occurred_at < %s
    ORDER BY occurred_at
"""

TABLE_GROWTH_SQL = """
    SELECT schema_name, table_name,
           (array_agg(total_bytes ORDER BY collected_at))[1],
           (array_agg(total_bytes ORDER BY collected_at DESC))[1]
    FROM table_stat_snapshots
    WHERE target_id = %s AND collected_at >= %s AND collected_at < %s AND total_bytes IS NOT NULL
    GROUP BY schema_name, table_name
"""

COLLECTOR_HEALTH_SQL = """
    SELECT job_name, count(*), count(*) FILTER (WHERE status <> 'ok')
    FROM collector_runs
    WHERE target_id = %s AND finished_at >= %s AND finished_at < %s
    GROUP BY job_name
    ORDER BY job_name
"""


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _severity_key(severity: str | None) -> int:
    return SEVERITY_RANK.get(severity or "unknown", 0)


def _metrics(cur, target_id, start, end) -> list[dict]:
    names = [name for name, _label, _unit in REPORT_METRICS]
    previous_start = start - (end - start)
    cur.execute(METRIC_STATS_SQL, (target_id, names, start, end))
    stats = {name: (low, avg, high) for name, low, avg, high in cur.fetchall()}
    cur.execute(METRIC_STATS_SQL, (target_id, names, previous_start, start))
    previous = {name: avg for name, _low, avg, _high in cur.fetchall()}
    cur.execute(METRIC_EDGE_SQL.format(direction="ASC"), (target_id, names, start, end))
    first = dict(cur.fetchall())
    cur.execute(METRIC_EDGE_SQL.format(direction="DESC"), (target_id, names, start, end))
    last = dict(cur.fetchall())
    rows = []
    for name, label, unit in REPORT_METRICS:
        if name not in stats:
            continue
        low, avg, high = stats[name]
        rows.append(
            {
                "metric": name,
                "label": label,
                "unit": unit,
                "min": low,
                "avg": avg,
                "max": high,
                "first": first.get(name),
                "last": last.get(name),
                "previous_avg": previous.get(name),
            }
        )
    return rows


def _top_queries(cur, target_id, start, end) -> tuple[list[dict], float]:
    cur.execute(TOTAL_DB_TIME_SQL, (target_id, start, end))
    total = cur.fetchone()[0] or 0.0
    cur.execute(TOP_QUERIES_SQL, (target_id, start, end, TOP_QUERIES))
    rows = cur.fetchall()
    ids = [queryid for queryid, *_rest in rows]
    previous = {}
    if ids:
        cur.execute(QUERY_TOTALS_SQL, (target_id, start - (end - start), start, ids))
        previous = dict(cur.fetchall())
    queries = []
    for queryid, text, calls, total_ms in rows:
        masked = " ".join((mask_query(text) or "").split())
        queries.append(
            {
                "queryid": str(queryid),
                "query": masked if len(masked) <= QUERY_PREVIEW_CHARS else masked[: QUERY_PREVIEW_CHARS - 1] + "…",
                "calls": calls,
                "total_ms": round(total_ms or 0.0, 1),
                "mean_ms": round((total_ms or 0.0) / calls, 3) if calls else None,
                "share_pct": round(100.0 * total_ms / total, 1) if total else None,
                "previous_total_ms": round(previous[queryid], 1) if previous.get(queryid) is not None else None,
            }
        )
    return queries, total


def _findings(cur, target_id, start, end) -> dict:
    cur.execute(OPEN_FINDINGS_SQL, (target_id,))
    open_rows = cur.fetchall()
    by_severity = {"critical": 0, "attention": 0}
    by_category: dict[str, dict] = {}
    for _fid, category, severity, *_rest in open_rows:
        by_severity[severity] = by_severity.get(severity, 0) + 1
        entry = by_category.setdefault(category, {"category": category, "critical": 0, "attention": 0, "total": 0})
        entry["total"] += 1
        if severity in ("critical", "attention"):
            entry[severity] += 1
    open_rows.sort(key=lambda r: (-_severity_key(r[2]), r[6]))
    top = [
        {
            "finding_id": fid,
            "category": category,
            "severity": severity,
            "title": title,
            "summary": summary,
            "suggested_action": action,
            "first_seen_at": _iso(first_seen),
        }
        for fid, category, severity, title, summary, action, first_seen in open_rows[:TOP_FINDINGS]
    ]
    cur.execute(OPENED_SQL, (target_id, start, end))
    opened = cur.fetchall()
    cur.execute(RESOLVED_COUNT_SQL, (target_id, start, end))
    resolved = cur.fetchone()[0]
    opened_sorted = sorted(opened, key=lambda r: (-_severity_key(r[2]), r[4]))
    return {
        "open_by_severity": by_severity,
        "open_total": len(open_rows),
        "open_by_category": sorted(by_category.values(), key=lambda c: (-c["critical"], -c["attention"], c["category"])),
        "top_open": top,
        "opened": len(opened),
        "resolved": resolved,
        "new": [
            {"finding_id": fid, "category": category, "severity": severity, "title": title, "first_seen_at": _iso(at)}
            for fid, category, severity, title, at in opened_sorted[:TOP_FINDINGS]
        ],
    }


def _lock_incidents(cur, target_id, start, end) -> dict:
    cur.execute(LOCK_SNAPSHOTS_SQL, (target_id, start, end))
    incidents = group_incidents([SnapshotSummary(*row) for row in cur.fetchall()])
    for incident in incidents:
        incident["duration_seconds"] = int((incident["end"] - incident["start"]).total_seconds())
    worst = sorted(incidents, key=lambda i: (i["max_wait_seconds"] or 0, i["max_blocked"]), reverse=True)
    return {
        "count": len(incidents),
        "longest_wait_seconds": max((i["max_wait_seconds"] or 0 for i in incidents), default=None),
        "max_blocked": max((i["max_blocked"] for i in incidents), default=0),
        "top": [
            {
                "start": _iso(i["start"]),
                "duration_seconds": i["duration_seconds"],
                "max_blocked": i["max_blocked"],
                "max_wait_seconds": i["max_wait_seconds"],
            }
            for i in worst[:TOP_INCIDENTS]
        ],
    }


def _events(cur, target_id, start, end) -> dict:
    cur.execute(EVENTS_SQL, (target_id, start, end))
    restarts, resets, config_changes = [], [], []
    for at, kind, detail in cur.fetchall():
        if kind == "restart":
            restarts.append(_iso(at))
        elif kind == "stats_reset":
            resets.append(_iso(at))
        elif kind == "config_change":
            names = [c["name"] for c in (detail or {}).get("changes", [])]
            config_changes.append({"at": _iso(at), "settings": names})
    return {"restarts": restarts, "stats_resets": resets, "config_changes": config_changes}


def _table_growth(cur, target_id, start, end) -> list[dict]:
    cur.execute(TABLE_GROWTH_SQL, (target_id, start, end))
    rows = [
        {"table": f"{schema}.{table}", "start_bytes": first, "end_bytes": last, "growth_bytes": last - first}
        for schema, table, first, last in cur.fetchall()
        if first is not None and last is not None
    ]
    rows.sort(key=lambda r: r["growth_bytes"], reverse=True)
    return [r for r in rows[:TOP_TABLES] if r["growth_bytes"] > 0]


def _collector_health(cur, target_id, start, end) -> list[dict]:
    cur.execute(COLLECTOR_HEALTH_SQL, (target_id, start, end))
    return [
        {"job": job, "runs": runs, "errors": errors, "error_pct": round(100.0 * errors / runs, 1) if runs else 0.0}
        for job, runs, errors in cur.fetchall()
    ]


def _plan_regressions(target_id) -> list[dict]:
    """The currently regressed plans (routers/plan_regressions.py) — titles
    and timing only; the finding's detail carries query text."""
    return [
        {"id": f.id, "severity": f.severity, "title": f.title, "summary": f.summary}
        for f in get_plan_regressions(target_id).findings
    ]


def overall_severity(data: dict) -> str:
    by_severity = data["findings"]["open_by_severity"]
    if by_severity.get("critical"):
        return "critical"
    if by_severity.get("attention"):
        return "attention"
    return "healthy"


def build_report_data(cur, target_id, end: datetime) -> dict:
    """A week ending at `end`, from the store only."""
    start = end - REPORT_PERIOD
    cur.execute(TARGET_SQL, (target_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError("Target not found")
    tid, name, host, port, dbname, version = row
    top_queries, total_db_time = _top_queries(cur, target_id, start, end)
    cur.execute(PLAN_CHANGES_COUNT_SQL, (target_id, start, end))
    plan_changes = cur.fetchone()[0]
    data = {
        "target": {"id": str(tid), "name": name, "host": host, "port": port, "dbname": dbname, "pg_version": version},
        "period": {"start": _iso(start), "end": _iso(end)},
        "findings": _findings(cur, target_id, start, end),
        "metrics": _metrics(cur, target_id, start, end),
        "top_queries": top_queries,
        "total_db_time_ms": round(total_db_time, 1),
        "plan_changes": plan_changes,
        "plan_regressions": _plan_regressions(target_id),
        "lock_incidents": _lock_incidents(cur, target_id, start, end),
        "events": _events(cur, target_id, start, end),
        "table_growth": _table_growth(cur, target_id, start, end),
        "collector_health": _collector_health(cur, target_id, start, end),
    }
    data["overall_severity"] = overall_severity(data)
    return data


# --- Rendering -------------------------------------------------------------------


def _metric(data: dict, name: str) -> dict | None:
    return next((m for m in data.get("metrics", []) if m["metric"] == name), None)


def _format_metric(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "bytes":
        return format_bytes(value)
    if unit == "%":
        return f"{value:.1f}%"
    return f"{value:,.0f}"


def _date(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%Y-%m-%d")


def _datetime(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M UTC")


def report_title(data: dict) -> str:
    period = data["period"]
    return f"Weekly health report — {data['target']['name']} — {_date(period['start'])} to {_date(period['end'])}"


def report_summary_lines(data: dict) -> list[str]:
    """The headline facts, for the webhook message and the page's top."""
    findings = data["findings"]
    by_severity = findings["open_by_severity"]
    lines = [
        f"Open findings: {by_severity.get('critical', 0)} critical, {by_severity.get('attention', 0)} attention "
        f"({findings['opened']} new this week, {findings['resolved']} resolved)"
    ]
    size = _metric(data, "database_size_bytes")
    if size and size["last"] is not None:
        growth = (size["last"] - size["first"]) if size["first"] is not None else None
        growth_text = f" ({'+' if growth >= 0 else ''}{format_bytes(growth)} this week)" if growth is not None else ""
        lines.append(f"Database size: {format_bytes(size['last'])}{growth_text}")
    cache = _metric(data, "cache_hit_ratio")
    if cache and cache["avg"] is not None:
        lines.append(f"Cache hit rate: {cache['avg']:.1f}% average, {cache['min']:.1f}% lowest")
    incidents = data["lock_incidents"]
    if incidents["count"]:
        lines.append(
            f"Lock incidents: {incidents['count']} (longest wait {format_seconds(incidents['longest_wait_seconds'])}, "
            f"up to {incidents['max_blocked']} sessions blocked)"
        )
    else:
        lines.append("Lock incidents: none")
    regressions = data["plan_regressions"]
    if regressions or data["plan_changes"]:
        lines.append(f"Query plans: {data['plan_changes']} shape changes, {len(regressions)} currently slower")
    events = data["events"]
    if events["restarts"] or events["config_changes"]:
        lines.append(f"Events: {len(events['restarts'])} restarts, {len(events['config_changes'])} configuration changes")
    return lines


_SEVERITY_LABEL = {"critical": "Critical", "attention": "Attention", "healthy": "Healthy"}

_CSS = """
:root { --bg:#ffffff; --fg:#1d2330; --muted:#5b6475; --line:#dde1e8; --soft:#f4f6f9;
  --critical:#b42318; --critical-bg:#fde8e6; --attention:#9a5b00; --attention-bg:#fdf1dc;
  --healthy:#1e7a3c; --healthy-bg:#e5f5ea; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171d; --fg:#e7eaf0; --muted:#9aa3b2; --line:#2c323d;
  --soft:#1c2028; --critical:#ff8a7e; --critical-bg:#3b1a17; --attention:#f3b75a; --attention-bg:#3a2b12;
  --healthy:#6fd394; --healthy-bg:#16301f; } }
@media print { :root { --bg:#ffffff; --fg:#000000; --muted:#444444; --line:#cccccc; --soft:#f2f2f2; }
  section { break-inside: avoid; } }
* { box-sizing: border-box; }
body { margin:0; padding:32px 24px; background:var(--bg); color:var(--fg);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0 0 8px; }
p.meta { color: var(--muted); margin: 0 0 20px; }
section { border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 0 0 16px; }
ul.headline { margin: 0; padding-left: 18px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; background: var(--soft); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
code { font: 12px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace; word-break: break-word; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.badge.critical { color: var(--critical); background: var(--critical-bg); }
.badge.attention { color: var(--attention); background: var(--attention-bg); }
.badge.healthy { color: var(--healthy); background: var(--healthy-bg); }
.empty { color: var(--muted); margin: 0; }
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _badge(severity: str | None) -> str:
    severity = severity or "healthy"
    return f'<span class="badge {_e(severity)}">{_e(_SEVERITY_LABEL.get(severity, severity))}</span>'


def _table(headers: list[tuple[str, bool]], rows: list[list[str]], empty: str) -> str:
    """headers: (label, numeric). Cells are already-escaped HTML."""
    if not rows:
        return f'<p class="empty">{_e(empty)}</p>'
    classes = [' class="num"' if numeric else "" for _label, numeric in headers]
    head = "".join(f'<th{cls} scope="col">{_e(label)}</th>' for (label, _n), cls in zip(headers, classes, strict=True))
    body = "".join(
        "<tr>" + "".join(f"<td{cls}>{cell}</td>" for cell, cls in zip(row, classes, strict=True)) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _change(current, previous) -> str:
    if current is None or not previous:
        return "—"
    pct = 100.0 * (current - previous) / previous
    return f"{'+' if pct >= 0 else ''}{pct:.0f}%"


def render_report_html(data: dict, generated_at: datetime | None = None) -> str:
    """A self-contained page (inline CSS, no scripts, no external requests)
    that reads well on screen, in dark mode and printed to PDF."""
    title = report_title(data)
    target = data["target"]
    findings = data["findings"]
    generated = f" · generated {generated_at.strftime('%Y-%m-%d %H:%M UTC')}" if generated_at else ""
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body><main>",
        f"<h1>{_e(title)}</h1>",
        f'<p class="meta">{_e(target["host"])}:{_e(target["port"])}/{_e(target["dbname"])} · PostgreSQL '
        f"{_e(target.get('pg_version') or 'unknown')}{_e(generated)}</p>",
        f"<section><h2>Summary {_badge(data.get('overall_severity'))}</h2><ul class=\"headline\">",
        "".join(f"<li>{_e(line)}</li>" for line in report_summary_lines(data)),
        "</ul></section>",
    ]

    rows = [
        [_badge(f["severity"]), _e(f["title"]), _e(f["category"]), _e(f["suggested_action"]), _e(_date(f["first_seen_at"]))]
        for f in findings["top_open"]
    ]
    parts.append("<section><h2>Open findings</h2>")
    parts.append(
        _table(
            [("Severity", False), ("Finding", False), ("Category", False), ("What to do", False), ("Open since", True)],
            rows,
            "No open findings.",
        )
    )
    if findings["open_total"] > len(findings["top_open"]):
        parts.append(f'<p class="empty">… and {findings["open_total"] - len(findings["top_open"])} more in DbButler.</p>')
    parts.append("</section>")

    rows = [[_badge(f["severity"]), _e(f["title"]), _e(f["category"]), _e(_datetime(f["first_seen_at"]))] for f in findings["new"]]
    parts.append("<section><h2>New this week</h2>")
    parts.append(_table([("Severity", False), ("Finding", False), ("Category", False), ("Opened", True)], rows, "Nothing new opened."))
    parts.append("</section>")

    rows = [
        [
            _e(m["label"]),
            _e(_format_metric(m["min"], m["unit"])),
            _e(_format_metric(m["avg"], m["unit"])),
            _e(_format_metric(m["max"], m["unit"])),
            _e(_change(m["avg"], m["previous_avg"])),
        ]
        for m in data["metrics"]
    ]
    parts.append("<section><h2>Metrics</h2>")
    parts.append(
        _table(
            [("Metric", False), ("Lowest", True), ("Average", True), ("Highest", True), ("Avg vs last week", True)],
            rows,
            "No metrics were collected this week.",
        )
    )
    parts.append("</section>")

    rows = [
        [
            f"<code>{_e(q['query'])}</code>",
            _e(f"{q['share_pct']:.1f}%" if q["share_pct"] is not None else "—"),
            _e(f"{q['calls']:,}"),
            _e(f"{q['mean_ms']:.2f} ms" if q["mean_ms"] is not None else "—"),
            _e(_change(q["total_ms"], q["previous_total_ms"])),
        ]
        for q in data["top_queries"]
    ]
    parts.append("<section><h2>Heaviest queries</h2>")
    parts.append(
        _table(
            [("Query (literals masked)", False), ("DB time", True), ("Calls", True), ("Mean", True), ("vs last week", True)],
            rows,
            "No query statistics this week (is pg_stat_statements installed?).",
        )
    )
    parts.append("</section>")

    incidents = data["lock_incidents"]
    rows = [
        [
            _e(_datetime(i["start"])),
            _e(format_seconds(i["duration_seconds"])),
            _e(i["max_blocked"]),
            _e(format_seconds(i["max_wait_seconds"]) if i["max_wait_seconds"] is not None else "—"),
        ]
        for i in incidents["top"]
    ]
    parts.append(f"<section><h2>Lock incidents ({incidents['count']})</h2>")
    parts.append(
        _table(
            [("Started", False), ("Lasted", True), ("Sessions blocked", True), ("Longest wait", True)],
            rows,
            "No session waited on a lock for a second or more.",
        )
    )
    parts.append("</section>")

    rows = [[_badge(r["severity"]), _e(r["title"]), _e(r["summary"])] for r in data["plan_regressions"]]
    parts.append(f"<section><h2>Query plans ({data['plan_changes']} shape changes this week)</h2>")
    parts.append(_table([("Severity", False), ("Regression", False), ("Detail", False)], rows, "No plan is currently slower than before its change."))
    parts.append("</section>")

    rows = [
        [_e(t["table"]), _e(format_bytes(t["start_bytes"])), _e(format_bytes(t["end_bytes"])), _e("+" + format_bytes(t["growth_bytes"]))]
        for t in data["table_growth"]
    ]
    parts.append("<section><h2>Fastest-growing tables</h2>")
    parts.append(_table([("Table", False), ("Week start", True), ("Week end", True), ("Growth", True)], rows, "No table grew."))
    parts.append("</section>")

    events = data["events"]
    rows = [[_e(_datetime(at)), "Server restarted"] for at in events["restarts"]]
    rows += [[_e(_datetime(at)), "Statistics reset"] for at in events["stats_resets"]]
    rows += [[_e(_datetime(c["at"])), _e("Configuration changed: " + ", ".join(c["settings"]))] for c in events["config_changes"]]
    rows.sort(key=lambda r: r[0])
    parts.append("<section><h2>Server events</h2>")
    parts.append(_table([("When", False), ("What", False)], rows, "No restarts, statistics resets or configuration changes."))
    parts.append("</section>")

    rows = [[_e(c["job"]), _e(f"{c['runs']:,}"), _e(f"{c['errors']:,}"), _e(f"{c['error_pct']:.1f}%")] for c in data["collector_health"]]
    parts.append("<section><h2>DbButler collection</h2>")
    parts.append(
        _table(
            [("Job", False), ("Runs", True), ("Failed", True), ("Failure rate", True)],
            rows,
            "No collection runs are kept for this week (run history is kept for 7 days).",
        )
    )
    parts.append("</section></main></body></html>")
    return "".join(parts)
