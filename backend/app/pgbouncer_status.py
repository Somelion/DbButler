"""Pure aggregation over PgBouncer's SHOW POOLS rows, scoped to the
target's own database — PgBouncer can proxy many databases at once, but
this app only cares about the one behind the currently connected target.
Feeds routers/dashboard.py's optional "Pooler" category/finding, only ever
present when a PgBouncer connection has been configured for this target
(app/pgbouncer_conn.py) — a complete blind spot before this feature.
"""

from app.schemas import CategoryStatus, Finding
from app.severity import pooler_severity


def build_pooler_status(pool_rows: list[dict], dbname: str) -> tuple[CategoryStatus | None, Finding | None]:
    """pool_rows: dicts built from SHOW POOLS' own column names (routers/
    dashboard.py reads cur.description rather than trusting a fixed column
    order, since PgBouncer versions have added columns over time). Returns
    (None, None) if nothing has routed through PgBouncer for this database
    yet — a quiet database that's configured but unused isn't a "healthy"
    pooler tile, it's simply nothing to report."""
    matching = [row for row in pool_rows if row.get("database") == dbname]
    if not matching:
        return None, None

    cl_active = sum(row.get("cl_active") or 0 for row in matching)
    cl_waiting = sum(row.get("cl_waiting") or 0 for row in matching)
    sv_active = sum(row.get("sv_active") or 0 for row in matching)
    sv_idle = sum(row.get("sv_idle") or 0 for row in matching)
    maxwait = max((row.get("maxwait") or 0) for row in matching)
    pool_mode = next((row.get("pool_mode") for row in matching if row.get("pool_mode")), "unknown")

    severity = pooler_severity(cl_waiting, maxwait)
    category = CategoryStatus(
        key="pooler",
        label="Pooler",
        severity=severity,
        value=f"{cl_waiting} waiting" if cl_waiting else f"{cl_active} active",
        detail=f"{pool_mode} pooling, {sv_active} server conn{'s' if sv_active != 1 else ''} in use",
    )

    if severity == "healthy":
        return category, None

    finding = Finding(
        id="pooler-clients-waiting",
        category="pooler",
        severity=severity,
        title="Clients are waiting for a pooled connection",
        summary=(
            f"{cl_waiting} client(s) are currently waiting for a free PgBouncer server connection "
            f"(longest wait: {maxwait}s)."
        ),
        detail=(
            f"pool_mode={pool_mode} cl_active={cl_active} cl_waiting={cl_waiting} sv_active={sv_active} "
            f"sv_idle={sv_idle} maxwait={maxwait}"
        ),
        suggested_action=(
            "Raising max_connections on Postgres itself often makes this worse, not better — check "
            "whether PgBouncer's own pool_size/default_pool_size is undersized for current demand, or "
            "whether a slow query is holding server connections longer than usual."
        ),
    )
    return category, finding
