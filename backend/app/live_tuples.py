"""Live-row counts that survive a statistics reset.

pg_stat_user_tables.n_live_tup / n_dead_tup are activity counters, not row
counts: pg_stat_reset(), a crash or unclean restart, and a restore or
pg_upgrade all set them back to zero, and they're only rebuilt when VACUUM
or ANALYZE next runs on the table. Until then a 10-million-row table reads
"0 live", and 10 rows updated since read "0 live / 10 dead" — 100% dead,
a false bloat alarm.

pg_class.reltuples (the planner's estimate, kept in the catalog) survives
all of that. So: when no vacuum or analyze has been recorded since the
counters were reset (the last_* timestamps are reset with them) and the
catalog knows more rows than the counter, the catalog's number is used and
flagged as an estimate.

The SQL fragments expect pg_stat_user_tables aliased s and pg_class c.
"""

# No VACUUM/ANALYZE recorded since the counters were last reset (or since
# the table was created).
NO_MAINTENANCE_SINCE_RESET = "coalesce(s.last_vacuum, s.last_autovacuum, s.last_analyze, s.last_autoanalyze) IS NULL"

LIVE_TUPLES_SQL = (
    f"CASE WHEN {NO_MAINTENANCE_SINCE_RESET} AND c.reltuples > s.n_live_tup "
    "THEN c.reltuples::bigint ELSE s.n_live_tup END"
)

# The live count can't be trusted as-is: it comes from the catalog estimate,
# or the table has data pages but has never been analyzed (reltuples -1 on
# PostgreSQL 14+), so neither number is real.
LIVE_TUPLES_ESTIMATED_SQL = (
    f"({NO_MAINTENANCE_SINCE_RESET} AND (c.reltuples > s.n_live_tup OR (c.reltuples < 0 AND c.relpages > 0)))"
)

DEAD_PCT_SQL = (
    f"CASE WHEN ({LIVE_TUPLES_SQL}) + s.n_dead_tup > 0 "
    f"THEN 100.0 * s.n_dead_tup / (({LIVE_TUPLES_SQL}) + s.n_dead_tup) ELSE 0 END"
)
