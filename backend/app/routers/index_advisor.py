import re
import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.index_analysis import (
    find_covering_index_candidates,
    find_duplicate_indexes,
    find_invalid_indexes,
    find_low_cardinality_index_findings,
    find_missing_fk_indexes,
    find_over_indexed_findings,
    find_redundant_indexes,
    find_seq_scan_heavy_findings,
    find_seq_scan_no_index_findings,
    find_unused_indexes,
)
from app.pg_stat_statements import is_enabled as pg_stat_statements_enabled
from app.routers.advisor_archive import get_archived_finding_ids
from app.schema_filter import get_allowed_schemas, schema_filter_params, schema_filter_sql
from app.schemas import ApplyIndexFindingRequest, IndexAdvisorResponse, IndexFinding, MaintenanceResult
from app.sql_ident import (
    ALLOWED_APPLY_STATEMENT,
    QUALIFIED_NAME_PATTERN,
    parse_qualified_name,
    split_sql_statements,
)
from app.target_conn import MAINTENANCE_STATEMENT_TIMEOUT_MS, connect_to_target

router = APIRouter(prefix="/api/targets", tags=["index-advisor"])

# Both patterns match only the DDL this app generates itself (index_analysis.py,
# identifiers quoted via app/sql_ident.py), and only ever run against single
# statements already split and validated by apply_index_finding below.
CREATE_INDEX_DDL_PATTERN = re.compile(
    rf"CREATE INDEX CONCURRENTLY ({QUALIFIED_NAME_PATTERN}) ON ({QUALIFIED_NAME_PATTERN})", re.IGNORECASE
)
DROP_INDEX_DDL_PATTERN = re.compile(rf"DROP INDEX CONCURRENTLY ({QUALIFIED_NAME_PATTERN})", re.IGNORECASE)

# Schemas whose objects are an extension's internals rather than the user's
# own design — TimescaleDB keeps every chunk (and each chunk's own copy of
# every hypertable index) in _timescaledb_internal. Flagging those as
# unused/duplicate/missing would suggest dropping or creating indexes the
# extension itself manages.
INTERNAL_SCHEMA_PATTERN = "^(pg_toast|_timescaledb|timescaledb_)"

# conparentid <> 0 marks the per-partition clone of a foreign key declared on
# a partitioned parent — reporting the parent's FK once is enough, and the
# clones' coverage follows from the parent's (a partitioned index cascades
# to every partition). relkind is returned so a missing index on a
# partitioned parent can be reported without a CONCURRENTLY suggestion,
# which PostgreSQL doesn't support on a partitioned table.
FK_COLUMNS_QUERY = f"""
    SELECT
        con.conrelid::regclass::text AS table_name,
        con.conname AS constraint_name,
        array_agg(att.attname::text ORDER BY u.ord) AS columns,
        n.nspname AS schema_name,
        tc.relname AS table_relname,
        tc.relkind AS table_relkind
    FROM pg_constraint con
    JOIN pg_class tc ON tc.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
    WHERE con.contype = 'f'
      AND con.conparentid = 0
      AND n.nspname !~ '{INTERNAL_SCHEMA_PATTERN}'
      {{schema_filter}}
    GROUP BY con.oid, con.conrelid, con.conname, n.nspname, tc.relname, tc.relkind
"""

# One row per index on a plain or partitioned table. key_columns holds only
# the index's key part (INCLUDE columns are separate — they can't serve a
# lookup), with an expression position rendered as its expression text via
# pg_get_indexdef(index, column_no) rather than silently dropped.
# key_signatures carries each key position's operator class/sort option/
# collation, and definition_signature the whole definition (incl. partial
# predicate and expressions), so two indexes only ever count as duplicates
# or a usable prefix of each other when they'd genuinely serve the same
# queries — `(lower(email))` vs `(email)`, a partial vs a full index, or
# `text_pattern_ops` vs the default opclass never do.
#
# is_protected marks an index that can't (or mustn't) be dropped on its
# own: a primary key, exclusion or replica-identity index, or one backing a
# unique/PK/exclusion constraint. is_partitioned_index / is_partition_child
# mark indexes DROP INDEX CONCURRENTLY can't touch (a partitioned parent
# index, or a partition's copy that's attached to one).
#
# schema_name is included (not just for display) because find_unused_indexes
# / find_duplicate_indexes / find_redundant_indexes / find_invalid_indexes
# all schema-qualify their DROP INDEX suggestions with it — a bare index name
# is ambiguous the moment two schemas ever have a same-named index, and these
# suggestions are now actually executed via Apply, not just copy-pasted.
# {schema_filter} is filled in at call time by schema_filter_sql() — either
# an `AND n.nspname = ANY(%s)`/`AND s.schemaname = ANY(%s)` clause (when the
# target has a schema allowlist configured, app/schema_filter.py) or an
# empty string (no filter, the query runs exactly as before). Always placed
# right after the query's own WHERE conditions and before any trailing
# GROUP BY, never after one.
ALL_INDEX_COLUMNS_QUERY = f"""
    SELECT
        n.nspname AS schema_name,
        i.indrelid::regclass::text AS table_name,
        ic.relname AS index_name,
        am.amname AS index_method,
        i.indisunique AS is_unique,
        cols.key_columns,
        cols.include_columns,
        cols.key_signatures,
        tc.relname AS table_relname,
        i.indpred IS NOT NULL AS is_partial,
        i.indexprs IS NOT NULL AS has_expressions,
        (
            i.indisprimary OR i.indisexclusion OR i.indisreplident
            OR EXISTS (
                SELECT 1 FROM pg_constraint con
                WHERE con.conindid = i.indexrelid AND con.contype IN ('p', 'u', 'x')
            )
        ) AS is_protected,
        ic.relkind = 'I' AS is_partitioned_index,
        EXISTS (SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid = i.indexrelid) AS is_partition_child,
        concat_ws(
            '|', i.indkey::text, i.indnkeyatts, i.indclass::text, i.indoption::text, i.indcollation::text,
            coalesce(pg_get_expr(i.indexprs, i.indrelid), '-'), coalesce(pg_get_expr(i.indpred, i.indrelid), '-')
        ) AS definition_signature
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_am am ON am.oid = ic.relam
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    CROSS JOIN LATERAL (
        SELECT
            array_agg(
                CASE WHEN u.attnum = 0 THEN pg_get_indexdef(i.indexrelid, u.ord::int, true) ELSE att.attname::text END
                ORDER BY u.ord
            ) FILTER (WHERE u.ord <= i.indnkeyatts) AS key_columns,
            array_agg(att.attname::text ORDER BY u.ord) FILTER (WHERE u.ord > i.indnkeyatts) AS include_columns,
            array_agg(
                concat_ws(':', i.indclass[(u.ord - 1)::int], i.indoption[(u.ord - 1)::int], i.indcollation[(u.ord - 1)::int])
                ORDER BY u.ord
            ) FILTER (WHERE u.ord <= i.indnkeyatts) AS key_signatures
        FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS u(attnum, ord)
        LEFT JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = u.attnum AND u.attnum > 0
    ) cols
    WHERE tc.relkind IN ('r', 'p')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname !~ '{INTERNAL_SCHEMA_PATTERN}'
      {{schema_filter}}
"""

# Excludes every index that can't be dropped on its own (see is_protected
# above), plus a partition's attached copy of a partitioned index, which
# PostgreSQL refuses to drop separately from its parent. Whether a remaining
# candidate backs a foreign key is decided in Python
# (compute_index_advisor_findings), from the same index/FK rows the
# missing-FK-index check already uses.
UNUSED_INDEXES_QUERY = f"""
    SELECT
        s.schemaname,
        s.relname AS table_name,
        s.indexrelname AS index_name,
        s.idx_scan,
        pg_relation_size(s.indexrelid) AS index_bytes
    FROM pg_stat_user_indexes s
    JOIN pg_index i ON i.indexrelid = s.indexrelid
    WHERE s.idx_scan = 0
      AND NOT i.indisprimary
      AND NOT i.indisunique
      AND NOT i.indisexclusion
      AND NOT i.indisreplident
      AND NOT EXISTS (SELECT 1 FROM pg_constraint con WHERE con.conindid = i.indexrelid AND con.contype IN ('p', 'u', 'x'))
      AND NOT EXISTS (SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid = i.indexrelid)
      AND s.schemaname !~ '{INTERNAL_SCHEMA_PATTERN}'
      {{schema_filter}}
"""

# How long this database's index usage counters have been accumulating —
# idx_scan = 0 means "unused since the last stats reset," not "never used,"
# and an hour-old reset says almost nothing. NULL stats_reset means the
# counters were never explicitly reset, so the server's own start time
# stands in as a conservative (never overstated) lower bound.
STATS_AGE_DAYS_QUERY = """
    SELECT extract(epoch FROM now() - coalesce(stats_reset, pg_postmaster_start_time())) / 86400
    FROM pg_stat_database
    WHERE datname = current_database()
"""

# Only tables with at least one non-PK index — a table with no such index
# scanning sequentially isn't an index-health problem, it's just missing an
# index entirely (find_missing_fk_indexes / manual review, not this check).
SEQ_SCAN_HEAVY_QUERY = """
    SELECT
        s.schemaname,
        s.relname AS table_name,
        s.seq_scan,
        s.idx_scan,
        s.n_live_tup,
        pg_total_relation_size(s.relid) AS total_bytes
    FROM pg_stat_user_tables s
    WHERE EXISTS (
        SELECT 1 FROM pg_index i WHERE i.indrelid = s.relid AND NOT i.indisprimary
    )
    {schema_filter}
"""

INVALID_INDEXES_QUERY = f"""
    SELECT
        n.nspname AS schema_name,
        tc.relname AS table_name,
        ic.relname AS index_name,
        pg_relation_size(ic.oid) AS index_bytes
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE NOT i.indisvalid
      AND ic.relkind = 'i'
      AND NOT EXISTS (SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid = i.indexrelid)
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname !~ '{INTERNAL_SCHEMA_PATTERN}'
      {{schema_filter}}
"""

# The mirror image of SEQ_SCAN_HEAVY_QUERY above: tables with NO non-PK
# index at all, rather than one that exists but goes unused.
SEQ_SCAN_NO_INDEX_QUERY = """
    SELECT
        s.schemaname,
        s.relname AS table_name,
        s.seq_scan,
        s.n_live_tup,
        pg_total_relation_size(s.relid) AS total_bytes
    FROM pg_stat_user_tables s
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_index i WHERE i.indrelid = s.relid AND NOT i.indisprimary
    )
    {schema_filter}
"""

OVER_INDEXED_QUERY = """
    SELECT
        s.schemaname,
        s.relname AS table_name,
        count(i.indexrelid) AS index_count,
        s.n_tup_ins + s.n_tup_upd + s.n_tup_del AS write_ops
    FROM pg_stat_user_tables s
    JOIN pg_index i ON i.indrelid = s.relid
    WHERE 1=1 {schema_filter}
    GROUP BY s.schemaname, s.relname, s.n_tup_ins, s.n_tup_upd, s.n_tup_del
"""

# Single-column, non-unique, non-partial indexes only — a partial index
# (indpred IS NOT NULL) already targets a specific value subset on purpose,
# and a unique/PK index needs to exist regardless of cardinality. Uses the
# same LATERAL unnest ... WITH ORDINALITY approach as ALL_INDEX_COLUMNS_QUERY
# above (indkey is an int2vector, not a plain array) rather than subscripting
# indkey directly.
LOW_CARDINALITY_INDEX_QUERY = """
    SELECT
        n.nspname AS schema_name,
        tc.relname AS table_name,
        ic.relname AS index_name,
        att.attname AS column_name,
        st.n_distinct,
        tc.reltuples
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS u(attnum, ord) ON u.ord = 1
    JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = u.attnum
    LEFT JOIN pg_stats st
        ON st.schemaname = n.nspname AND st.tablename = tc.relname AND st.attname = att.attname
    WHERE i.indpred IS NULL
      AND NOT i.indisprimary
      AND NOT i.indisunique
      AND array_length(i.indkey, 1) = 1
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
"""

# Sizes for every index on a plain table, keyed by (schema, index_name) in
# Python — kept as its own query rather than added as a column to
# ALL_INDEX_COLUMNS_QUERY above, so the index-row shape fetch_index_rows
# returns stays about definitions only.
INDEX_BYTES_QUERY = """
    SELECT
        n.nspname AS schema_name,
        ic.relname AS index_name,
        pg_relation_size(ic.oid) AS index_bytes
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE tc.relkind IN ('r', 'p')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
"""

# Same shape/intent as schema_lint.py's RECENT_QUERIES_QUERY (top 200 by
# call count, not duration — this is about which columns a query's WHERE/
# ORDER BY touches, not which query is slowest) — duplicated rather than
# imported across routers, matching this codebase's per-router-owns-its-SQL
# convention. Feeds find_covering_index_candidates.
RECENT_QUERIES_QUERY = """
    SELECT pss.query, pss.calls
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.calls DESC
    LIMIT 200
"""


@router.get("/{target_id}/index-advisor", response_model=IndexAdvisorResponse)
def get_index_advisor(target_id: uuid.UUID):
    allowed_schemas = get_allowed_schemas(target_id)
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_index_advisor_findings(cur, allowed_schemas)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return IndexAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_index_advisor_findings(cur, allowed_schemas: list[str] | None = None) -> list[dict]:
    """Runs all ten Index Advisor checks against an already-open target
    cursor and returns raw finding dicts, unfiltered by archive state. Shared
    by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart.

    allowed_schemas narrows every catalog query to a target's configured
    schema allowlist (app/schema_filter.py) when the caller passes one — the
    on-demand endpoint above does; the nightly deep scan and the Apply path's
    own re-fetch (_resolve_index_apply_ddl) don't thread target_id through
    here today, so they still see every schema, a known, narrower gap than
    the live screen."""
    schema_params = schema_filter_params(allowed_schemas)

    fk_rows = fetch_fk_rows(cur, allowed_schemas)
    index_rows = fetch_index_rows(cur, allowed_schemas)

    cur.execute(UNUSED_INDEXES_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)), schema_params)
    unused_rows = cur.fetchall()

    cur.execute(STATS_AGE_DAYS_QUERY)
    stats_age_row = cur.fetchone()
    stats_age_days = float(stats_age_row[0]) if stats_age_row and stats_age_row[0] is not None else None

    cur.execute(SEQ_SCAN_HEAVY_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)), schema_params)
    seq_scan_rows = cur.fetchall()

    cur.execute(INVALID_INDEXES_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params)
    invalid_index_rows = cur.fetchall()

    cur.execute(
        SEQ_SCAN_NO_INDEX_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)), schema_params
    )
    seq_scan_no_index_rows = cur.fetchall()

    cur.execute(OVER_INDEXED_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)), schema_params)
    over_indexed_rows = cur.fetchall()

    cur.execute(
        LOW_CARDINALITY_INDEX_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params
    )
    low_cardinality_rows = cur.fetchall()

    cur.execute(INDEX_BYTES_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params)
    index_bytes = {(row[0], row[1]): row[2] for row in cur.fetchall()}

    recent_queries = []
    if pg_stat_statements_enabled(cur):
        cur.execute(RECENT_QUERIES_QUERY)
        recent_queries = cur.fetchall()

    return (
        find_missing_fk_indexes(fk_rows, index_rows)
        + find_unused_indexes(unused_rows, stats_age_days, fk_covering_indexes(fk_rows, index_rows))
        + find_duplicate_indexes(index_rows)
        + find_seq_scan_heavy_findings(seq_scan_rows)
        + find_invalid_indexes(invalid_index_rows)
        + find_redundant_indexes(index_rows)
        + find_seq_scan_no_index_findings(seq_scan_no_index_rows)
        + find_over_indexed_findings(over_indexed_rows, index_rows, index_bytes)
        + find_low_cardinality_index_findings(low_cardinality_rows)
        + find_covering_index_candidates(index_rows, index_bytes, recent_queries)
    )


def fetch_index_rows(cur, allowed_schemas: list[str] | None = None) -> list[tuple]:
    """Index definitions as (schema, table_name, index_name, method,
    is_unique, key_columns, info) — the row shape every index_analysis.py
    check takes. info carries the extra definition detail (INCLUDE columns,
    partial/expression flags, per-key-position opclass signatures, whether
    the index is protected or partition-managed) that decides whether two
    indexes are really interchangeable and whether one may be dropped."""
    cur.execute(
        ALL_INDEX_COLUMNS_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)),
        schema_filter_params(allowed_schemas),
    )
    rows = []
    for row in cur.fetchall():
        info = {
            "include_columns": list(row[6] or []),
            "key_signatures": list(row[7] or []),
            "table_relname": row[8],
            "is_partial": row[9],
            "has_expressions": row[10],
            "is_protected": row[11],
            "is_partitioned_index": row[12],
            "is_partition_child": row[13],
            "definition_signature": row[14],
        }
        rows.append((row[0], row[1], row[2], row[3], row[4], list(row[5] or []), info))
    return rows


def fetch_fk_rows(cur, allowed_schemas: list[str] | None = None) -> list[tuple]:
    """Foreign keys as (table_name, constraint_name, columns, info), info
    holding the table's schema, bare relname and relkind."""
    cur.execute(
        FK_COLUMNS_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)),
        schema_filter_params(allowed_schemas),
    )
    return [
        (row[0], row[1], list(row[2]), {"schema": row[3], "table_relname": row[4], "relkind": row[5]})
        for row in cur.fetchall()
    ]


def fk_covering_indexes(fk_rows: list[tuple], index_rows: list[tuple]) -> dict[tuple[str, str], str]:
    """(schema, index_name) -> constraint name, for every non-partial index
    whose leading key columns serve some foreign key's lookups — the same
    leftmost-prefix rule find_missing_fk_indexes uses. An unused-index
    finding for one of these must not suggest dropping it: idx_scan stays 0
    until a row in the referenced table is deleted or its key updated, and
    then that index is what keeps the cascade check from scanning the whole
    table."""
    covering: dict[tuple[str, str], str] = {}
    for table_name, constraint_name, fk_columns, _fk_info in fk_rows:
        for schema, index_table, index_name, _method, _is_unique, columns, info in index_rows:
            if index_table == table_name and not info["is_partial"] and columns[: len(fk_columns)] == fk_columns:
                covering.setdefault((schema, index_name), constraint_name)
    return covering


def _resolve_index_apply_ddl(target_id: uuid.UUID, payload: ApplyIndexFindingRequest) -> tuple[str, str]:
    """Never trusts DDL text from the client — only ever an id referencing
    something the server itself already computed (a live finding,
    re-derived fresh from the target's own catalogs) or already generated
    and persisted (an AI covering-index analysis' recommended_ddl, written
    by index_coverage_analysis.py, never by the client). Returns
    (ddl, source_finding_id)."""
    if payload.analysis_id is not None:
        with store_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT finding_id, recommended_ddl FROM index_coverage_analyses "
                "WHERE id = %s AND target_id = %s AND status = 'done'",
                (payload.analysis_id, target_id),
            )
            row = cur.fetchone()
        if row is None or not row[1]:
            raise HTTPException(status_code=404, detail="No applicable AI suggestion found for this analysis.")
        return row[1], row[0]

    if not payload.finding_id:
        raise HTTPException(status_code=400, detail="finding_id or analysis_id is required.")

    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_index_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    finding = next((f for f in findings if f["id"] == payload.finding_id), None)
    if finding is None:
        raise HTTPException(
            status_code=404, detail="This finding no longer applies — it may have been resolved already."
        )
    if not finding.get("recommended_ddl"):
        raise HTTPException(status_code=400, detail="This finding has no DDL to apply.")
    return finding["recommended_ddl"], finding["id"]


@router.post("/{target_id}/index-advisor/apply", response_model=MaintenanceResult)
def apply_index_finding(target_id: uuid.UUID, payload: ApplyIndexFindingRequest):
    """Runs a finding's DDL against the target — the one place Index Advisor
    writes to (rather than just suggests changes to) a monitored database.
    The DDL itself is always resolved server-side (see
    _resolve_index_apply_ddl) — the client only ever names which finding or
    AI suggestion to apply. The DDL may hold several statements (a
    duplicate-index finding drops one index per redundant copy), split on
    semicolons outside quotes rather than by line, since a quoted identifier
    may itself contain a newline. Every statement must be a CREATE/DROP
    INDEX CONCURRENTLY — anything else is refused before any of them runs.
    Both refuse to run inside a transaction block, so the connection runs
    autocommit, with the longer maintenance statement timeout (a CONCURRENTLY
    build on a big table legitimately takes minutes) and no lock timeout
    (CONCURRENTLY waits out older transactions by design; aborting that wait
    would leave an INVALID index behind)."""
    ddl, source_finding_id = _resolve_index_apply_ddl(target_id, payload)
    statements = split_sql_statements(ddl)
    if not statements or not all(ALLOWED_APPLY_STATEMENT.match(statement) for statement in statements):
        raise HTTPException(
            status_code=400,
            detail="This suggestion isn't a plain CREATE/DROP INDEX CONCURRENTLY change, so it can't be "
            "applied from here. Review it and run it manually if it's what you want.",
        )

    try:
        with connect_to_target(
            target_id, autocommit=True, statement_timeout_ms=MAINTENANCE_STATEMENT_TIMEOUT_MS, lock_timeout_ms=0
        ) as conn, conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

                dropped = DROP_INDEX_DDL_PATTERN.match(statement)
                if dropped:
                    # DROP DDL is schema-qualified (schema.index_name); the
                    # tracked row stores the bare index name, matching what
                    # pg_stat_user_indexes.indexrelname returns.
                    bare_name = parse_qualified_name(dropped.group(1))[-1]
                    _reconcile_dropped_index(target_id, bare_name)
                    continue

                created = CREATE_INDEX_DDL_PATTERN.match(statement)
                if created:
                    index_name = parse_qualified_name(created.group(1))[-1]
                    _track_created_index(target_id, cur, index_name, source_finding_id, ddl)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not apply this change: {exc}") from exc

    return MaintenanceResult(ok=True, message="Applied.")


def _track_created_index(
    target_id: uuid.UUID, target_cur, index_name: str, source_finding_id: str, ddl: str
) -> None:
    """Looks up the newly created index's real schema/table/baseline scan
    count straight from the target's own catalogs — more reliable than
    trusting the table name as it appears in the DDL text, which may or may
    not be schema-qualified."""
    target_cur.execute(
        """
        SELECT idx.schemaname, idx.relname, s.seq_scan
        FROM pg_stat_user_indexes idx
        JOIN pg_stat_user_tables s ON s.relid = idx.relid
        WHERE idx.indexrelname = %s
        """,
        (index_name,),
    )
    row = target_cur.fetchone()
    if row is None:
        return
    schema_name, table_name, baseline_seq_scan = row

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tracked_indexes
                (target_id, schema_name, table_name, index_name, ddl, source_finding_id, baseline_seq_scan)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (target_id, schema_name, index_name) DO UPDATE
                SET dropped_at = NULL, baseline_seq_scan = EXCLUDED.baseline_seq_scan
            """,
            (target_id, schema_name, table_name, index_name, ddl, source_finding_id, baseline_seq_scan),
        )
        conn.commit()


def _reconcile_dropped_index(target_id: uuid.UUID, index_name: str) -> None:
    """If the index this app just dropped happens to be one it was tracking
    (e.g. a tool-created index that later showed up as unused), mark it
    dropped there too rather than leaving Index Testing showing a stale row."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tracked_indexes SET dropped_at = now() "
            "WHERE target_id = %s AND index_name = %s AND dropped_at IS NULL",
            (target_id, index_name),
        )
        conn.commit()
