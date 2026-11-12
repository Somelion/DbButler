"""Incident Time Machine: pure helpers for the lock-tree snapshots, target
events and the correlated timeline (scheduler.py captures,
routers/timeline.py serves).

Nothing here talks to a database; the SQL lives with its callers.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.query_anonymizer import LiteralMasker

QUERY_TEXT_LIMIT = 1000
# Snapshots are taken every 15s while anyone is blocked. A gap longer than
# this between two of them starts a new incident.
INCIDENT_GAP = timedelta(seconds=60)
MAX_PROMPT_INCIDENTS = 10


# --- Lock-tree snapshots ------------------------------------------------------

# Columns of scheduler.py's LOCK_TREE_QUERY, in order.
LOCK_TREE_COLUMNS = (
    "pid",
    "blocked_by",
    "usename",
    "application_name",
    "backend_type",
    "state",
    "wait_event_type",
    "wait_event",
    "query",
    "query_seconds",
    "xact_seconds",
    "state_seconds",
)


TRUNCATED_LITERAL = "'…'"


def strip_comments_and_open_literal(text: str) -> str:
    """Removes comments (sqlcommenter tags and the like can carry user ids)
    and cuts off a string literal left open at the end — pg_stat_activity
    truncates query text at track_activity_query_size, often mid-literal,
    and a literal without its closing quote would slip past the masker.
    A small scanner rather than regexes, so a quote inside a comment or a
    comment marker inside a string doesn't fool it."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            end = i + 1
            while end < n:
                if text[end] == "'":
                    if end + 1 < n and text[end + 1] == "'":
                        end += 2
                        continue
                    break
                end += 1
            if end >= n:  # never closed: the text was cut inside it
                out.append(TRUNCATED_LITERAL)
                break
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("--", i):
            newline = text.find("\n", i)
            out.append(" ")
            i = n if newline == -1 else newline
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            out.append(" ")
            i = n if close == -1 else close + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def mask_query(text: str | None) -> str | None:
    """Literal values replaced by placeholders, comments dropped: snapshots
    are kept for weeks, and a query's literals can be personal data."""
    if not text:
        return text
    masked = LiteralMasker().mask_query(strip_comments_and_open_literal(text))
    return masked if len(masked) <= QUERY_TEXT_LIMIT else masked[: QUERY_TEXT_LIMIT - 1] + "…"


def build_lock_snapshot(rows: list[tuple], waits: dict[int, tuple[str, int | None]]) -> dict | None:
    """rows: LOCK_TREE_QUERY output (the waiting sessions and whoever blocks
    them). waits: pid -> ("Mode on resource", seconds waited) for sessions
    waiting on an ungranted lock; seconds is None before PostgreSQL 14
    (no pg_locks.waitstart), when the time since the session's last state
    change stands in — an upper bound, as it includes run time before the
    wait. Returns {blocked_count, max_wait_seconds, sessions}, or None when
    nobody is actually blocked (the wait cleared between the reads)."""
    sessions = []
    for row in rows:
        session = dict(zip(LOCK_TREE_COLUMNS, row, strict=True))
        session["blocked_by"] = sorted(set(session["blocked_by"] or []))
        session["query"] = mask_query(session["query"])
        waiting_on, wait_seconds = waits.get(session["pid"], (None, None))
        session["waiting_on"] = waiting_on
        session["wait_seconds"] = (
            wait_seconds if wait_seconds is not None else (session["state_seconds"] if session["blocked_by"] else None)
        )
        sessions.append(session)
    blocked = [s for s in sessions if s["blocked_by"]]
    if not blocked:
        return None
    waits = [s["wait_seconds"] for s in blocked if s["wait_seconds"] is not None]
    sessions.sort(key=lambda s: (bool(s["blocked_by"]), s["pid"]))
    return {
        "blocked_count": len(blocked),
        "max_wait_seconds": max(waits) if waits else None,
        "sessions": sessions,
    }


def root_blockers(sessions: list[dict]) -> list[int]:
    """Pids at the head of a chain: blocking someone, blocked by no one.
    A blocker that isn't in the snapshot (pid 0 = a prepared transaction,
    or a session in another database) counts as a root too."""
    blocking = {pid for s in sessions for pid in s["blocked_by"]}
    blocked_pids = {s["pid"] for s in sessions if s["blocked_by"]}
    return sorted(pid for pid in blocking if pid not in blocked_pids)


@dataclass
class SnapshotSummary:
    collected_at: datetime
    blocked_count: int
    max_wait_seconds: int | None


def group_incidents(snapshots: list[SnapshotSummary]) -> list[dict]:
    """Consecutive snapshots (no gap over INCIDENT_GAP) make one incident."""
    incidents: list[dict] = []
    for snap in sorted(snapshots, key=lambda s: s.collected_at):
        current = incidents[-1] if incidents else None
        if current is None or snap.collected_at - current["end"] > INCIDENT_GAP:
            current = {
                "start": snap.collected_at,
                "end": snap.collected_at,
                "snapshots": 0,
                "max_blocked": 0,
                "max_wait_seconds": None,
            }
            incidents.append(current)
        current["end"] = snap.collected_at
        current["snapshots"] += 1
        current["max_blocked"] = max(current["max_blocked"], snap.blocked_count)
        if snap.max_wait_seconds is not None:
            current["max_wait_seconds"] = max(current["max_wait_seconds"] or 0, snap.max_wait_seconds)
    return incidents


# --- Target events --------------------------------------------------------------


def diff_settings(previous: dict[str, str] | None, current: dict[str, str]) -> list[dict]:
    """Changed, added and removed settings between two pg_settings reads.
    None for `previous` (first read of this target) means no changes — a
    baseline, not "everything was just set"."""
    if previous is None:
        return []
    changes = []
    for name in sorted(set(previous) | set(current)):
        old, new = previous.get(name), current.get(name)
        if old != new:
            changes.append({"name": name, "old": old, "new": new})
    return changes


def setting_value(setting: str, unit: str | None) -> str:
    return f"{setting} {unit}" if unit else setting


# --- "Explain this incident" ------------------------------------------------------


def incident_facts(
    start: datetime,
    end: datetime,
    incidents: list[dict],
    lock_details: list[dict],
    plan_changes: list[dict],
    findings_opened: list[dict],
    events: list[dict],
    metrics: dict[str, dict],
) -> str:
    """The facts an AI gets to explain a time window — counts, names of
    tables/locks/applications, plan-shape summaries and finding titles. Never
    query text or row data (the Time Machine's own rule: send findings, not
    raw data)."""
    lines = [f"Window: {start.isoformat()} to {end.isoformat()} (UTC)."]
    if incidents:
        lines.append(f"Lock incidents: {len(incidents)}.")
        for incident in incidents[:MAX_PROMPT_INCIDENTS]:
            duration = int((incident["end"] - incident["start"]).total_seconds())
            lines.append(
                f"- {incident['start'].isoformat()} for ~{duration}s: up to {incident['max_blocked']} sessions "
                f"blocked, longest wait {incident['max_wait_seconds'] or 0}s."
            )
    else:
        lines.append("Lock incidents: none recorded.")
    for detail in lock_details:
        lines.append(
            f"- Waits seen: {', '.join(detail['waiting_on']) or 'unknown'}; blocking applications: "
            f"{', '.join(detail['blocker_apps']) or 'unknown'}; blockers' states: {', '.join(detail['blocker_states']) or 'unknown'}."
        )
    if plan_changes:
        lines.append(f"Query plan shape changes: {len(plan_changes)}.")
        lines.extend(f"- {p['at'].isoformat()}: {p['summary']}" for p in plan_changes[:10])
    if findings_opened:
        lines.append(f"Findings that opened: {len(findings_opened)}.")
        lines.extend(f"- {f['at'].isoformat()} [{f['severity']}] {f['title']}" for f in findings_opened[:15])
    if events:
        lines.append("Events:")
        for event in events[:15]:
            if event["kind"] == "config_change":
                names = ", ".join(c["name"] for c in event["detail"].get("changes", []))
                lines.append(f"- {event['at'].isoformat()} configuration changed: {names}")
            else:
                lines.append(f"- {event['at'].isoformat()} {event['kind'].replace('_', ' ')}")
    for label, values in metrics.items():
        lines.append(f"{label}: min {values['min']}, max {values['max']}, average {values['avg']}.")
    return "\n".join(lines)


EXPLAIN_PROMPT = """You are a PostgreSQL DBA writing a short post-mortem.
Below are facts DbButler recorded for one database during a time window:
lock incidents (sessions blocked by other sessions), query plan changes,
health findings that opened, server events and metric ranges. There is no
query text or data.

Write 4-8 sentences: what most likely happened, in what order, the likely
root cause, and what to check or change. Say plainly when the facts don't
support a conclusion. Plain text, no headings.

Facts:
{facts}
"""
