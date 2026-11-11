"""Wraparound coverage beyond one heap relfrozenxid per user table.

Transaction IDs and multixact IDs are both 32-bit counters that VACUUM must
freeze before they wrap. The Dashboard tile used to look only at user
tables' heap relfrozenxid; this adds:

- the database's own datfrozenxid, which also covers system catalogs, TOAST
  tables, temporary tables and schemas outside the allowlist;
- multixact ID age (mxid_age), for tables and for the database. Heavy use of
  row locks (SELECT ... FOR SHARE, foreign key checks) burns multixacts, and
  running out stops writes the same way XID wraparound does.

The per-table XID age itself comes from table_health.py's query, which now
takes the older of a table's heap and its TOAST table, and reports 0 for a
partitioned parent (it holds no rows, and its relfrozenxid of 0 would read
as the maximum age).
"""

from app.schemas import CategoryStatus
from app.severity import worse, wraparound_severity

DATABASE_WRAPAROUND_QUERY = """
    SELECT age(datfrozenxid)::bigint, mxid_age(datminmxid)::bigint
    FROM pg_database
    WHERE datname = current_database()
"""

# Same table scope as the XID side (ordinary and materialized views, no temp
# tables: autovacuum can't touch another session's temp table, and the
# database-level age above still catches one that's left behind).
WORST_TABLE_MXID_QUERY = """
    SELECT n.nspname, c.relname, greatest(mxid_age(c.relminmxid), mxid_age(t.relminmxid))::bigint
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_class t ON t.oid = c.reltoastrelid
    WHERE c.relkind IN ('r', 'm')
      AND c.relpersistence <> 't'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND (%(schemas)s::text[] IS NULL OR n.nspname = ANY(%(schemas)s::text[]))
    ORDER BY 3 DESC
    LIMIT 1
"""

FIND_OLDEST_RELATIONS_SQL = (
    "SELECT c.oid::regclass, age(c.relfrozenxid), mxid_age(c.relminmxid) FROM pg_class c "
    "WHERE c.relkind IN ('r', 't', 'm') ORDER BY greatest(age(c.relfrozenxid), mxid_age(c.relminmxid)) DESC LIMIT 5;"
)


def fetch_wraparound_inputs(cur, allowed_schemas: list[str] | None):
    """(database xid age, database mxid age, worst table mxid row or None)."""
    cur.execute(DATABASE_WRAPAROUND_QUERY)
    row = cur.fetchone()
    db_xid_age, db_mxid_age = row if row else (None, None)
    cur.execute(WORST_TABLE_MXID_QUERY, {"schemas": allowed_schemas or None})
    worst_mxid = cur.fetchone()
    return db_xid_age, db_mxid_age, worst_mxid


def _table_xid_finding(schema_name, table_name, xid_age, severity) -> dict:
    return {
        "id": f"wraparound-{schema_name}-{table_name}",
        "category": "wraparound",
        "severity": severity,
        "title": f"Table {schema_name}.{table_name} is approaching transaction ID wraparound",
        "summary": f"This table's transaction age is {xid_age:,}, which is getting close to the safety limit.",
        "detail": f"xid_age={xid_age}",
        "suggested_action": "Make sure autovacuum is running on this table; never disable autovacuum freeze.",
        "schema_name": schema_name,
        "table_name": table_name,
    }


def _table_mxid_finding(schema_name, table_name, mxid_age, severity) -> dict:
    return {
        "id": f"mxid-wraparound-{schema_name}-{table_name}",
        "category": "wraparound",
        "severity": severity,
        "title": f"Table {schema_name}.{table_name} is approaching multixact ID wraparound",
        "summary": (
            f"This table's oldest multixact ID is {mxid_age:,} old. Multixacts are created by row locks "
            "shared between transactions (foreign key checks, SELECT ... FOR SHARE); if they wrap, "
            "PostgreSQL stops accepting writes, just as with transaction ID wraparound."
        ),
        "detail": f"mxid_age={mxid_age}",
        "suggested_action": (
            "Run VACUUM (FREEZE) on this table and make sure autovacuum can keep up with it; check "
            "autovacuum_multixact_freeze_max_age."
        ),
        "schema_name": schema_name,
        "table_name": table_name,
    }


def _database_finding(kind: str, age: int, severity: str) -> dict:
    label = "transaction ID" if kind == "xid" else "multixact ID"
    return {
        "id": "wraparound-database" if kind == "xid" else "mxid-wraparound-database",
        "category": "wraparound",
        "severity": severity,
        "title": f"This database is approaching {label} wraparound",
        "summary": (
            f"The database's oldest unfrozen {label} is {age:,} old, but none of the tables shown here is "
            "that old. The table holding it back is a system catalog, a TOAST table, a temporary table left "
            "by a session, or in a schema outside this connection's allowlist."
        ),
        "detail": f"{kind}_age={age}",
        "suggested_action": (
            f"Find it with: {FIND_OLDEST_RELATIONS_SQL} Then run VACUUM (FREEZE) on it (for a TOAST table, "
            "vacuum the table that owns it). A temporary table left behind by a crashed session can only be "
            "dropped."
        ),
    }


def build_wraparound_status(
    worst_table_xid: tuple[str, str, int] | None,
    db_xid_age: int | None,
    worst_table_mxid: tuple[str, str, int] | None,
    db_mxid_age: int | None,
) -> tuple[CategoryStatus, list[dict]]:
    """The Wraparound tile and its findings. A table-level finding names the
    table to vacuum; the database-level one only appears when no table
    finding already explains the age, so the same problem isn't reported
    twice."""
    findings = []
    severity = "unknown"

    def track(sev):
        nonlocal severity
        severity = sev if severity == "unknown" else worse(severity, sev)

    for kind, worst_table, db_age, table_builder in (
        ("xid", worst_table_xid, db_xid_age, _table_xid_finding),
        ("mxid", worst_table_mxid, db_mxid_age, _table_mxid_finding),
    ):
        table_finding = None
        if worst_table is not None and worst_table[2] is not None:
            table_severity = wraparound_severity(worst_table[2])
            track(table_severity)
            if table_severity != "healthy":
                table_finding = table_builder(*worst_table, table_severity)
                findings.append(table_finding)
        if db_age is not None:
            db_severity = wraparound_severity(db_age)
            track(db_severity)
            if db_severity != "healthy" and table_finding is None:
                findings.append(_database_finding(kind, db_age, db_severity))

    xid_age = max((a for a in (worst_table_xid and worst_table_xid[2], db_xid_age) if a is not None), default=None)
    mxid_age = max((a for a in (worst_table_mxid and worst_table_mxid[2], db_mxid_age) if a is not None), default=None)
    if xid_age is None and mxid_age is None:
        return CategoryStatus(key="wraparound", label="Wraparound", severity="unknown", value="—", detail="no tables yet"), []

    detail = "oldest transaction age"
    if mxid_age is not None and wraparound_severity(mxid_age) not in ("healthy", "unknown"):
        detail += f" · multixact {mxid_age:,}"
    value = f"{xid_age:,}" if xid_age is not None else "—"
    return CategoryStatus(key="wraparound", label="Wraparound", severity=severity, value=value, detail=detail), findings
