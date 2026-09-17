"""Pure Table Health advisor checks — vacuum/analyze hygiene, TOAST usage,
wide columns, missing per-column statistics targets, HOT-update ratio, and
autovacuum tuning for large tables. Each function takes already-fetched
rows/values and returns finding dicts, mirroring app/index_analysis.py's
pattern: the queries themselves, and the per-table/all-tables combinators
(compute_table_findings / compute_all_table_health_findings), live in
routers/table_health.py, reused by both the live per-table detail endpoint
and the nightly deep scan (scheduler.py::run_deep_scan_cycle).
"""

from typing import NamedTuple

from app.forecasting import days_to_reach, fit_linear_trend
from app.severity import DEAD_PCT_ATTENTION

CATEGORY = "table health"


class ColumnDetail(NamedTuple):
    """One row from routers/table_health.py's COLUMN_DETAIL_QUERY — also
    returned as-is (via schemas.TableColumnDetail) in the live per-table
    detail response, so a user can see every column's stats, not just the
    ones that happened to trigger a finding."""

    attname: str
    data_type: str
    avg_width: int | None
    null_frac: float | None
    n_distinct: float | None
    # NULL means "using the system default" on PostgreSQL 15+ (which
    # repurposed -1, used for the same meaning on PG13/14, to NULL).
    attstattarget: int | None
    is_indexed: bool
    storage: str


def find_never_vacuumed(schema_name, table_name, n_live_tup, last_vacuum, last_autovacuum) -> list[dict]:
    if n_live_tup <= 0 or last_vacuum is not None or last_autovacuum is not None:
        return []
    return [
        {
            "id": f"table-health-never-vacuumed-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "attention",
            "title": f"{schema_name}.{table_name} has never been vacuumed",
            "summary": "This table has live rows but no vacuum — manual or automatic — has ever run on it.",
            "detail": f"n_live_tup={n_live_tup} last_vacuum=NULL last_autovacuum=NULL",
            "suggested_action": "Run Vacuum from this screen, and confirm autovacuum is enabled for this table.",
        }
    ]


def find_never_analyzed(schema_name, table_name, n_live_tup, last_analyze, last_autoanalyze) -> list[dict]:
    if n_live_tup <= 0 or last_analyze is not None or last_autoanalyze is not None:
        return []
    return [
        {
            "id": f"table-health-never-analyzed-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "attention",
            "title": f"{schema_name}.{table_name} has never been analyzed",
            "summary": "The query planner has no statistics for this table yet, which can lead to poor plan choices.",
            "detail": f"n_live_tup={n_live_tup} last_analyze=NULL last_autoanalyze=NULL",
            "suggested_action": "Run Analyze from this screen.",
        }
    ]


def find_dead_tuple_finding(
    schema_name, table_name, dead_pct, severity, vacuum_phase=None, vacuum_progress_pct=None
) -> list[dict]:
    """Restates Table Health's own dead_pct/severity as an actionable card,
    same thresholds (severity.py::dead_pct_severity) as the column already
    shown in the row — this screen already computes it, so no new query.
    vacuum_phase/vacuum_progress_pct (from pg_stat_progress_vacuum, routers/
    table_health.py) fold in whether a vacuum is already running on this
    table right now, so a DBA looking at a critical dead-tuple % isn't told
    to do something that's already in flight."""
    if severity == "healthy":
        return []

    if vacuum_phase is not None:
        progress = f", ~{vacuum_progress_pct:.0f}% of the heap scanned" if vacuum_progress_pct is not None else ""
        progress_note = (
            f" A vacuum is currently running on this table (phase: {vacuum_phase}{progress}) — this "
            "percentage may already be dropping without further action."
        )
        suggested_action = "Vacuum is already in progress; check back after it finishes before running it again."
    else:
        progress_note = ""
        suggested_action = (
            "Run Vacuum from this screen, or lower this table's autovacuum_vacuum_scale_factor if this recurs."
        )

    return [
        {
            "id": f"table-health-dead-tuples-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": severity,
            "title": f"{schema_name}.{table_name} is accumulating dead rows",
            "summary": f"About {dead_pct:.0f}% of this table's rows are dead but not yet reclaimed by vacuum.{progress_note}",
            "detail": f"dead_pct={dead_pct:.1f}" + (f" vacuum_phase={vacuum_phase}" if vacuum_phase else ""),
            "suggested_action": suggested_action,
        }
    ]


# Only worth predicting a crossing when the projected crossing is close
# enough to be actionable — a table crawling toward "attention" over the
# next six months isn't something a DBA needs to hear about today.
# fit_linear_trend's own defaults (app/forecasting.py) gate whether there's
# enough history to trust a trend at all.
DEAD_TUPLE_FORECAST_HORIZON_DAYS = 14


def find_dead_tuple_growth_forecast(schema_name, table_name, dead_pct, severity, history) -> list[dict]:
    """history: (collected_at, value) dead_pct samples for this table from
    pgdba-store's metric_points (table_metrics_cycle's existing 5-minute
    collection — no new collector), oldest first. Fits a simple linear trend
    (app/forecasting.py) and projects forward; only raises a finding while
    the table is still healthy today (find_dead_tuple_finding above already
    covers "already crossed") and the projected crossing falls within
    DEAD_TUPLE_FORECAST_HORIZON_DAYS, so this is additive signal, not a
    louder duplicate of the existing check."""
    if severity != "healthy":
        return []

    trend = fit_linear_trend(history)
    if trend is None:
        return []
    slope, span_days = trend  # dead_pct points per day

    days_to_attention = days_to_reach(dead_pct, DEAD_PCT_ATTENTION, slope)
    if days_to_attention is None or days_to_attention > DEAD_TUPLE_FORECAST_HORIZON_DAYS:
        return []

    return [
        {
            "id": f"table-health-dead-tuple-forecast-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "attention",
            "title": (
                f"{schema_name}.{table_name} is on track to hit the dead-row attention threshold in "
                f"~{days_to_attention:.0f} day{'s' if round(days_to_attention) != 1 else ''}"
            ),
            "summary": (
                f"Dead-row percentage has been climbing about {slope:.2f} points/day over the last "
                f"{span_days:.1f} days — currently {dead_pct:.1f}%, heading toward the "
                f"{DEAD_PCT_ATTENTION}% threshold."
            ),
            "detail": (
                f"dead_pct={dead_pct:.2f} slope_pct_per_day={slope:.3f} "
                f"projected_days_to_attention={days_to_attention:.1f} sample_span_days={span_days:.1f}"
            ),
            "suggested_action": (
                "Not urgent yet, but worth a look: confirm autovacuum is enabled and keeping pace on this "
                "table, or run Vacuum proactively if a write spike is expected."
            ),
        }
    ]


# A table's size in TOAST (large text/bytea/jsonb values stored out-of-line,
# compressed) is only worth flagging once it's both a meaningful absolute
# size and a real fraction of the table's total footprint — a tiny table
# that's 90% TOAST by fraction but 200KB total isn't worth mentioning.
TOAST_HEAVY_MIN_BYTES = 10 * 1024 * 1024
TOAST_HEAVY_FRACTION = 0.3

# Naming the actual columns driving TOAST usage (and a type-aware nudge for
# each) turns "this table uses a lot of TOAST" into something actionable —
# a much lower bar than the wide-column check above, since several
# moderately-sized toastable columns can add up to real TOAST usage even
# when none of them individually crosses that check's 500-byte threshold.
# storage != 'plain' is what actually identifies a toastable column — attstorage
# is Postgres' own signal, more reliable than guessing from the type name.
TOAST_CULPRIT_MIN_AVG_WIDTH = 100
TOAST_CULPRIT_MAX_COLUMNS = 3


def _toast_column_advice(data_type: str) -> str:
    lowered = data_type.lower()
    if "bytea" in lowered:
        return (
            "binary data — if it's already compressed (images, zip/gzip, PDFs), TOAST's own compression "
            "attempt is wasted effort on every write; consider object storage (S3 or similar) with just a "
            "reference here, or ALTER COLUMN ... SET STORAGE EXTERNAL to skip the compression attempt"
        )
    if "json" in lowered:
        return (
            "JSON — consider pulling the specific keys you actually filter/sort/join on into real columns, "
            "keeping the rest here or in a separate table"
        )
    if "text" in lowered or "character varying" in lowered:
        return (
            "long text — consider whether the full value needs to be read on this table's normal hot path, "
            "or could live in a separate table fetched only when actually needed"
        )
    return "a large value — worth checking whether it needs to be inline in this table at all"


def find_toast_heavy(schema_name, table_name, heap_bytes, toast_bytes, column_rows) -> list[dict]:
    total = heap_bytes + toast_bytes
    if total == 0 or toast_bytes < TOAST_HEAVY_MIN_BYTES:
        return []
    fraction = toast_bytes / total
    if fraction < TOAST_HEAVY_FRACTION:
        return []

    culprits = sorted(
        (col for col in column_rows if col.storage != "plain" and (col.avg_width or 0) >= TOAST_CULPRIT_MIN_AVG_WIDTH),
        key=lambda col: col.avg_width or 0,
        reverse=True,
    )[:TOAST_CULPRIT_MAX_COLUMNS]

    if culprits:
        suggested_action = " ".join(
            f"{col.attname} ({col.data_type}, avg {col.avg_width} B): {_toast_column_advice(col.data_type)}."
            for col in culprits
        )
    else:
        suggested_action = (
            "No single column stands out yet (run Analyze on this table if it hasn't been recently) — in "
            "general, consider whether large values (long text, JSON blobs, binaries) belong in this table, "
            "or would be better as a reference to object storage or a separate table."
        )

    return [
        {
            "id": f"table-health-toast-heavy-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "unknown",
            "title": f"{schema_name}.{table_name} stores most of its data out-of-line (TOAST)",
            "summary": (
                f"{fraction * 100:.0f}% of this table's size ({toast_bytes // (1024 * 1024):,} MB) lives in its "
                "TOAST table rather than the main table — large text/bytea/jsonb column values."
            ),
            "detail": f"toast_bytes={toast_bytes} heap_bytes={heap_bytes} fraction={fraction:.2f}",
            "suggested_action": suggested_action,
        }
    ]


WIDE_COLUMN_ATTENTION_BYTES = 500
WIDE_COLUMN_CRITICAL_BYTES = 2000


def find_wide_columns(schema_name, table_name, column_rows) -> list[dict]:
    """column_rows: ColumnDetail tuples (see routers/table_health.py's
    COLUMN_DETAIL_QUERY) — avg_width is only populated once a column has
    been analyzed at least once."""
    findings = []
    for col in column_rows:
        avg_width = col.avg_width
        if avg_width is None or avg_width < WIDE_COLUMN_ATTENTION_BYTES:
            continue
        attname = col.attname
        severity = "critical" if avg_width >= WIDE_COLUMN_CRITICAL_BYTES else "attention"
        findings.append(
            {
                "id": f"table-health-wide-column-{schema_name}-{table_name}-{attname}",
                "category": CATEGORY,
                "severity": severity,
                "title": f"{schema_name}.{table_name}.{attname} averages {avg_width:,} bytes per value",
                "summary": (
                    "Wide column values inflate this table's row size, slow sequential scans, and increase "
                    "TOAST usage."
                ),
                "detail": f"avg_width={avg_width}",
                "suggested_action": (
                    "Consider whether this content belongs in this table, or should move to object storage "
                    "or a separate table."
                ),
            }
        )
    return findings


# Only worth flagging on a table large enough for planner mis-estimates to
# actually matter, and only for a column the planner leans on for row
# estimates (indexed) that's still on the default target despite high
# cardinality. "Using the default" is spelled two different ways depending
# on server version: PG13/14 report -1; PG15+ changed the sentinel to NULL
# (attstattarget is nullable there) — DEFAULT_STATS_TARGET_SENTINELS covers
# both. n_distinct is pg_stats' own convention: a non-negative value is an
# absolute distinct-value estimate, a negative value is a fraction of the
# table's rows (e.g. -0.5 means ~50% of rows are distinct).
DEFAULT_STATS_TARGET_SENTINELS = (None, -1)
LOW_STATS_TARGET_ROW_THRESHOLD = 100_000
LOW_STATS_TARGET_N_DISTINCT_ABS = 1000
LOW_STATS_TARGET_N_DISTINCT_FRACTION = 0.1
SUGGESTED_STATISTICS_TARGET = 500


def find_low_statistics_target_candidates(schema_name, table_name, n_live_tup, column_rows) -> list[dict]:
    """column_rows: ColumnDetail tuples."""
    if n_live_tup < LOW_STATS_TARGET_ROW_THRESHOLD:
        return []

    findings = []
    for col in column_rows:
        attname, attstattarget, n_distinct, is_indexed = col.attname, col.attstattarget, col.n_distinct, col.is_indexed
        if not is_indexed or attstattarget not in DEFAULT_STATS_TARGET_SENTINELS or n_distinct is None:
            continue
        high_cardinality = (n_distinct >= 0 and n_distinct >= LOW_STATS_TARGET_N_DISTINCT_ABS) or (
            n_distinct < 0 and abs(n_distinct) >= LOW_STATS_TARGET_N_DISTINCT_FRACTION
        )
        if not high_cardinality:
            continue
        findings.append(
            {
                "id": f"table-health-low-stats-target-{schema_name}-{table_name}-{attname}",
                "category": CATEGORY,
                "severity": "unknown",
                "title": f"{schema_name}.{table_name}.{attname} might benefit from a higher statistics target",
                "summary": (
                    "This indexed column has high cardinality but still uses the default statistics target, "
                    "which can lead to poor row-count estimates and suboptimal query plans."
                ),
                "detail": f"attstattarget={attstattarget} (default) n_distinct={n_distinct} n_live_tup={n_live_tup}",
                "suggested_action": f"Raise its statistics target, e.g. to {SUGGESTED_STATISTICS_TARGET}, then re-analyze.",
                "recommended_ddl": (
                    f"ALTER TABLE {schema_name}.{table_name} ALTER COLUMN {attname} "
                    f"SET STATISTICS {SUGGESTED_STATISTICS_TARGET};\n"
                    f"ANALYZE {schema_name}.{table_name};"
                ),
            }
        )
    return findings


# A fixed percentage scale factor means a huge table waits for a huge
# absolute number of dead rows before autovacuum triggers — this only
# matters once a table is large enough for that gap to be real bloat, and
# only if it doesn't already have a table-level override.
AUTOVACUUM_SCALE_FACTOR_ROW_THRESHOLD = 5_000_000
SUGGESTED_AUTOVACUUM_VACUUM_SCALE_FACTOR = 0.02
SUGGESTED_AUTOVACUUM_ANALYZE_SCALE_FACTOR = 0.02


def find_autovacuum_scale_factor_candidate(
    schema_name, table_name, n_live_tup, has_custom_scale_factor, effective_vacuum_scale_factor
) -> list[dict]:
    """effective_vacuum_scale_factor is whatever's actually in effect right
    now — the server-wide `current_setting('autovacuum_vacuum_scale_factor')`
    when there's no table-level override, so the finding always states the
    real current value rather than assuming the compiled-in 0.2 default."""
    if n_live_tup < AUTOVACUUM_SCALE_FACTOR_ROW_THRESHOLD or has_custom_scale_factor:
        return []
    return [
        {
            "id": f"table-health-autovacuum-scale-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "unknown",
            "title": f"{schema_name}.{table_name} is large enough to need a tighter autovacuum threshold",
            "summary": (
                f"With {n_live_tup:,} live rows, the current autovacuum_vacuum_scale_factor "
                f"({effective_vacuum_scale_factor * 100:.0f}%, server-wide default) means autovacuum waits "
                "for a huge absolute number of dead rows before it runs on this table."
            ),
            "detail": f"n_live_tup={n_live_tup} autovacuum_vacuum_scale_factor={effective_vacuum_scale_factor} (server default, no table override)",
            "suggested_action": f"Lower this table's autovacuum scale factors, e.g. to {SUGGESTED_AUTOVACUUM_VACUUM_SCALE_FACTOR}.",
            "recommended_ddl": (
                f"ALTER TABLE {schema_name}.{table_name} SET (autovacuum_vacuum_scale_factor = "
                f"{SUGGESTED_AUTOVACUUM_VACUUM_SCALE_FACTOR}, autovacuum_analyze_scale_factor = "
                f"{SUGGESTED_AUTOVACUUM_ANALYZE_SCALE_FACTOR});"
            ),
        }
    ]


# HOT (Heap-Only Tuple) updates avoid touching any index at all, which is
# the cheapest possible update path and the main reason fillfactor exists —
# leaving free space per page for new row versions to land in-place. A low
# ratio on a table with real update volume means most updates are paying
# full index-maintenance cost that a lower fillfactor could avoid.
HOT_UPDATE_ROW_THRESHOLD = 10_000
HOT_UPDATE_RATIO_ATTENTION = 0.5
SUGGESTED_FILLFACTOR = 90


def find_low_hot_update_ratio_candidate(schema_name, table_name, n_tup_upd, n_tup_hot_upd, fillfactor) -> list[dict]:
    if n_tup_upd < HOT_UPDATE_ROW_THRESHOLD:
        return []
    hot_ratio = n_tup_hot_upd / n_tup_upd
    if hot_ratio >= HOT_UPDATE_RATIO_ATTENTION or fillfactor <= SUGGESTED_FILLFACTOR:
        return []
    return [
        {
            "id": f"table-health-hot-update-ratio-{schema_name}-{table_name}",
            "category": CATEGORY,
            "severity": "unknown",
            "title": f"{schema_name}.{table_name} is missing most of its HOT-update fast path",
            "summary": (
                f"Only {hot_ratio * 100:.0f}% of updates on this table avoided touching an index (HOT updates) "
                f"— the rest paid full index-maintenance cost on every update, out of {n_tup_upd:,} updates."
            ),
            "detail": f"n_tup_upd={n_tup_upd} n_tup_hot_upd={n_tup_hot_upd} hot_ratio={hot_ratio:.2f} fillfactor={fillfactor}",
            "suggested_action": (
                f"Lower this table's fillfactor, e.g. to {SUGGESTED_FILLFACTOR}, to leave room on each page for "
                "new row versions to land in-place. Only affects pages written after the change — a one-time "
                "VACUUM FULL (exclusive lock) rewrites existing ones immediately if you want it right away."
            ),
            "recommended_ddl": f"ALTER TABLE {schema_name}.{table_name} SET (fillfactor = {SUGGESTED_FILLFACTOR});",
        }
    ]
