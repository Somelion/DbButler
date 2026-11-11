"""Vacuum horizon (xmin) holders: the #1 reason "autovacuum runs but nothing
gets cleaned up".

VACUUM can only remove a row version once no snapshot that might still see
it exists. Three things keep such a snapshot alive:

- a session with an old transaction or snapshot (backend_xmin), including a
  walsender whose standby runs with hot_standby_feedback = on;
- a prepared (two-phase) transaction nobody committed or rolled back —
  it survives restarts and keeps its locks too;
- a replication slot's xmin / catalog_xmin, which holds back every database
  in the cluster.
"""

from app.schemas import CategoryStatus
from app.severity import SEVERITY_RANK, worse, xmin_horizon_severity
from app.sql_ident import quote_literal

# Sessions and prepared transactions only hold back the horizon of their own
# database (a walsender has no database and counts everywhere); a slot holds
# back the whole cluster. Autovacuum workers and plain (non-FULL) VACUUMs
# are left out: PostgreSQL itself ignores their snapshots when computing the
# horizon. Every column is cast so the three branches line up in the UNION.
XMIN_HOLDERS_QUERY = """
    SELECT
        'backend'::text AS kind,
        pid::text AS holder,
        datname::text AS database,
        usename::text AS owner,
        state::text AS state,
        backend_type::text AS detail_type,
        age(backend_xmin)::bigint AS xmin_age,
        EXTRACT(EPOCH FROM now() - coalesce(xact_start, backend_start))::bigint AS held_seconds
    FROM pg_stat_activity
    WHERE backend_xmin IS NOT NULL
      AND pid <> pg_backend_pid()
      AND backend_type <> 'autovacuum worker'
      AND (datname IS NULL OR datname = current_database())
      AND NOT (coalesce(query, '') ~* '^\\s*vacuum\\M' AND coalesce(query, '') !~* '\\mfull\\M')
    UNION ALL
    SELECT
        'prepared'::text,
        gid::text,
        database::text,
        owner::text,
        NULL::text,
        NULL::text,
        age(transaction)::bigint,
        EXTRACT(EPOCH FROM now() - prepared)::bigint
    FROM pg_prepared_xacts
    WHERE database = current_database()
    UNION ALL
    SELECT
        'slot'::text,
        slot_name::text,
        database::text,
        NULL::text,
        CASE WHEN active THEN 'active' ELSE 'inactive' END,
        slot_type::text,
        greatest(age(xmin), age(catalog_xmin))::bigint,
        NULL::bigint
    FROM pg_replication_slots
    WHERE xmin IS NOT NULL OR catalog_xmin IS NOT NULL
"""

# One finding per holder is useful; fifty idle sessions from a leaky pool
# aren't fifty separate insights — the worst few are enough to act on.
MAX_XMIN_FINDINGS = 5

CATEGORY = "xmin horizon"


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "an unknown time"
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 2 * 3600:
        return f"{seconds // 60} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def _held_for(xmin_age, held_seconds) -> str:
    if held_seconds is not None:
        return f"for {format_duration(held_seconds)} ({xmin_age:,} transactions ago)"
    return f"since {xmin_age:,} transactions ago"


def _backend_finding(holder, database, owner, state, detail_type, xmin_age, held_seconds, severity) -> dict:
    held = _held_for(xmin_age, held_seconds)
    if detail_type == "walsender":
        return {
            "id": f"xmin-holder-walsender-{holder}",
            "title": "A standby is holding back vacuum on this server",
            "summary": (
                f"Replication connection {holder} has reported an old snapshot {held}. With "
                "hot_standby_feedback = on, a long-running query on the standby stops VACUUM here from "
                "removing rows that query might still read."
            ),
            "suggested_action": (
                "Find and end the long query on the standby. If cancelling standby queries is acceptable, "
                "turn hot_standby_feedback off there and rely on max_standby_streaming_delay instead."
            ),
            "detail": f"pid={holder} backend_type=walsender xmin_age={xmin_age} held_seconds={held_seconds}",
            "severity": severity,
        }
    who = f"{owner}, {state}" if owner and state else (owner or state or "unknown user")
    return {
        "id": f"xmin-holder-backend-{holder}",
        "title": f"Session {holder} is holding back vacuum",
        "summary": (
            f"Session {holder} ({who}) has kept the same transaction or snapshot open {held}. Until it "
            "ends, VACUUM can't remove any row deleted or updated after it started, on any table in this "
            "database, so dead rows pile up even though autovacuum keeps running."
        ),
        "suggested_action": (
            "Open Activity to see what it's running. If it shouldn't still be open, end it with "
            f"SELECT pg_terminate_backend({holder}); To stop it recurring, set "
            "idle_in_transaction_session_timeout and fix the code path that leaves transactions open."
        ),
        "detail": (
            f"pid={holder} database={database} user={owner} state={state} "
            f"xmin_age={xmin_age} held_seconds={held_seconds}"
        ),
        "severity": severity,
    }


def _prepared_finding(holder, database, owner, xmin_age, held_seconds, severity) -> dict:
    return {
        "id": f"xmin-holder-prepared-{holder}",
        "title": f"Prepared transaction {quote_literal(holder)} was never finished",
        "summary": (
            f"A two-phase transaction was prepared {format_duration(held_seconds)} ago and has been "
            "neither committed nor rolled back. It holds back VACUUM for this whole database and keeps its "
            "row locks, and it survives restarts."
        ),
        "suggested_action": (
            "If no transaction manager is going to finish it, connect to database "
            f"{database} as {owner} (or a superuser) and run ROLLBACK PREPARED {quote_literal(holder)}; "
            "(or COMMIT PREPARED if its work should be kept)."
        ),
        "detail": f"gid={holder} database={database} owner={owner} xmin_age={xmin_age} held_seconds={held_seconds}",
        "severity": severity,
    }


def _slot_finding(holder, state, detail_type, xmin_age, severity) -> dict:
    return {
        "id": f"xmin-holder-slot-{holder}",
        "title": f"Replication slot {holder} is holding back vacuum",
        "summary": (
            f"This {detail_type or ''} slot ({state}) still needs rows from {xmin_age:,} transactions ago. "
            "While it does, VACUUM can't remove them (for a logical slot, in the system catalogs) in any "
            "database on this server."
        ),
        "suggested_action": (
            "Reconnect the slot's consumer so it can catch up. If the consumer is gone for good, drop the "
            f"slot: SELECT pg_drop_replication_slot({quote_literal(holder)});"
        ),
        "detail": f"slot={holder} slot_type={detail_type} state={state} xmin_age={xmin_age}",
        "severity": severity,
    }


def build_xmin_horizon_status(holder_rows) -> tuple[CategoryStatus, list[dict]]:
    """One Dashboard tile plus one finding per holder past the thresholds
    (worst first, at most MAX_XMIN_FINDINGS). The tile shows the oldest
    holder even when it's still healthy, so "who's holding the horizon"
    stays visible before it becomes a problem."""
    if not holder_rows:
        return (
            CategoryStatus(
                key=CATEGORY, label="Vacuum Horizon", severity="healthy", value="clear", detail="no long-held snapshots"
            ),
            [],
        )

    rated = []
    for kind, holder, database, owner, state, detail_type, xmin_age, held_seconds in holder_rows:
        xmin_age = xmin_age or 0
        severity = xmin_horizon_severity(xmin_age, held_seconds)
        rated.append((severity, kind, holder, database, owner, state, detail_type, xmin_age, held_seconds))
    rated.sort(key=lambda r: (SEVERITY_RANK[r[0]], r[7]), reverse=True)

    findings = []
    overall = "healthy"
    for severity, kind, holder, database, owner, state, detail_type, xmin_age, held_seconds in rated:
        overall = worse(overall, severity)
        if severity == "healthy" or len(findings) >= MAX_XMIN_FINDINGS:
            continue
        if kind == "prepared":
            finding = _prepared_finding(holder, database, owner, xmin_age, held_seconds, severity)
        elif kind == "slot":
            finding = _slot_finding(holder, state, detail_type, xmin_age, severity)
        else:
            finding = _backend_finding(holder, database, owner, state, detail_type, xmin_age, held_seconds, severity)
        findings.append({"category": CATEGORY, **finding})

    _, kind, holder, _database, _owner, _state, detail_type, xmin_age, held_seconds = rated[0]
    label = {"prepared": f"prepared transaction {quote_literal(holder)}", "slot": f"slot {holder}"}.get(
        kind, "standby feedback" if detail_type == "walsender" else f"session {holder}"
    )
    value = format_duration(held_seconds) if held_seconds is not None else f"{xmin_age:,} xids"
    return (
        CategoryStatus(key=CATEGORY, label="Vacuum Horizon", severity=overall, value=value, detail=f"oldest: {label}"),
        findings,
    )
