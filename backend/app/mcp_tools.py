"""The tools DbButler's MCP server (app/mcp_server.py) offers an AI agent.

Every tool is a fixed, read-only query or an existing read-only check — no
tool accepts SQL to run, and none changes anything. Most read only the
store (findings, history, reports); run_diagnosis, get_workload_map,
get_refactoring_suggestions and lint_migration also read the target's
statistics and catalog with DbButler's own monitoring login and its usual
timeouts, the same way the screens do. The agent never gets a login.

Query text leaves with its literal values masked (app/time_machine.py's
mask_query: comments dropped, '…' and numbers replaced), session user
names are left out of lock data, and session/owner role names are scrubbed
from finding text (scrub_identities, applied to every result).
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.db.store import store_conn
from app.routers.ddl_lint import run_migration_lint
from app.routers.diagnose import diagnose
from app.routers.plan_regressions import get_plan_regressions
from app.routers.refactoring_advisor import get_refactoring_advisor
from app.routers.timeline import LOCK_SNAPSHOTS_SQL
from app.time_machine import SnapshotSummary, group_incidents, mask_query, root_blockers
from app.workload_data import collect_workload

MAX_LIMIT = 100
SEVERITY_ORDER = {"critical": 0, "attention": 1, "healthy": 2, "unknown": 3}

# metric_points name -> (label, unit)
METRICS = {
    "connection_count": ("Connections", "count"),
    "cache_hit_ratio": ("Cache hit rate", "%"),
    "database_size_bytes": ("Database size", "bytes"),
    "worst_table_dead_pct": ("Worst table dead rows", "%"),
    "worst_table_xid_age": ("Transaction ID age (datfrozenxid)", "count"),
    "database_mxid_age": ("Multixact ID age", "count"),
}


class ToolError(Exception):
    """A problem the agent should see and can act on (bad argument, unknown
    target, target unreachable) — returned as a tool result with isError."""


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]

    def definition(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }


# --- Argument handling -------------------------------------------------------------

TARGET_ARG = {
    "type": "string",
    "description": "The monitored database: its id (UUID) or its name as shown by list_targets.",
}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def validate_arguments(schema: dict, arguments) -> dict:
    """The subset of JSON Schema the tools use: object with typed
    properties, required, enum, minimum/maximum, maxLength, no extras."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("Arguments must be an object.")
    properties = schema.get("properties", {})
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(unknown)}.")
    for name in schema.get("required", []):
        if arguments.get(name) in (None, ""):
            raise ToolError(f"Missing required argument: {name}.")
    result = {}
    for name, spec in properties.items():
        if name not in arguments or arguments[name] is None:
            if "default" in spec:
                result[name] = spec["default"]
            continue
        value = arguments[name]
        kind = spec.get("type")
        if kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError(f"{name} must be an integer.")
            if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
                raise ToolError(f"{name} must be between {spec.get('minimum')} and {spec.get('maximum')}.")
        elif kind == "string":
            if not isinstance(value, str):
                raise ToolError(f"{name} must be a string.")
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                raise ToolError(f"{name} is longer than {spec['maxLength']} characters.")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{name} must be one of: {', '.join(map(str, spec['enum']))}.")
        result[name] = value
    return result


def resolve_target(cur, reference: str) -> tuple[uuid.UUID, str]:
    """A target by id, else by exact name, else by database name — case
    insensitive; ambiguous or unknown is an error naming the choices."""
    reference = reference.strip()
    try:
        target_id = uuid.UUID(reference)
    except ValueError:
        target_id = None
    if target_id is not None:
        cur.execute("SELECT id, name FROM targets WHERE id = %s", (target_id,))
        rows = cur.fetchall()
    else:
        cur.execute("SELECT id, name FROM targets WHERE lower(name) = lower(%s)", (reference,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("SELECT id, name FROM targets WHERE lower(dbname) = lower(%s)", (reference,))
            rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0][0], rows[0][1]
    cur.execute("SELECT name FROM targets ORDER BY name")
    names = ", ".join(row[0] for row in cur.fetchall()) or "none configured"
    if rows:
        raise ToolError(f"'{reference}' matches several targets; use the id from list_targets. Targets: {names}.")
    raise ToolError(f"No target '{reference}'. Targets: {names}.")


def _target(reference: str) -> tuple[uuid.UUID, str]:
    with store_conn() as conn, conn.cursor() as cur:
        return resolve_target(cur, reference)


# Session and owner role names inside finding text (app/xmin_horizon.py's
# "Session 123 (jane, idle in transaction)", "user=jane", "owner=jane",
# "connect … as jane (or a superuser)"). A login is often a person's.
_IDENTITY_PATTERNS = (
    (re.compile(r"\b(user|owner)=\S+"), r"\1=(hidden)"),
    (re.compile(r"(Session \d+) \(.*?\) (has kept)"), r"\1 \2"),
    (re.compile(r"(\bdatabase \S+ )as .+? (\(or a superuser\))"), r"\1as its owner \2"),
)


def scrub_identities(value):
    """Every string in a tool result, recursively, with session/owner role
    names removed. Applied once to every tool result at the MCP boundary
    (app/mcp_server.py), so no tool can forget it."""
    if isinstance(value, str):
        for pattern, replacement in _IDENTITY_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {key: scrub_identities(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [scrub_identities(item) for item in value]
    return value


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _masked(text: str | None, limit: int = 600) -> str | None:
    masked = " ".join((mask_query(text) or "").split())
    return masked if len(masked) <= limit else masked[: limit - 1] + "…"


def _call_check(fn, *args):
    """Runs an existing endpoint function; its HTTP errors become tool
    errors with the same message."""
    try:
        return fn(*args)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def _finding_out(finding: dict, include_detail: bool = True) -> dict:
    out = {
        "finding_id": finding.get("id"),
        "severity": finding.get("severity"),
        "category": finding.get("category"),
        "title": finding.get("title"),
        "summary": finding.get("summary"),
    }
    if include_detail:
        out["detail"] = finding.get("detail")
        out["suggested_action"] = finding.get("suggested_action")
        if finding.get("recommended_ddl"):
            out["recommended_ddl"] = finding["recommended_ddl"]
        if finding.get("schema_name"):
            out["table"] = f"{finding['schema_name']}.{finding.get('table_name')}"
        occurrences = finding.get("occurrences") or []
        if occurrences:
            out["queries"] = [{"query": _masked(o.get("query")), "calls": o.get("calls"), "note": o.get("note")} for o in occurrences[:5]]
    return out


# --- Tools -------------------------------------------------------------------------


def list_targets(_args: dict) -> dict:
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.name, t.host, t.port, t.dbname, t.detected_pg_version, t.is_active, t.last_test_ok,
                   count(f.id) FILTER (WHERE f.finding->>'severity' = 'critical'),
                   count(f.id) FILTER (WHERE f.finding->>'severity' = 'attention')
            FROM targets t
            LEFT JOIN deep_scan_findings f ON f.target_id = t.id AND f.status = 'open'
            GROUP BY t.id
            ORDER BY t.name
            """
        )
        rows = cur.fetchall()
    return {
        "targets": [
            {
                "id": str(tid),
                "name": name,
                "database": f"{host}:{port}/{dbname}",
                "postgresql_version": version,
                "monitoring_active": active,
                "last_connection_test_ok": test_ok,
                "open_findings": {"critical": critical, "attention": attention},
            }
            for tid, name, host, port, dbname, version, active, test_ok, critical, attention in rows
        ]
    }


def get_target_overview(args: dict) -> dict:
    target_id, name = _target(args["target"])
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT finding_id, category, finding->>'severity', finding->>'title', finding->>'summary', first_seen_at
            FROM deep_scan_findings WHERE target_id = %s AND status = 'open'
            """,
            (target_id,),
        )
        open_rows = cur.fetchall()
        cur.execute(
            """
            SELECT DISTINCT ON (metric_name) metric_name, value, collected_at
            FROM metric_points
            WHERE target_id = %s AND metric_name = ANY(%s) AND collected_at > now() - interval '1 hour'
            ORDER BY metric_name, collected_at DESC
            """,
            (target_id, list(METRICS)),
        )
        latest = {metric: (value, at) for metric, value, at in cur.fetchall()}
        cur.execute(LOCK_SNAPSHOTS_SQL, (target_id, since, datetime.now(timezone.utc)))
        incidents = group_incidents([SnapshotSummary(*row) for row in cur.fetchall()])
        cur.execute(
            """
            SELECT count(*) FILTER (WHERE first_seen_at >= %s), count(*) FILTER (WHERE resolved_at >= %s)
            FROM deep_scan_findings WHERE target_id = %s
            """,
            (since, since, target_id),
        )
        opened, resolved = cur.fetchone()
        cur.execute(
            "SELECT kind, occurred_at FROM target_events WHERE target_id = %s AND occurred_at >= %s ORDER BY occurred_at DESC",
            (target_id, since),
        )
        events = [{"kind": kind, "at": _iso(at)} for kind, at in cur.fetchall()]
        cur.execute(
            """
            SELECT DISTINCT ON (job_name) job_name, status, finished_at, error
            FROM collector_runs WHERE target_id = %s AND finished_at > now() - interval '1 hour'
            ORDER BY job_name, finished_at DESC
            """,
            (target_id,),
        )
        collectors = [
            {"job": job, "ok": status == "ok", "at": _iso(at), **({"error": (error or "")[:300]} if status != "ok" else {})}
            for job, status, at, error in cur.fetchall()
        ]

    by_severity: dict[str, int] = {}
    for _fid, _category, severity, *_rest in open_rows:
        by_severity[severity] = by_severity.get(severity, 0) + 1
    open_rows.sort(key=lambda r: (SEVERITY_ORDER.get(r[2], 9), r[5]))
    overall = "critical" if by_severity.get("critical") else "attention" if by_severity.get("attention") else "healthy"
    return {
        "target": {"id": str(target_id), "name": name},
        "overall_severity": overall,
        "open_findings": by_severity,
        "top_findings": [
            {"finding_id": fid, "severity": sev, "category": cat, "title": title, "summary": summary, "open_since": _iso(at)}
            for fid, cat, sev, title, summary, at in open_rows[:10]
        ],
        "latest_metrics": {
            metric: {"label": METRICS[metric][0], "unit": METRICS[metric][1], "value": value, "at": _iso(at)}
            for metric, (value, at) in latest.items()
        },
        "last_24h": {
            "findings_opened": opened,
            "findings_resolved": resolved,
            "lock_incidents": len(incidents),
            "longest_lock_wait_seconds": max((i["max_wait_seconds"] or 0 for i in incidents), default=None),
            "events": events[:20],
        },
        "collectors": collectors,
        "next_steps": "list_findings for details and fixes; run_diagnosis for a live check; get_top_queries, "
        "get_lock_incidents and get_plan_regressions for the history behind them.",
    }


def list_findings(args: dict) -> dict:
    target_id, name = _target(args["target"])
    clauses = ["target_id = %s", "status = %s"]
    params: list = [target_id, args["status"]]
    if args.get("severity") and args["severity"] != "any":
        clauses.append("finding->>'severity' = %s")
        params.append(args["severity"])
    if args.get("category"):
        clauses.append("lower(category) = lower(%s)")
        params.append(args["category"])
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT finding, first_seen_at, last_seen_at, resolved_at
            FROM deep_scan_findings WHERE {' AND '.join(clauses)}
            ORDER BY CASE finding->>'severity' WHEN 'critical' THEN 0 WHEN 'attention' THEN 1 ELSE 2 END,
                     first_seen_at
            LIMIT %s
            """,
            (*params, args["limit"]),
        )
        rows = cur.fetchall()
        cur.execute(
            "SELECT DISTINCT category FROM deep_scan_findings WHERE target_id = %s ORDER BY 1",
            (target_id,),
        )
        categories = [row[0] for row in cur.fetchall()]
    return {
        "target": name,
        "status": args["status"],
        "findings": [
            {**_finding_out(finding), "first_seen_at": _iso(first), "last_seen_at": _iso(last), "resolved_at": _iso(resolved)}
            for finding, first, last, resolved in rows
        ],
        "categories": categories,
    }


def run_diagnosis(args: dict) -> dict:
    target_id, name = _target(args["target"])
    result = _call_check(diagnose, target_id).model_dump(mode="json")
    for query in result.get("top_queries", []):
        query["query"] = _masked(query["query"])
    return {"target": name, **result}


def get_top_queries(args: dict) -> dict:
    target_id, name = _target(args["target"])
    order = {"total_time": "4 DESC", "mean_time": "5 DESC", "calls": "3 DESC"}[args["order_by"]]
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT tq.queryid, tq.query_text, sum(s.calls)::bigint, sum(s.calls * s.mean_exec_ms),
                   sum(s.calls * s.mean_exec_ms) / nullif(sum(s.calls), 0), sum(s.rows)::bigint
            FROM query_stat_snapshots s
            JOIN tracked_queries tq ON tq.id = s.tracked_query_id
            WHERE tq.target_id = %s AND s.collected_at >= now() - (%s * interval '1 hour')
            GROUP BY tq.queryid, tq.query_text
            ORDER BY {order} NULLS LAST
            LIMIT %s
            """,
            (target_id, args["hours"], args["limit"]),
        )
        rows = cur.fetchall()
    return {
        "target": name,
        "hours": args["hours"],
        "note": "From DbButler's history of the 50 heaviest pg_stat_statements entries; literals are masked.",
        "queries": [
            {
                "queryid": str(queryid),
                "query": _masked(text),
                "calls": calls,
                "total_ms": round(total or 0, 1),
                "mean_ms": round(mean, 3) if mean is not None else None,
                "rows": rows_total,
            }
            for queryid, text, calls, total, mean, rows_total in rows
        ],
    }


def get_plan_regressions_tool(args: dict) -> dict:
    target_id, name = _target(args["target"])
    regressions = []
    for finding in get_plan_regressions(target_id).findings:
        detail, _sep, query = finding.detail.partition("\n\nQuery: ")
        regressions.append(
            {
                "queryid": finding.id.removeprefix("plan-regression-"),
                "severity": finding.severity,
                "title": finding.title,
                "summary": finding.summary,
                "plan_change": detail,
                "query": _masked(query) if query else None,
                "suggested_action": finding.suggested_action,
            }
        )
    return {"target": name, "regressions": regressions}


def get_metric_history(args: dict) -> dict:
    target_id, name = _target(args["target"])
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args["hours"])
    bucket = max(timedelta(seconds=30), (end - start) / args["points"])
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT date_bin(%s, collected_at, %s), avg(value), min(value), max(value)
            FROM metric_points
            WHERE target_id = %s AND metric_name = %s AND collected_at >= %s AND collected_at <= %s
            GROUP BY 1 ORDER BY 1
            """,
            (bucket, start, target_id, args["metric"], start, end),
        )
        rows = cur.fetchall()
    label, unit = METRICS[args["metric"]]
    values = [avg for _ts, avg, _low, _high in rows]
    return {
        "target": name,
        "metric": args["metric"],
        "label": label,
        "unit": unit,
        "bucket_seconds": int(bucket.total_seconds()),
        "summary": {
            "min": min((low for _ts, _avg, low, _high in rows), default=None),
            "max": max((high for _ts, _avg, _low, high in rows), default=None),
            "avg": sum(values) / len(values) if values else None,
            "first": values[0] if values else None,
            "last": values[-1] if values else None,
        },
        "points": [{"at": _iso(ts), "avg": round(avg, 3)} for ts, avg, _low, _high in rows],
    }


def get_lock_incidents(args: dict) -> dict:
    target_id, name = _target(args["target"])
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args["hours"])
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(LOCK_SNAPSHOTS_SQL, (target_id, start, end))
        incidents = group_incidents([SnapshotSummary(*row) for row in cur.fetchall()])
        incidents.sort(key=lambda i: (i["max_wait_seconds"] or 0, i["max_blocked"]), reverse=True)
        out = []
        for incident in incidents[:10]:
            cur.execute(
                """
                SELECT collected_at, sessions FROM lock_snapshots
                WHERE target_id = %s AND collected_at >= %s AND collected_at <= %s
                ORDER BY blocked_count DESC, collected_at LIMIT 1
                """,
                (target_id, incident["start"], incident["end"]),
            )
            peak = cur.fetchone()
            sessions = peak[1] if peak else []
            roots = set(root_blockers(sessions))
            out.append(
                {
                    "start": _iso(incident["start"]),
                    "end": _iso(incident["end"]),
                    "duration_seconds": int((incident["end"] - incident["start"]).total_seconds()),
                    "max_blocked_sessions": incident["max_blocked"],
                    "longest_wait_seconds": incident["max_wait_seconds"],
                    "peak_snapshot_at": _iso(peak[0]) if peak else None,
                    "sessions_at_peak": [
                        {
                            "pid": s["pid"],
                            "root_blocker": s["pid"] in roots,
                            "blocked_by": s["blocked_by"],
                            "application_name": s.get("application_name"),
                            "state": s.get("state"),
                            "waiting_on": s.get("waiting_on"),
                            "wait_seconds": s.get("wait_seconds"),
                            "transaction_seconds": s.get("xact_seconds"),
                            "query": _masked(s.get("query"), 300),
                        }
                        for s in sessions[:25]
                    ],
                }
            )
    return {
        "target": name,
        "hours": args["hours"],
        "incident_count": len(incidents),
        "note": "A lock snapshot is taken every 15s while some session has waited on a lock for 1s or more.",
        "incidents": out,
    }


def get_workload_map(args: dict) -> dict:
    target_id, name = _target(args["target"])
    graph = _call_check(lambda: collect_workload(target_id, args["hours"]).graph)
    nodes = sorted(graph["nodes"], key=lambda n: n["share_pct"], reverse=True)
    edges = graph["edges"]

    def edge_out(edge: dict) -> dict:
        return {
            "tables": [edge["source"], edge["target"]],
            "share_pct_of_db_time": edge["share_pct"],
            "columns": edge["column_pairs"],
            "join_strategies": edge["join_strategies"],
            "indexed": {"left": edge["source_indexed"], "right": edge["target_indexed"]},
        }

    return {
        "target": name,
        "hours": args["hours"],
        "coverage_pct": graph["meta"]["coverage_pct"],
        "edge_counts": graph["meta"]["edge_counts"],
        "busiest_tables": [
            {
                "table": n["id"],
                "share_pct_of_db_time": n["share_pct"],
                "total_bytes": n["total_bytes"],
                "access": n["access_kinds"],
                "filter_columns": n["filter_columns"][:10],
                "worst_open_finding": n["severity"],
            }
            for n in nodes[:15]
            if n["in_workload"]
        ],
        "joins_without_foreign_key": [edge_out(e) for e in edges if e["kind"] == "no_fk"][:15],
        "foreign_keys_never_joined": [
            {"tables": [e["source"], e["target"]], "columns": e["column_pairs"], "child_side_indexed": e["source_indexed"] and e["target_indexed"]}
            for e in edges
            if e["kind"] == "fk_unused"
        ][:15],
        "busiest_joins": [edge_out(e) for e in edges if e["kind"] != "fk_unused"][:10],
    }


def get_refactoring_suggestions(args: dict) -> dict:
    target_id, name = _target(args["target"])
    result = _call_check(get_refactoring_advisor, target_id, args["hours"])
    return {
        "target": name,
        "hours": args["hours"],
        "suggestions": [_finding_out(f.model_dump(mode="json")) for f in result.findings],
    }


def lint_migration(args: dict) -> dict:
    target_id = _target(args["target"])[0] if args.get("target") else None
    return _call_check(run_migration_lint, args["sql"], target_id)


def get_weekly_report(args: dict) -> dict:
    target_id, name = _target(args["target"])
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, generated_at, data FROM health_reports WHERE target_id = %s ORDER BY generated_at DESC LIMIT 1",
            (target_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ToolError(f"No weekly report for {name} yet (one is generated every Monday, or from the Reports screen).")
    report_id, generated_at, data = row
    return {"target": name, "report_id": str(report_id), "generated_at": _iso(generated_at), "report": data}


HOURS_ARG = {"type": "integer", "minimum": 1, "maximum": 720, "default": 24, "description": "How far back to look, in hours."}
WORKLOAD_HOURS_ARG = {**HOURS_ARG, "default": 168}

TOOLS = [
    Tool(
        "list_targets",
        "List monitored databases",
        "The PostgreSQL databases DbButler monitors, with their open critical/attention finding counts. Start here.",
        _schema({}, []),
        list_targets,
    ),
    Tool(
        "get_target_overview",
        "What's wrong with a database",
        "One database's state at a glance: overall severity, the top open findings, latest metrics, the last 24 hours "
        "(findings opened/resolved, lock incidents, restarts, configuration changes) and collector health. "
        "The best first call for 'what's wrong with X?'.",
        _schema({"target": TARGET_ARG}, ["target"]),
        get_target_overview,
    ),
    Tool(
        "list_findings",
        "List findings",
        "Findings (health checks, Advisor rules, Table Health, ...) with detail, the suggested fix and any recommended "
        "DDL. Open ones by default, most severe first.",
        _schema(
            {
                "target": TARGET_ARG,
                "severity": {"type": "string", "enum": ["any", "critical", "attention"], "default": "any"},
                "category": {"type": "string", "maxLength": 100, "description": "e.g. 'index advisor', 'bloat', 'locks'."},
                "status": {"type": "string", "enum": ["open", "resolved"], "default": "open"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 25},
            },
            ["target"],
        ),
        list_findings,
    ),
    Tool(
        "run_diagnosis",
        "Run a live diagnosis",
        "Runs DbButler's quick runbook against the database now (replication, locks, cache hit rate, bloat, top "
        "queries) with its read-only monitoring connection.",
        _schema({"target": TARGET_ARG}, ["target"]),
        run_diagnosis,
    ),
    Tool(
        "get_top_queries",
        "Heaviest queries",
        "The queries that used the most database time (or were slowest / most frequent) over a past window, from "
        "DbButler's pg_stat_statements history. Literal values are masked.",
        _schema(
            {
                "target": TARGET_ARG,
                "hours": HOURS_ARG,
                "order_by": {"type": "string", "enum": ["total_time", "mean_time", "calls"], "default": "total_time"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
            },
            ["target"],
        ),
        get_top_queries,
    ),
    Tool(
        "get_plan_regressions",
        "Plan regressions",
        "Queries whose execution plan changed shape and got slower since, with the before/after plan summary.",
        _schema({"target": TARGET_ARG}, ["target"]),
        get_plan_regressions_tool,
    ),
    Tool(
        "get_metric_history",
        "Metric history",
        "A collected metric over a past window, bucketed (min/avg/max and points) — e.g. to see when connections or "
        "dead rows started climbing.",
        _schema(
            {
                "target": TARGET_ARG,
                "metric": {"type": "string", "enum": list(METRICS)},
                "hours": HOURS_ARG,
                "points": {"type": "integer", "minimum": 10, "maximum": 200, "default": 48},
            },
            ["target", "metric"],
        ),
        get_metric_history,
    ),
    Tool(
        "get_lock_incidents",
        "Lock incidents",
        "Recorded lock incidents (sessions blocked by other sessions) in a past window, worst first, with who blocked "
        "whom at each incident's peak.",
        _schema({"target": TARGET_ARG, "hours": HOURS_ARG}, ["target"]),
        get_lock_incidents,
    ),
    Tool(
        "get_workload_map",
        "Workload map",
        "How the tables are really used and joined, from the plans of the tracked queries: the busiest tables, the "
        "busiest joins, joins with no foreign key behind them and foreign keys nobody joins on.",
        _schema({"target": TARGET_ARG, "hours": WORKLOAD_HOURS_ARG}, ["target"]),
        get_workload_map,
    ),
    Tool(
        "get_refactoring_suggestions",
        "Schema refactoring suggestions",
        "The Refactoring Advisor: missing foreign keys and indexes for hot joins, partitioning and denormalization "
        "candidates, unused indexes and more, with DDL.",
        _schema({"target": TARGET_ARG, "hours": WORKLOAD_HOURS_ARG}, ["target"]),
        get_refactoring_suggestions,
    ),
    Tool(
        "lint_migration",
        "Lint a migration",
        "Checks a SQL migration without running it: what each statement locks and for how long, risky patterns "
        "(non-concurrent index, table rewrites, missing lock_timeout, validation under lock, ...) and, with a target, "
        "the impact on that database right now (table sizes, sessions holding conflicting locks, traffic).",
        _schema(
            {
                "sql": {"type": "string", "maxLength": 200_000, "description": "The migration's SQL text."},
                "target": {**TARGET_ARG, "description": "Optional: predict the impact on this database."},
            },
            ["sql"],
        ),
        lint_migration,
    ),
    Tool(
        "get_weekly_report",
        "Latest weekly report",
        "The most recent weekly health report: a week of findings, metrics, heaviest queries, lock incidents, plan "
        "changes, table growth and server events.",
        _schema({"target": TARGET_ARG}, ["target"]),
        get_weekly_report,
    ),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
