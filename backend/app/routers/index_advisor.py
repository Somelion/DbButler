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
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["index-advisor"])

# Both patterns match only the DDL this app generates itself (index_analysis.py
# f-strings built from catalog-derived identifiers), never arbitrary user
# input, so a plain regex extraction is safe here — no injection surface.
CREATE_INDEX_DDL_PATTERN = re.compile(r"CREATE INDEX CONCURRENTLY (\S+) ON (\S+)", re.IGNORECASE)
DROP_INDEX_DDL_PATTERN = re.compile(r"DROP INDEX CONCURRENTLY (\S+)", re.IGNORECASE)

FK_COLUMNS_QUERY = """
    SELECT
        con.conrelid::regclass::text AS table_name,
        con.conname AS constraint_name,
        array_agg(att.attname ORDER BY u.ord) AS columns
    FROM pg_constraint con
    JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = u.attnum
    WHERE con.contype = 'f'
    GROUP BY con.oid, con.conrelid, con.conname
"""

# indkey holds 0 for an expression-index position, which the attnum join
# below silently drops — an index mixing plain and expression columns loses
# its expression part from `columns` here. Rare enough for FK-covering
# purposes not to be worth the extra complexity of handling it.
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
ALL_INDEX_COLUMNS_QUERY = """
    SELECT
        n.nspname AS schema_name,
        i.indrelid::regclass::text AS table_name,
        ic.relname AS index_name,
        am.amname AS index_method,
        i.indisunique AS is_unique,
        array_agg(att.attname ORDER BY u.ord) AS columns
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_am am ON am.oid = ic.relam
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS u(attnum, ord) ON true
    JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = u.attnum
    WHERE tc.relkind = 'r'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
    GROUP BY n.nspname, i.indrelid, ic.relname, am.amname, i.indisunique
"""

UNUSED_INDEXES_QUERY = """
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
      {schema_filter}
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

INVALID_INDEXES_QUERY = """
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
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
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
# ALL_INDEX_COLUMNS_QUERY above, so the three existing consumers of that
# query's 6-tuple row shape (find_missing_fk_indexes/find_duplicate_indexes/
# find_redundant_indexes) don't need to change.
INDEX_BYTES_QUERY = """
    SELECT
        n.nspname AS schema_name,
        ic.relname AS index_name,
        pg_relation_size(ic.oid) AS index_bytes
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = tc.relnamespace
    WHERE tc.relkind = 'r'
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

    cur.execute(FK_COLUMNS_QUERY)
    fk_rows = [(row[0], row[1], list(row[2])) for row in cur.fetchall()]

    cur.execute(ALL_INDEX_COLUMNS_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)), schema_params)
    index_rows = [(row[0], row[1], row[2], row[3], row[4], list(row[5])) for row in cur.fetchall()]

    cur.execute(UNUSED_INDEXES_QUERY.format(schema_filter=schema_filter_sql("s.schemaname", allowed_schemas)), schema_params)
    unused_rows = cur.fetchall()

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
        + find_unused_indexes(unused_rows)
        + find_duplicate_indexes(index_rows)
        + find_seq_scan_heavy_findings(seq_scan_rows)
        + find_invalid_indexes(invalid_index_rows)
        + find_redundant_indexes(index_rows)
        + find_seq_scan_no_index_findings(seq_scan_no_index_rows)
        + find_over_indexed_findings(over_indexed_rows, index_rows, index_bytes)
        + find_low_cardinality_index_findings(low_cardinality_rows)
        + find_covering_index_candidates(index_rows, index_bytes, recent_queries)
    )


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
    AI suggestion to apply. Each line is a standalone statement (a
    duplicate-index finding's DDL is several DROP INDEX lines, one per
    redundant index), and CREATE/DROP INDEX CONCURRENTLY both refuse to run
    inside a transaction block, so the connection runs autocommit."""
    ddl, source_finding_id = _resolve_index_apply_ddl(target_id, payload)
    statements = [line.strip() for line in ddl.splitlines() if line.strip()]

    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

                dropped = DROP_INDEX_DDL_PATTERN.search(statement)
                if dropped:
                    # DROP DDL is schema-qualified (schema.index_name); the
                    # tracked row stores the bare index name, matching what
                    # pg_stat_user_indexes.indexrelname returns.
                    bare_name = dropped.group(1).rsplit(".", 1)[-1]
                    _reconcile_dropped_index(target_id, bare_name)
                    continue

                created = CREATE_INDEX_DDL_PATTERN.search(statement)
                if created:
                    _track_created_index(target_id, cur, created.group(1), source_finding_id, ddl)
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
