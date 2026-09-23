import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import (
    ApplyTableHealthFindingRequest,
    IndexFinding,
    MaintenanceResult,
    TableAutovacuumDetail,
    TableColumnDetail,
    TableHealthDeepScanStatus,
    TableHealthDetailResponse,
    TableHealthResponse,
    TableHealthRow,
    TableStorageDetail,
)
from app.severity import cache_hit_severity, dead_pct_severity, wraparound_severity
from app.table_health_advisor import (
    ColumnDetail,
    find_autovacuum_scale_factor_candidate,
    find_dead_tuple_finding,
    find_dead_tuple_growth_forecast,
    find_low_hot_update_ratio_candidate,
    find_low_statistics_target_candidates,
    find_never_analyzed,
    find_never_vacuumed,
    find_toast_heavy,
    find_wide_columns,
)
from app.schema_filter import get_allowed_schemas
from app.target_conn import connect_to_target
from app.timescaledb_health import aggregate_hypertable_rows, is_timescaledb_installed

router = APIRouter(prefix="/api/targets", tags=["table-health"])

# pg_statio_user_tables is 1:1 with pg_stat_user_tables (same relid) — a
# LEFT JOIN just in case a table has stats but somehow no I/O row yet, so a
# table never silently drops out of the health list. table_oid/reltoastrelid/
# n_tup_upd/n_tup_hot_upd are appended at the end (not exposed in
# TableHealthRow) purely so compute_table_findings below can run its extra
# checks without extra lookups — dashboard.py::compute_health, which also
# runs this exact query, only reads indices 0-8 and is unaffected.
_TABLE_HEALTH_COLUMNS = """
        s.schemaname,
        s.relname,
        s.n_live_tup,
        s.n_dead_tup,
        s.last_vacuum,
        s.last_autovacuum,
        s.last_analyze,
        s.last_autoanalyze,
        age(c.relfrozenxid) AS xid_age,
        pg_total_relation_size(c.oid) AS total_bytes,
        100.0 * io.heap_blks_hit / nullif(io.heap_blks_hit + io.heap_blks_read, 0) AS cache_hit_pct,
        c.oid AS table_oid,
        c.reltoastrelid,
        s.n_tup_upd,
        s.n_tup_hot_upd
"""

TABLE_HEALTH_QUERY = f"""
    SELECT
{_TABLE_HEALTH_COLUMNS}
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    LEFT JOIN pg_statio_user_tables io ON io.relid = s.relid
    ORDER BY s.n_dead_tup DESC
"""

TABLE_HEALTH_SINGLE_QUERY = f"""
    SELECT
{_TABLE_HEALTH_COLUMNS}
    FROM pg_stat_user_tables s
    JOIN pg_class c ON c.oid = s.relid
    LEFT JOIN pg_statio_user_tables io ON io.relid = s.relid
    WHERE s.schemaname = %s AND s.relname = %s
"""

# One row per column, combining pg_attribute (always present) with pg_stats
# (only populated once ANALYZE has run) via LEFT JOIN, so a never-analyzed
# table's columns still show up with null width/null_frac/n_distinct rather
# than vanishing. is_indexed casts indkey to int2[] (an int2vector's native
# form doesn't support ANY() directly) to check membership in any index's
# key columns, not just a single-column index.
COLUMN_DETAIL_QUERY = """
    SELECT
        a.attname,
        format_type(a.atttypid, a.atttypmod) AS data_type,
        s.avg_width,
        s.null_frac,
        s.n_distinct,
        a.attstattarget,
        EXISTS (
            SELECT 1 FROM pg_index i WHERE i.indrelid = a.attrelid AND a.attnum = ANY(i.indkey::int2[])
        ) AS is_indexed,
        a.attstorage
    FROM pg_attribute a
    LEFT JOIN pg_stats s ON s.schemaname = %s AND s.tablename = %s AND s.attname = a.attname
    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
"""

RELOPTIONS_QUERY = "SELECT reloptions FROM pg_class WHERE oid = %s"

# The server-wide values a table without its own override actually runs
# under right now — read live rather than assumed, since these are
# themselves postgresql.conf settings a DBA may have already tuned.
AUTOVACUUM_DEFAULTS_QUERY = """
    SELECT
        current_setting('autovacuum')::boolean,
        current_setting('autovacuum_vacuum_scale_factor')::float,
        current_setting('autovacuum_analyze_scale_factor')::float
"""

# pg_stat_progress_vacuum only has a row for a table while a VACUUM (manual
# or autovacuum) is actively running against it — 0 rows the rest of the
# time. Feeds find_dead_tuple_finding's "already in progress" note and
# TableAutovacuumDetail's live status, so a table sitting at a critical
# dead-tuple % isn't reported as needing action it's already getting.
VACUUM_PROGRESS_QUERY = """
    SELECT phase, heap_blks_total, heap_blks_scanned
    FROM pg_stat_progress_vacuum
    WHERE relid = %s
"""

# Feeds find_dead_tuple_growth_forecast — pgdba-store's own history, not the
# target. Capped to a recent lookback (not the full 30-day retention window)
# since a near-term trend is what a "days until X" projection should be
# based on, not a table's entire history.
DEAD_TUPLE_HISTORY_LOOKBACK_DAYS = 3
DEAD_TUPLE_HISTORY_QUERY = """
    SELECT collected_at, value
    FROM metric_points
    WHERE target_id = %s AND metric_name = 'table_dead_pct'
      AND labels->>'schema' = %s AND labels->>'table' = %s
      AND collected_at >= now() - (%s * interval '1 day')
    ORDER BY collected_at ASC
"""

_STORAGE_CODE_LABELS = {"p": "plain", "e": "external", "m": "main", "x": "extended"}


def fetch_table_health_rows(cur, allowed_schemas: list[str] | None = None) -> list:
    """Runs TABLE_HEALTH_QUERY and, when the target has TimescaleDB
    installed, rolls per-chunk rows up onto their parent hypertable
    (app/timescaledb_health.py) before anyone reads them as "the" table
    health rows — otherwise a hypertable shows up as N cryptically-named
    _timescaledb_internal chunk rows plus its own always-near-empty native
    row, instead of one row with real numbers. Shared by this module's own
    endpoints and dashboard.py::compute_health (which reuses this function
    directly for its Bloat/Wraparound tiles), so every screen agrees.

    allowed_schemas (app/schema_filter.py) is applied AFTER aggregation, not
    as a SQL WHERE clause — a hypertable's chunks physically live in
    _timescaledb_internal, not the hypertable's own schema, so filtering at
    the SQL level would silently drop every chunk (and revert to the
    near-empty native-row bug this module works around) for any target whose
    allowlist doesn't happen to also name _timescaledb_internal. Filtering
    the already-aggregated rows by their (now hypertable-identity) schema
    name instead sidesteps that entirely."""
    cur.execute(TABLE_HEALTH_QUERY)
    rows = cur.fetchall()
    if is_timescaledb_installed(cur):
        rows = aggregate_hypertable_rows(rows, cur)
    if allowed_schemas:
        rows = [row for row in rows if row[0] in allowed_schemas]
    return rows


def _reloption_value(reloptions, key, default):
    reloptions = reloptions or []
    prefix = f"{key}="
    for opt in reloptions:
        if opt.startswith(prefix):
            return opt[len(prefix) :]
    return default


def _fetch_toast_bytes(cur, table_oid, reltoastrelid) -> tuple[int, int]:
    cur.execute("SELECT pg_relation_size(%s::oid)", (table_oid,))
    heap_bytes = cur.fetchone()[0]
    toast_bytes = 0
    if reltoastrelid:
        cur.execute("SELECT pg_total_relation_size(%s::oid)", (reltoastrelid,))
        toast_bytes = cur.fetchone()[0]
    return heap_bytes, toast_bytes


def _fetch_vacuum_progress(cur, table_oid) -> tuple[str | None, float | None]:
    cur.execute(VACUUM_PROGRESS_QUERY, (table_oid,))
    row = cur.fetchone()
    if row is None:
        return None, None
    phase, heap_blks_total, heap_blks_scanned = row
    progress_pct = (100.0 * heap_blks_scanned / heap_blks_total) if heap_blks_total else None
    return phase, progress_pct


def _fetch_dead_pct_history(target_id: uuid.UUID, schema_name: str, table_name: str):
    """Reads pgdba-store, not the target — called from inside _analyze_table,
    which both callers wrap in a try/except scoped to target-connection
    errors (psycopg.Error -> 502 "Could not reach target" in the live
    endpoint, a broad Exception -> deep_scan_runs error in the nightly scan).
    A store hiccup here isn't a target problem and, for the deep scan
    especially, shouldn't abort every other table's otherwise-good findings
    just because this one table's forecast history couldn't be read — so it's
    caught locally and treated the same as "not enough history yet"."""
    try:
        with store_conn() as conn, conn.cursor() as cur:
            cur.execute(
                DEAD_TUPLE_HISTORY_QUERY, (target_id, schema_name, table_name, DEAD_TUPLE_HISTORY_LOOKBACK_DAYS)
            )
            return cur.fetchall()
    except psycopg.Error:
        return []


def _build_table_health_row(row) -> TableHealthRow:
    (
        schema_name,
        table_name,
        live_tuples,
        dead_tuples,
        last_vacuum,
        last_autovacuum,
        last_analyze,
        last_autoanalyze,
        xid_age,
        total_bytes,
        cache_hit_pct,
        _table_oid,
        _reltoastrelid,
        _n_tup_upd,
        _n_tup_hot_upd,
    ) = row
    live_tuples = live_tuples or 0
    dead_tuples = dead_tuples or 0
    total = live_tuples + dead_tuples
    dead_pct = (100.0 * dead_tuples / total) if total > 0 else 0.0
    return TableHealthRow(
        schema_name=schema_name,
        table_name=table_name,
        live_tuples=live_tuples,
        dead_tuples=dead_tuples,
        dead_pct=dead_pct,
        dead_pct_severity=dead_pct_severity(dead_pct),
        last_vacuum=last_vacuum,
        last_autovacuum=last_autovacuum,
        last_analyze=last_analyze,
        last_autoanalyze=last_autoanalyze,
        xid_age=xid_age,
        wraparound_severity=wraparound_severity(xid_age),
        total_bytes=total_bytes,
        cache_hit_pct=cache_hit_pct,
        cache_hit_pct_severity=cache_hit_severity(cache_hit_pct),
    )


def _analyze_table(cur, row, target_id: uuid.UUID) -> tuple[list[dict], dict]:
    """Runs every Table Health advisor query for one table once, and returns
    (findings, detail) — detail carries the raw supporting data (every
    column's stats, storage breakdown, effective autovacuum settings) the
    live per-table endpoint shows alongside the findings themselves.
    compute_table_findings below (used by the nightly deep scan) just takes
    the findings half; the detail half is never persisted.

    target_id is only needed for find_dead_tuple_growth_forecast, which
    reads pgdba-store's own metric_points history (table_metrics_cycle) —
    everything else here queries the target via cur, same as before.

    For a TimescaleDB hypertable, `row`'s dead/live tuple counts are already
    a chunk-aggregate (app/timescaledb_health.py), but `table_oid`/
    `reltoastrelid` still point at the hypertable's own (real, but nearly
    empty) relation — so this function's own heap/TOAST byte lookups
    (_fetch_toast_bytes) and column/reloptions reads describe that one
    relation, not a chunk-aggregate. Known, scoped limitation: the primary
    Table Health list is chunk-aware; this expandable per-table detail panel
    isn't yet."""
    (
        schema_name,
        table_name,
        n_live_tup,
        n_dead_tup,
        last_vacuum,
        last_autovacuum,
        last_analyze,
        last_autoanalyze,
        _xid_age,
        total_bytes,
        _cache_hit_pct,
        table_oid,
        reltoastrelid,
        n_tup_upd,
        n_tup_hot_upd,
    ) = row
    n_live_tup = n_live_tup or 0
    n_dead_tup = n_dead_tup or 0
    n_tup_upd = n_tup_upd or 0
    n_tup_hot_upd = n_tup_hot_upd or 0
    total = n_live_tup + n_dead_tup
    dead_pct = (100.0 * n_dead_tup / total) if total > 0 else 0.0
    severity = dead_pct_severity(dead_pct)

    heap_bytes, toast_bytes = _fetch_toast_bytes(cur, table_oid, reltoastrelid)

    cur.execute(COLUMN_DETAIL_QUERY, (schema_name, table_name, table_oid))
    column_rows = [ColumnDetail(*col_row) for col_row in cur.fetchall()]
    # attstorage comes back as a single-char code (p/e/m/x) — resolved to a
    # readable label once here rather than in every consumer.
    column_rows = [col._replace(storage=_STORAGE_CODE_LABELS.get(col.storage, col.storage)) for col in column_rows]

    cur.execute(RELOPTIONS_QUERY, (table_oid,))
    reloptions_row = cur.fetchone()
    reloptions = reloptions_row[0] if reloptions_row else None

    cur.execute(AUTOVACUUM_DEFAULTS_QUERY)
    server_autovacuum_enabled, server_vacuum_scale_factor, server_analyze_scale_factor = cur.fetchone()

    autovacuum_enabled_override = _reloption_value(reloptions, "autovacuum_enabled", None)
    autovacuum_enabled = (
        (autovacuum_enabled_override != "false") if autovacuum_enabled_override is not None else server_autovacuum_enabled
    )
    has_custom_scale_factor = _reloption_value(reloptions, "autovacuum_vacuum_scale_factor", None) is not None
    vacuum_scale_factor = float(
        _reloption_value(reloptions, "autovacuum_vacuum_scale_factor", server_vacuum_scale_factor)
    )
    analyze_scale_factor = float(
        _reloption_value(reloptions, "autovacuum_analyze_scale_factor", server_analyze_scale_factor)
    )
    fillfactor = int(_reloption_value(reloptions, "fillfactor", 100))
    hot_update_ratio = (n_tup_hot_upd / n_tup_upd) if n_tup_upd > 0 else None

    vacuum_phase, vacuum_progress_pct = _fetch_vacuum_progress(cur, table_oid)
    dead_pct_history = _fetch_dead_pct_history(target_id, schema_name, table_name)

    findings = (
        find_never_vacuumed(schema_name, table_name, n_live_tup, last_vacuum, last_autovacuum)
        + find_never_analyzed(schema_name, table_name, n_live_tup, last_analyze, last_autoanalyze)
        + find_dead_tuple_finding(schema_name, table_name, dead_pct, severity, vacuum_phase, vacuum_progress_pct)
        + find_dead_tuple_growth_forecast(schema_name, table_name, dead_pct, severity, dead_pct_history)
        + find_toast_heavy(schema_name, table_name, heap_bytes, toast_bytes, column_rows)
        + find_wide_columns(schema_name, table_name, column_rows)
        + find_low_statistics_target_candidates(schema_name, table_name, n_live_tup, column_rows)
        + find_autovacuum_scale_factor_candidate(
            schema_name, table_name, n_live_tup, has_custom_scale_factor, vacuum_scale_factor
        )
        + find_low_hot_update_ratio_candidate(schema_name, table_name, n_tup_upd, n_tup_hot_upd, fillfactor)
    )

    detail = {
        "columns": column_rows,
        "heap_bytes": heap_bytes,
        "toast_bytes": toast_bytes,
        "total_bytes": total_bytes,
        "autovacuum_enabled": autovacuum_enabled,
        "vacuum_scale_factor": vacuum_scale_factor,
        "analyze_scale_factor": analyze_scale_factor,
        "has_custom_scale_factor": has_custom_scale_factor,
        "fillfactor": fillfactor,
        "n_live_tup": n_live_tup,
        "n_dead_tup": n_dead_tup,
        "n_tup_upd": n_tup_upd,
        "n_tup_hot_upd": n_tup_hot_upd,
        "hot_update_ratio": hot_update_ratio,
        "vacuum_phase": vacuum_phase,
        "vacuum_progress_pct": vacuum_progress_pct,
    }
    return findings, detail


def compute_table_findings(cur, row, target_id: uuid.UUID) -> list[dict]:
    """Findings only — used by compute_all_table_health_findings (the
    nightly deep scan's per-table loop), which has no use for the detail
    half _analyze_table also computes."""
    return _analyze_table(cur, row, target_id)[0]


def compute_all_table_health_findings(cur, target_id: uuid.UUID) -> list[dict]:
    """Loops compute_table_findings across every user table — used only by
    the nightly deep scan (scheduler.py::run_deep_scan_cycle) and its manual
    "Run Deep Scan Now" trigger. The live Table Health screen never calls
    this: it fetches per-table findings lazily, only for a table the user
    actually expands, to keep the existing 15s poll cheap. Already has
    target_id in scope (table-health's one special case needing it, per its
    own dead-tuple-growth-forecast use below), so its schema allowlist
    (app/schema_filter.py) is applied here too, unlike the other seven deep
    scan categories."""
    rows = fetch_table_health_rows(cur, get_allowed_schemas(target_id))
    findings = []
    for row in rows:
        findings += compute_table_findings(cur, row, target_id)
    return findings


@router.get("/{target_id}/table-health", response_model=TableHealthResponse)
def get_table_health(target_id: uuid.UUID):
    allowed_schemas = get_allowed_schemas(target_id)
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            rows = fetch_table_health_rows(cur, allowed_schemas)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    return TableHealthResponse(tables=[_build_table_health_row(row) for row in rows])


@router.get(
    "/{target_id}/table-health/{schema_name}/{table_name}/findings",
    response_model=TableHealthDetailResponse,
)
def get_table_health_findings(target_id: uuid.UUID, schema_name: str, table_name: str):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(TABLE_HEALTH_SINGLE_QUERY, (schema_name, table_name))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Table not found on target.")
            findings, detail = _analyze_table(cur, row, target_id)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]

    index_bytes = max(0, detail["total_bytes"] - detail["heap_bytes"] - detail["toast_bytes"])

    return TableHealthDetailResponse(
        findings=[IndexFinding(**finding) for finding in findings],
        columns=[
            TableColumnDetail(
                attname=col.attname,
                data_type=col.data_type,
                avg_width=col.avg_width,
                null_frac=col.null_frac,
                n_distinct=col.n_distinct,
                attstattarget=col.attstattarget,
                is_indexed=col.is_indexed,
                storage=col.storage,
            )
            for col in detail["columns"]
        ],
        storage=TableStorageDetail(
            heap_bytes=detail["heap_bytes"],
            toast_bytes=detail["toast_bytes"],
            index_bytes=index_bytes,
            total_bytes=detail["total_bytes"],
        ),
        autovacuum=TableAutovacuumDetail(
            autovacuum_enabled=detail["autovacuum_enabled"],
            vacuum_scale_factor=detail["vacuum_scale_factor"],
            analyze_scale_factor=detail["analyze_scale_factor"],
            has_custom_scale_factor=detail["has_custom_scale_factor"],
            fillfactor=detail["fillfactor"],
            n_live_tup=detail["n_live_tup"],
            n_dead_tup=detail["n_dead_tup"],
            n_tup_upd=detail["n_tup_upd"],
            n_tup_hot_upd=detail["n_tup_hot_upd"],
            hot_update_ratio=detail["hot_update_ratio"],
            vacuum_in_progress=detail["vacuum_phase"] is not None,
            vacuum_phase=detail["vacuum_phase"],
            vacuum_progress_pct=detail["vacuum_progress_pct"],
        ),
    )


@router.post("/{target_id}/table-health/apply", response_model=MaintenanceResult)
def apply_table_health_finding(target_id: uuid.UUID, payload: ApplyTableHealthFindingRequest):
    """Re-derives the finding's DDL server-side from the target's current
    catalog state — statistics target, autovacuum scale factor, and
    fillfactor are the checks that have one — and runs only that. The
    client names which table and which finding to apply, never the DDL
    itself. All three are fast, non-destructive ALTER TABLE metadata changes
    (no CONCURRENTLY, no lock beyond a brief catalog update), so this runs
    in a normal transaction rather than needing autocommit."""
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(TABLE_HEALTH_SINGLE_QUERY, (payload.schema_name, payload.table_name))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Table not found on target.")
            findings, _detail = _analyze_table(cur, row, target_id)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    finding = next((f for f in findings if f["id"] == payload.finding_id), None)
    if finding is None:
        raise HTTPException(
            status_code=404, detail="This finding no longer applies — it may have been resolved already."
        )
    if not finding.get("recommended_ddl"):
        raise HTTPException(status_code=400, detail="This finding has no DDL to apply.")

    statements = [line.strip() for line in finding["recommended_ddl"].splitlines() if line.strip()]
    try:
        with connect_to_target(target_id, autocommit=True) as conn, conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not apply this change: {exc}") from exc

    return MaintenanceResult(ok=True, message="Applied.")


def _table_health_deep_scan_status(target_id: uuid.UUID) -> TableHealthDeepScanStatus:
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT finished_at, status, error FROM deep_scan_runs
            WHERE target_id = %s AND category = 'table health'
            ORDER BY finished_at DESC LIMIT 1
            """,
            (target_id,),
        )
        run_row = cur.fetchone()

        cur.execute(
            "SELECT count(*) FROM deep_scan_findings WHERE target_id = %s AND category = 'table health' AND status = 'open'",
            (target_id,),
        )
        open_findings = cur.fetchone()[0]

    return TableHealthDeepScanStatus(
        last_run_at=run_row[0] if run_row else None,
        last_run_status=run_row[1] if run_row else None,
        last_run_error=run_row[2] if run_row else None,
        open_findings=open_findings,
    )


@router.get("/{target_id}/table-health/deep-scan", response_model=TableHealthDeepScanStatus)
def get_table_health_deep_scan_status(target_id: uuid.UUID):
    return _table_health_deep_scan_status(target_id)


@router.post("/{target_id}/table-health/deep-scan", response_model=TableHealthDeepScanStatus)
def run_table_health_deep_scan(target_id: uuid.UUID):
    """"Run Deep Scan Now" — runs the table-health category immediately for
    this target instead of waiting for the nightly cron, writing to the
    same deep_scan_findings/deep_scan_runs history (docs/DATA_MODEL.md).

    Imports scheduler locally (not at module level) since scheduler.py
    itself imports compute_all_table_health_findings from this module —
    a module-level import here would be a circular import at startup."""
    from app import scheduler

    try:
        scheduler.run_table_health_deep_scan_now(target_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _table_health_deep_scan_status(target_id)
