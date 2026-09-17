from dataclasses import dataclass


@dataclass(frozen=True)
class Collector:
    """A single metric to poll from a target database.

    Adding a new metric is adding one of these to COLLECTORS below — the
    scheduler and storage layer don't change.
    """

    name: str
    metric_name: str
    query: str


CACHE_HIT_RATIO = Collector(
    name="cache_hit_ratio",
    metric_name="cache_hit_ratio",
    query="""
        SELECT 100.0 * sum(heap_blks_hit) / nullif(sum(heap_blks_hit) + sum(heap_blks_read), 0)
        FROM pg_statio_user_tables
    """,
)

CONNECTION_COUNT = Collector(
    name="connection_count",
    metric_name="connection_count",
    query="SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()",
)

# Worst table across the database, same "worst offender" framing the
# dashboard's bloat category already uses — one trend line stands in for
# per-table bloat without needing a per-table time series.
WORST_TABLE_DEAD_PCT = Collector(
    name="worst_table_dead_pct",
    metric_name="worst_table_dead_pct",
    query="""
        SELECT COALESCE(MAX(
            CASE WHEN (n_live_tup + n_dead_tup) > 0
                 THEN 100.0 * n_dead_tup / (n_live_tup + n_dead_tup)
                 ELSE 0 END
        ), 0)
        FROM pg_stat_user_tables
    """,
)

DATABASE_SIZE = Collector(
    name="database_size",
    metric_name="database_size_bytes",
    query="SELECT pg_database_size(current_database())",
)

# Same "worst offender, one trend line" framing as WORST_TABLE_DEAD_PCT above
# and the same join Dashboard's own Wraparound category and Table Health use
# (pg_stat_user_tables joined to pg_class) — feeds Trends' wraparound
# forecast (routers/metrics.py), which the Dashboard tile alone can't
# provide since it only ever shows the current age, never a trend.
WORST_TABLE_XID_AGE = Collector(
    name="worst_table_xid_age",
    metric_name="worst_table_xid_age",
    query="""
        SELECT COALESCE(MAX(age(c.relfrozenxid)), 0)
        FROM pg_stat_user_tables s
        JOIN pg_class c ON c.oid = s.relid
    """,
)

COLLECTORS = [CACHE_HIT_RATIO, CONNECTION_COUNT, WORST_TABLE_DEAD_PCT, DATABASE_SIZE, WORST_TABLE_XID_AGE]
