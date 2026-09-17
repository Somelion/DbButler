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
            }
        )
    return findings
