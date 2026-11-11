-- Data quality v2. Additive only: an install upgraded from the previous
-- version keeps all of its history, and nothing the previous backend version
-- reads or writes is renamed or dropped, so it can still run against this
-- schema too.

-- Query History rows were stored as the cumulative pg_stat_statements total
-- while calls were an interval delta, so rows per call meant nothing. From
-- now on query_stat_snapshots.rows is this interval's delta as well, diffed
-- against the new tracked_queries.last_rows baseline.
--
-- last_rows stays NULL on every row recorded by an older version. The
-- scheduler treats that as "no usable baseline": it re-baselines on the next
-- cycle instead of diffing, because the older baseline came from a single
-- pg_stat_statements row, not the per-queryid sum the scheduler now reads.
ALTER TABLE tracked_queries ADD COLUMN IF NOT EXISTS last_rows BIGINT;

-- The old cumulative values can't be turned into per-interval deltas after
-- the fact, so they're cleared (NULL = unknown) rather than left looking like
-- a per-interval count.
ALTER TABLE query_stat_snapshots ALTER COLUMN rows DROP NOT NULL;
UPDATE query_stat_snapshots SET rows = NULL WHERE rows IS NOT NULL;

-- Typed per-table history (app/scheduler.py::_collect_table_metrics). Before
-- this, every table cost two JSONB-labelled metric_points rows per cycle;
-- this is one narrow row with the counters later features need. All rows
-- from one cycle share one collected_at (the same transaction's now()).
CREATE TABLE IF NOT EXISTS table_stat_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    target_id           UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_name         TEXT NOT NULL,
    table_name          TEXT NOT NULL,
    dead_pct            DOUBLE PRECISION,
    total_bytes         BIGINT,
    n_live_tup          BIGINT,
    n_dead_tup          BIGINT,
    n_mod_since_analyze BIGINT,
    seq_scan            BIGINT,
    seq_tup_read        BIGINT,
    idx_scan            BIGINT,
    idx_tup_fetch       BIGINT,
    n_tup_ins           BIGINT,
    n_tup_upd           BIGINT,
    n_tup_del           BIGINT,
    n_tup_hot_upd       BIGINT,
    heap_blks_read      BIGINT,
    heap_blks_hit       BIGINT
);

CREATE INDEX IF NOT EXISTS table_stat_snapshots_target_table_time_idx
    ON table_stat_snapshots (target_id, schema_name, table_name, collected_at);
CREATE INDEX IF NOT EXISTS table_stat_snapshots_target_time_idx
    ON table_stat_snapshots (target_id, collected_at);

-- Same idea per index, written on the same cycle. Nothing reads it yet; it's
-- the history the workload features build on (idx_scan deltas over time).
CREATE TABLE IF NOT EXISTS index_stat_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    target_id     UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    collected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_name   TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    index_name    TEXT NOT NULL,
    idx_scan      BIGINT,
    idx_tup_read  BIGINT,
    idx_tup_fetch BIGINT,
    size_bytes    BIGINT
);

CREATE INDEX IF NOT EXISTS index_stat_snapshots_target_index_time_idx
    ON index_stat_snapshots (target_id, schema_name, index_name, collected_at);
CREATE INDEX IF NOT EXISTS index_stat_snapshots_target_time_idx
    ON index_stat_snapshots (target_id, collected_at);

-- Carry the existing per-table history over, so Trends' per-table charts and
-- Table Health's size column and dead-tuple forecast keep their past data.
-- The old metric_points rows are left in place (nothing reads them any more,
-- and the retention job prunes them within RETENTION_DAYS), so going back to
-- the previous version still finds them.
INSERT INTO table_stat_snapshots (target_id, collected_at, schema_name, table_name, dead_pct, total_bytes)
SELECT
    target_id,
    collected_at,
    labels->>'schema',
    labels->>'table',
    max(value) FILTER (WHERE metric_name = 'table_dead_pct'),
    max(value) FILTER (WHERE metric_name = 'table_size_bytes')::bigint
FROM metric_points
WHERE metric_name IN ('table_dead_pct', 'table_size_bytes')
  AND labels->>'schema' IS NOT NULL
  AND labels->>'table' IS NOT NULL
GROUP BY target_id, collected_at, labels->>'schema', labels->>'table';

-- Retention (app/scheduler.py::run_retention_cycle) now prunes every
-- history table. These indexes keep its deletes, and the "latest run" reads,
-- from scanning whole tables.
CREATE INDEX IF NOT EXISTS metric_points_collected_idx
    ON metric_points (collected_at);
CREATE INDEX IF NOT EXISTS collector_runs_finished_idx
    ON collector_runs (finished_at);
CREATE INDEX IF NOT EXISTS deep_scan_runs_target_category_finished_idx
    ON deep_scan_runs (target_id, category, finished_at DESC);
CREATE INDEX IF NOT EXISTS deep_scan_findings_resolved_idx
    ON deep_scan_findings (resolved_at) WHERE status = 'resolved';
CREATE INDEX IF NOT EXISTS query_stat_snapshots_collected_idx
    ON query_stat_snapshots (collected_at);
