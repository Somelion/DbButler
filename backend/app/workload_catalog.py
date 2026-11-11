"""Target-catalog reads for the Workload Map, shared by the capture job
(scheduler.py::_capture_query_plans) and the graph endpoint
(routers/workload_graph.py). Everything here is a plain catalog SELECT.

Callers pass an autocommit cursor: the TimescaleDB catalog query can fail on
an unexpected TimescaleDB version, and that failure must not abort the
queries that follow it."""

import logging

import psycopg

from app.timescaledb_health import fetch_chunk_map, is_timescaledb_installed
from app.workload_extract import Table

logger = logging.getLogger(__name__)

# Never drawn on the map: catalogs, TOAST and TimescaleDB's internals (its
# chunks fold onto their hypertable before this filter applies).
EXCLUDED_SCHEMA_PREFIXES = ("pg_", "_timescaledb", "timescaledb_")
EXCLUDED_SCHEMAS = frozenset({"information_schema"})


def is_user_schema(schema: str) -> bool:
    return schema not in EXCLUDED_SCHEMAS and not schema.startswith(EXCLUDED_SCHEMA_PREFIXES)


# Topmost ancestor of every inheritance child — declarative partitions
# (including sub-partitions) and classic inheritance alike.
PARENT_MAP_QUERY = """
    WITH RECURSIVE ancestry AS (
        SELECT inhrelid AS child, inhparent AS parent, 1 AS depth FROM pg_inherits
        UNION ALL
        SELECT a.child, i.inhparent, a.depth + 1
        FROM ancestry a
        JOIN pg_inherits i ON i.inhrelid = a.parent
    )
    SELECT DISTINCT ON (a.child) cn.nspname, c.relname, pn.nspname, p.relname
    FROM ancestry a
    JOIN pg_class c ON c.oid = a.child
    JOIN pg_namespace cn ON cn.oid = c.relnamespace
    JOIN pg_class p ON p.oid = a.parent
    JOIN pg_namespace pn ON pn.oid = p.relnamespace
    WHERE c.relkind IN ('r', 'p', 'f')
    ORDER BY a.child, a.depth DESC
"""

# A compressed TimescaleDB chunk lives in an internal "compressed
# hypertable", not under the user's hypertable — map it back through the
# catalog. Not part of timescaledb_information, so it is read defensively.
COMPRESSED_CHUNK_MAP_QUERY = """
    SELECT ch.schema_name, ch.table_name, main.schema_name, main.table_name
    FROM _timescaledb_catalog.chunk ch
    JOIN _timescaledb_catalog.hypertable h ON h.id = ch.hypertable_id
    JOIN _timescaledb_catalog.hypertable main ON main.compressed_hypertable_id = h.id
"""

# Foreign keys declared on a table itself — not the per-partition clones
# PostgreSQL creates (conparentid <> 0) or the copies TimescaleDB puts on
# every chunk. `indexed`: some index's leading *key* columns are exactly the
# FK's columns in any order — the standard "is this FK indexed" test. INCLUDE
# columns don't count (indnkeyatts), nor do partial indexes (the planner
# can't use them for every join) or invalid ones left by a failed CREATE
# INDEX CONCURRENTLY — matching Index Advisor's own missing-FK-index check.
FOREIGN_KEYS_QUERY = """
    SELECT
        con.conname,
        cn.nspname, c.relname,
        pn.nspname, p.relname,
        ARRAY(
            SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
            ORDER BY k.ord
        ),
        ARRAY(
            SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
            ORDER BY k.ord
        ),
        EXISTS (
            SELECT 1 FROM pg_index i
            WHERE i.indrelid = con.conrelid
              AND i.indisvalid
              AND i.indpred IS NULL
              AND i.indnkeyatts >= cardinality(con.conkey)
              AND (i.indkey::int2[])[0:cardinality(con.conkey) - 1] OPERATOR(pg_catalog.@>) con.conkey
              AND (i.indkey::int2[])[0:cardinality(con.conkey) - 1] OPERATOR(pg_catalog.<@) con.conkey
        )
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace cn ON cn.oid = c.relnamespace
    JOIN pg_class p ON p.oid = con.confrelid
    JOIN pg_namespace pn ON pn.oid = p.relnamespace
    WHERE con.contype = 'f'
      AND con.conparentid = 0
      AND NOT c.relispartition
"""

# First key column of every usable index, per table — "is this join column
# indexed" for a join that has no FK to ask about. Same validity rules as
# above (indkey[0] is always a key column, never an INCLUDE one).
INDEX_LEADING_COLUMNS_QUERY = """
    SELECT n.nspname, c.relname, a.attname
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
    WHERE i.indkey[0] <> 0
      AND i.indisvalid
      AND i.indpred IS NULL
      AND NOT c.relispartition
"""


def fetch_parent_map(cur) -> dict[Table, Table]:
    """Partition/inheritance child and TimescaleDB chunk -> logical parent."""
    cur.execute(PARENT_MAP_QUERY)
    parents = {Table(cs, ct): Table(ps, pt) for cs, ct, ps, pt in cur.fetchall()}
    try:
        if is_timescaledb_installed(cur):
            for chunk, hypertable in fetch_chunk_map(cur).items():
                parents[Table(*chunk)] = Table(*hypertable)
            cur.execute(COMPRESSED_CHUNK_MAP_QUERY)
            for cs, ct, hs, ht in cur.fetchall():
                parents[Table(cs, ct)] = Table(hs, ht)
    except psycopg.Error as exc:
        if cur.connection.broken:
            raise
        logger.info("TimescaleDB chunk mapping unavailable: %s", exc)
    return parents


def fetch_foreign_keys(cur) -> list[dict]:
    cur.execute(FOREIGN_KEYS_QUERY)
    return [
        {
            "name": name,
            "child": Table(cs, ct),
            "parent": Table(ps, pt),
            "child_columns": list(child_cols),
            "parent_columns": list(parent_cols),
            "indexed": bool(indexed),
        }
        for name, cs, ct, ps, pt, child_cols, parent_cols, indexed in cur.fetchall()
    ]


def fetch_index_leading_columns(cur) -> set[tuple[Table, str]]:
    cur.execute(INDEX_LEADING_COLUMNS_QUERY)
    return {(Table(schema, table), column) for schema, table, column in cur.fetchall()}
