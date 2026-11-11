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


# The cache hit rate isn't one of these: it's the ratio between two samples
# of pg_stat_database's counters, not a single reading — see app/cache_hit.py
# and app/scheduler.py::_collect_cache_hit_ratio.

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

# Feeds Trends' wraparound forecast (routers/metrics.py), which the Dashboard
# tile alone can't provide since it only ever shows the current age, never a
# trend. The database's datfrozenxid is the oldest unfrozen XID of any table
# in it — catalogs and TOAST tables included — so it's the age that actually
# runs out; it used to be the oldest user table's heap only. The metric name
# is kept so existing history stays on the same chart.
WORST_TABLE_XID_AGE = Collector(
    name="worst_table_xid_age",
    metric_name="worst_table_xid_age",
    query="SELECT age(datfrozenxid) FROM pg_database WHERE datname = current_database()",
)

# Multixact IDs wrap just like XIDs (app/wraparound.py).
DATABASE_MXID_AGE = Collector(
    name="database_mxid_age",
    metric_name="database_mxid_age",
    query="SELECT mxid_age(datminmxid) FROM pg_database WHERE datname = current_database()",
)

COLLECTORS = [CONNECTION_COUNT, WORST_TABLE_DEAD_PCT, DATABASE_SIZE, WORST_TABLE_XID_AGE, DATABASE_MXID_AGE]
