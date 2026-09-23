import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.routers.advisor_archive import get_archived_finding_ids
from app.schema_filter import get_allowed_schemas, schema_filter_params, schema_filter_sql
from app.schema_lint import (
    find_bad_data_type_findings,
    find_boolean_as_int_findings,
    find_disk_spill_findings,
    find_fk_type_mismatch_findings,
    find_jsonb_overuse_findings,
    find_missing_primary_key_findings,
    find_n_plus_one_signature_findings,
    find_naive_timestamp_findings,
    find_partitioning_findings,
    find_query_pattern_findings,
    find_sequence_exhaustion_findings,
    find_unlogged_table_findings,
    find_unvalidated_constraint_findings,
    find_uuid_fragmentation_findings,
)
from app.schemas import IndexFinding, SchemaLintResponse
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["schema-lint"])

# {schema_filter} is filled in at call time by schema_filter_sql() — either
# an AND ... = ANY(%s) clause (target has a schema allowlist configured,
# app/schema_filter.py) or an empty string (no filter). Always placed right
# after the query's own WHERE conditions, before any trailing GROUP BY.
COLUMN_TYPES_QUERY = """
    SELECT table_schema, table_name, column_name, data_type, character_maximum_length,
           column_default, is_identity
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
"""

# Excludes partitioned parent tables (pg_partitioned_table) and their
# partition children (relispartition) — both are already "partitioned" in
# the sense this check cares about, so only plain tables reach Python.
PARTITION_CANDIDATES_QUERY = """
    SELECT
        s.schemaname,
        s.relname,
        s.n_live_tup,
        pg_total_relation_size(c.oid) AS total_bytes
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    WHERE NOT EXISTS (SELECT 1 FROM pg_partitioned_table pt WHERE pt.partrelid = c.oid)
      AND NOT c.relispartition
      {schema_filter}
"""

# Single-column uuid primary keys only (array_length = 1) — composite
# surrogate keys are rare enough here not to be worth the extra complexity.
# No pg_namespace join (table_name comes from ::regclass::text, not a
# schema-qualified pair) — not schema-filterable without one, so this check
# doesn't respect a target's schema allowlist yet, a known, narrower gap.
UUID_PK_QUERY = """
    SELECT
        con.conrelid::regclass::text AS table_name,
        att.attname AS column_name,
        pg_get_expr(def.adbin, def.adrelid) AS column_default,
        s.n_live_tup,
        pg_total_relation_size(con.conrelid) AS total_bytes
    FROM pg_constraint con
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
    JOIN pg_type ty ON ty.oid = att.atttypid
    LEFT JOIN pg_attrdef def ON def.adrelid = att.attrelid AND def.adnum = att.attnum
    JOIN pg_stat_user_tables s ON s.relid = con.conrelid
    WHERE con.contype = 'p'
      AND ty.typname = 'uuid'
      AND array_length(con.conkey, 1) = 1
"""

# Ranked by calls, not total time — this check is about a pattern's presence,
# not which query is slowest, so the busiest queries are the most useful sample.
# calls is carried through to find_query_pattern_findings so each per-query
# finding can report how often the offending query actually runs.
RECENT_QUERIES_QUERY = """
    SELECT pss.query, pss.calls
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.calls DESC
    LIMIT 200
"""

# temp_blks_written has been a stable pg_stat_statements column name since
# PG9.2 — unlike total_time/mean_time, it was never renamed, so no
# version-branch is needed for this app's PG13+ minimum.
DISK_SPILL_QUERY = """
    SELECT pss.query, pss.calls, pss.temp_blks_written
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.temp_blks_written DESC
    LIMIT 50
"""

# rows is pg_stat_statements' own cumulative row count across all calls of
# this query — dividing by calls gives the average rows returned per call,
# which is what tells a single-row point lookup apart from a filter that
# just happens to use "=" but usually returns many rows.
N_PLUS_ONE_QUERY = """
    SELECT pss.query, pss.calls, pss.rows
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.calls DESC
    LIMIT 200
"""

UNLOGGED_TABLES_QUERY = """
    SELECT n.nspname, c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND c.relpersistence = 'u'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
"""

# contype 'c' (CHECK) and 'f' (FOREIGN KEY) are the two constraint kinds
# that support ADD CONSTRAINT ... NOT VALID; convalidated=false means the
# follow-up VALIDATE CONSTRAINT step never ran. No pg_namespace join (same
# reason as UUID_PK_QUERY above) — not schema-filterable without one.
UNVALIDATED_CONSTRAINTS_QUERY = """
    SELECT con.conrelid::regclass::text, con.conname, con.contype
    FROM pg_constraint con
    WHERE NOT con.convalidated AND con.contype IN ('c', 'f')
"""

MISSING_PRIMARY_KEY_QUERY = """
    SELECT
        n.nspname AS schema_name,
        c.relname AS table_name,
        s.n_live_tup,
        pg_total_relation_size(c.oid) AS total_bytes
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_stat_user_tables s ON s.relid = c.oid
    WHERE c.relkind = 'r'
      AND NOT EXISTS (
          SELECT 1 FROM pg_constraint con
          WHERE con.conrelid = c.oid AND con.contype IN ('p', 'u')
      )
      {schema_filter}
"""

# pg_depend links a sequence to the column it backs: deptype 'a' for a plain
# serial/nextval() default (ALTER SEQUENCE ... OWNED BY), 'i' for a
# GENERATED ... AS IDENTITY column. Scoped to int4/int2-backed sequences —
# bigint sequences aren't realistically exhaustible.
SEQUENCE_EXHAUSTION_QUERY = """
    SELECT
        n.nspname AS schema_name,
        tc.relname AS table_name,
        att.attname AS column_name,
        seq_ns.nspname || '.' || seq_class.relname AS seq_name,
        ps.data_type,
        ps.last_value,
        ps.max_value
    FROM pg_depend dep
    JOIN pg_class seq_class ON seq_class.oid = dep.objid AND seq_class.relkind = 'S'
    JOIN pg_namespace seq_ns ON seq_ns.oid = seq_class.relnamespace
    JOIN pg_attribute att ON att.attrelid = dep.refobjid AND att.attnum = dep.refobjsubid
    JOIN pg_class tc ON tc.oid = dep.refobjid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    JOIN pg_sequences ps ON ps.schemaname = seq_ns.nspname AND ps.sequencename = seq_class.relname
    WHERE dep.deptype IN ('a', 'i')
      AND ps.data_type IN ('integer', 'smallint')
      {schema_filter}
"""

JSONB_OVERUSE_QUERY = """
    SELECT
        table_schema,
        table_name,
        count(*) FILTER (WHERE data_type = 'jsonb') AS jsonb_column_count,
        count(*) AS total_column_count
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
    GROUP BY table_schema, table_name
"""

# Pairs each FK column with the column it references by ordinal position
# (conkey[i] <-> confkey[i]) via two LATERAL unnests joined on matching
# ordinality — needed so a composite FK's columns line up correctly, not
# just its first column. No pg_namespace join (same reason as UUID_PK_QUERY
# above) — not schema-filterable without one.
FK_TYPE_MISMATCH_QUERY = """
    SELECT
        con.conname AS constraint_name,
        con.conrelid::regclass::text AS table_name,
        att.attname AS column_name,
        col_ty.typname AS column_type,
        con.confrelid::regclass::text AS ref_table,
        ref_att.attname AS ref_column,
        ref_ty.typname AS ref_type
    FROM pg_constraint con
    JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
    JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS ru(attnum, ord) ON ru.ord = u.ord
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
    JOIN pg_attribute ref_att ON ref_att.attrelid = con.confrelid AND ref_att.attnum = ru.attnum
    JOIN pg_type col_ty ON col_ty.oid = att.atttypid
    JOIN pg_type ref_ty ON ref_ty.oid = ref_att.atttypid
    WHERE con.contype = 'f'
"""


@router.get("/{target_id}/schema-lint", response_model=SchemaLintResponse)
def get_schema_lint(target_id: uuid.UUID):
    allowed_schemas = get_allowed_schemas(target_id)
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_schema_lint_findings(cur, allowed_schemas)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return SchemaLintResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_schema_lint_findings(cur, allowed_schemas: list[str] | None = None) -> list[dict]:
    """Runs all Schema Lint checks against an already-open target cursor and
    returns raw finding dicts, unfiltered by archive state. Shared by the
    on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart.

    allowed_schemas narrows the checks that have a pg_namespace join
    (app/schema_filter.py) when the caller passes one — the on-demand
    endpoint above does; the nightly deep scan doesn't thread target_id
    through here today, so it still sees every schema, a known, narrower
    gap. UUID_PK_QUERY/FK_TYPE_MISMATCH_QUERY/UNVALIDATED_CONSTRAINTS_QUERY
    have no schema join at all and aren't filtered regardless."""
    schema_params = schema_filter_params(allowed_schemas)

    cur.execute(COLUMN_TYPES_QUERY.format(schema_filter=schema_filter_sql("table_schema", allowed_schemas)), schema_params)
    column_rows = cur.fetchall()

    cur.execute(
        PARTITION_CANDIDATES_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)),
        schema_params,
    )
    partition_rows = cur.fetchall()

    cur.execute(UUID_PK_QUERY)
    uuid_pk_rows = cur.fetchall()

    cur.execute(
        MISSING_PRIMARY_KEY_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params
    )
    missing_pk_rows = cur.fetchall()

    cur.execute(
        SEQUENCE_EXHAUSTION_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params
    )
    sequence_rows = cur.fetchall()

    cur.execute(JSONB_OVERUSE_QUERY.format(schema_filter=schema_filter_sql("table_schema", allowed_schemas)), schema_params)
    jsonb_rows = cur.fetchall()

    cur.execute(FK_TYPE_MISMATCH_QUERY)
    fk_type_rows = cur.fetchall()

    cur.execute(UNLOGGED_TABLES_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params)
    unlogged_table_rows = cur.fetchall()

    cur.execute(UNVALIDATED_CONSTRAINTS_QUERY)
    unvalidated_constraint_rows = cur.fetchall()

    recent_query_rows = []
    disk_spill_rows = []
    n_plus_one_rows = []
    if pg_stat_statements_enabled(cur):
        cur.execute(RECENT_QUERIES_QUERY)
        recent_query_rows = cur.fetchall()

        cur.execute(DISK_SPILL_QUERY)
        disk_spill_rows = cur.fetchall()

        cur.execute(N_PLUS_ONE_QUERY)
        n_plus_one_rows = cur.fetchall()

    return (
        find_bad_data_type_findings(column_rows)
        + find_partitioning_findings(partition_rows)
        + find_uuid_fragmentation_findings(uuid_pk_rows)
        + find_query_pattern_findings(recent_query_rows)
        + find_n_plus_one_signature_findings(n_plus_one_rows)
        + find_missing_primary_key_findings(missing_pk_rows)
        + find_sequence_exhaustion_findings(sequence_rows)
        + find_naive_timestamp_findings(column_rows)
        + find_jsonb_overuse_findings(jsonb_rows)
        + find_boolean_as_int_findings(column_rows)
        + find_fk_type_mismatch_findings(fk_type_rows)
        + find_disk_spill_findings(disk_spill_rows)
        + find_unlogged_table_findings(unlogged_table_rows)
        + find_unvalidated_constraint_findings(unvalidated_constraint_rows)
    )
