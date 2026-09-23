"""Extension-aware Advisor analyzers — checks specific to popular
extensions (TimescaleDB, pgvector, PostGIS) rather than core Postgres.
Every check here is gated on the extension actually being installed
(routers/extension_advisor.py only runs the extension-specific catalog
query once `pg_extension` confirms it's present), so a target without any
of these extensions gets an empty findings list, not an error. Severities
stay soft (`unknown`/`attention`) — these are the same kind of missed-index
nudge Index Advisor already gives for ordinary tables, just for a column
type/catalog this app otherwise has no visibility into.
"""

CATEGORY = "extension advisor"

# A short, curated list of extensions this app already knows how to talk
# about elsewhere (Query Intelligence/Schema Lint, Security & Compliance,
# bloat remediation) — not an exhaustive catalog, just the ones worth
# nudging a DBA toward if they're missing. Each tuple is
# (extname, why, create_extension_note).
RECOMMENDED_EXTENSIONS = [
    (
        "pg_stat_statements",
        "Powers Query Intelligence, several Schema Lint checks, and the Plan Regression Detector — "
        "without it, this app can only see live activity, never historical query performance.",
        "Add 'pg_stat_statements' to shared_preload_libraries in postgresql.conf, restart PostgreSQL, "
        "then run: CREATE EXTENSION pg_stat_statements;",
    ),
    (
        "pgaudit",
        "Structured, SIEM-parseable audit logging of SQL activity — Security & Compliance already "
        "checks for this, but it's worth surfacing here too as a general-purpose recommendation.",
        "Add 'pgaudit' to shared_preload_libraries in postgresql.conf, restart PostgreSQL, then run: "
        "CREATE EXTENSION pgaudit;",
    ),
    (
        "pg_buffercache",
        "Lets a DBA inspect exactly what's currently sitting in shared_buffers, one page at a time — "
        "useful for confirming a cache-hit-rate problem is actually about the working set, not noise.",
        "CREATE EXTENSION pg_buffercache;",
    ),
    (
        "pg_repack",
        "Reclaims bloat without VACUUM FULL's exclusive table lock — a lower-risk alternative worth "
        "having installed before a bloat emergency, not during one.",
        "CREATE EXTENSION pg_repack; (also needs the pg_repack client tool installed alongside Postgres)",
    ),
]


def find_timescaledb_uncompressed_hypertable_findings(rows: list[tuple]) -> list[dict]:
    """rows: (hypertable_schema, hypertable_name) from
    timescaledb_information.hypertables where compression_enabled is false.
    An uncompressed hypertable pays full storage and I/O cost for chunks old
    enough that they're realistically only ever read, not written —
    TimescaleDB's whole compression pitch."""
    findings = []
    for schema, table in rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"timescaledb-uncompressed-{schema}-{table}",
                "category": CATEGORY,
                "severity": "unknown",
                "title": f"{full_name} is a hypertable with compression disabled",
                "summary": (
                    f"{full_name} has no compression policy — every chunk stays at full size "
                    "regardless of age, even chunks old enough that they're realistically read-only."
                ),
                "detail": f"schema={schema} hypertable={table}",
                "suggested_action": (
                    "If older chunks are rarely written to, enable compression and add a policy "
                    "(add_compression_policy) so they compress automatically past a given age."
                ),
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_pgvector_unindexed_column_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, column) for vector-typed columns with no index
    at all. Without an ivfflat/hnsw index, every similarity search does a
    full sequential scan computing the distance to every row — the vector
    equivalent of a missing index on a normal WHERE/ORDER BY column."""
    findings = []
    for schema, table, column in rows:
        full_name = f"{schema}.{table}.{column}"
        findings.append(
            {
                "id": f"pgvector-unindexed-{schema}-{table}-{column}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{full_name} has no vector index",
                "summary": (
                    f"{full_name} is a vector column with no index — every similarity search "
                    "(<->, <#>, <=>) computes the distance to every row in the table."
                ),
                "detail": f"schema={schema} table={table} column={column}",
                "suggested_action": (
                    f"Add an ivfflat or hnsw index on {full_name} matching the distance operator "
                    "queries actually use, once there's enough data for the index to be worth it."
                ),
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_postgis_unindexed_geometry_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, column) for geometry columns (per the
    geometry_columns view) with no GiST index. Spatial predicates
    (ST_Intersects, ST_DWithin, etc.) can't use a plain B-tree index — a
    missing GiST index means every spatial query is a full scan."""
    findings = []
    for schema, table, column in rows:
        full_name = f"{schema}.{table}.{column}"
        findings.append(
            {
                "id": f"postgis-unindexed-{schema}-{table}-{column}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{full_name} has no spatial index",
                "summary": (
                    f"{full_name} is a geometry column with no GiST index — spatial predicates "
                    "(ST_Intersects, ST_DWithin, ST_Contains, ...) can't use a plain B-tree index, "
                    "so every spatial query is a full scan."
                ),
                "detail": f"schema={schema} table={table} column={column}",
                "suggested_action": f"CREATE INDEX ... ON {schema}.{table} USING GIST ({column});",
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_installed_extensions_summary(installed: list[tuple[str, str]]) -> list[dict]:
    """installed: (extname, extversion) for every extension currently
    installed on the target, not just the three this module otherwise
    checks. Purely informational — one card giving a DBA visibility into
    what's already there without needing a separate \\dx in psql.

    The actual names go in the structured `extensions` list (rendered as
    pills by FindingCard.jsx, always visible) rather than only `detail` —
    `detail` is only ever shown in Advanced mode, which would make the one
    thing this finding exists to show invisible by default."""
    if not installed:
        return []
    names = sorted(installed, key=lambda row: row[0])
    listing = ", ".join(f"{name} {version}" for name, version in names)
    return [
        {
            "id": "installed-extensions-summary",
            "category": CATEGORY,
            "severity": "unknown",
            "title": f"{len(names)} extension{'s' if len(names) != 1 else ''} installed on this database",
            "summary": "Listed below, for reference.",
            "detail": listing,
            "suggested_action": "No action needed — this is informational.",
            "extensions": [{"name": name, "version": version} for name, version in names],
        }
    ]


def find_recommended_extension_findings(
    installed_names: set[str], available_names: set[str]
) -> list[dict]:
    """installed_names/available_names: extnames from pg_extension and
    pg_available_extensions respectively. One finding per RECOMMENDED_EXTENSIONS
    entry that isn't already installed — worded differently depending on
    whether the target's Postgres build can even install it, since those
    need completely different next steps (CREATE EXTENSION vs. installing
    the extension's OS package first)."""
    findings = []
    for extname, why, create_note in RECOMMENDED_EXTENSIONS:
        if extname in installed_names:
            continue
        available = extname in available_names
        findings.append(
            {
                "id": f"recommended-extension-{extname}",
                "category": CATEGORY,
                "severity": "unknown",
                "title": f"Consider installing {extname}",
                "summary": why,
                "detail": (
                    f"{extname} is available to install on this server."
                    if available
                    else f"{extname} isn't installed and isn't in pg_available_extensions on this server "
                    "— it would need its OS package (or equivalent) installed on the Postgres host first."
                ),
                "suggested_action": create_note if available else f"Install the {extname} package for this Postgres build, then: {create_note}",
            }
        )
    return findings
