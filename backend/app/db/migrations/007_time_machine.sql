-- Incident Time Machine (Sprint 7): the history a post-mortem needs that
-- wasn't kept before. Additive only — three new tables and two new
-- scheduler jobs; the previous backend ignores all of them.

-- Who blocked whom, captured every 15s by lock_snapshot_cycle
-- (scheduler.py::_capture_lock_snapshot) — but only while some session is
-- actually waiting on a lock, so a healthy database writes nothing.
-- sessions: the blocked sessions and their blockers, each
--   {pid, blocked_by[], usename, application_name, backend_type, state,
--    wait_event_type, wait_event, waiting_on, query, query_seconds,
--    xact_seconds, state_seconds}
-- with literal values in `query` masked (app/query_anonymizer.py's
-- LiteralMasker) — this history is kept for weeks, the live Activity view
-- isn't. blocked_by can contain 0: a prepared transaction holds the lock.
CREATE TABLE IF NOT EXISTS lock_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    target_id        UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    blocked_count    INTEGER NOT NULL,
    max_wait_seconds BIGINT,
    sessions         JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS lock_snapshots_target_time_idx
    ON lock_snapshots (target_id, collected_at);

-- Things that happened to a target, for the timeline's markers
-- (scheduler.py::_capture_target_events, events_cycle):
--   kind 'restart'       occurred_at = pg_postmaster_start_time()
--   kind 'stats_reset'   occurred_at = pg_stat_database.stats_reset
--   kind 'config_change' occurred_at = when the change was noticed;
--                        detail = {"changes": [{name, old, new}]}
-- The unique key makes re-reading the same start/reset time a no-op.
CREATE TABLE IF NOT EXISTS target_events (
    id          BIGSERIAL PRIMARY KEY,
    target_id   UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (target_id, kind, occurred_at)
);

CREATE INDEX IF NOT EXISTS target_events_target_time_idx
    ON target_events (target_id, occurred_at);

-- The last pg_settings values seen per target (settings changed from their
-- defaults by configuration, not by a session), so the next read can say
-- what changed. One row per target, overwritten.
CREATE TABLE IF NOT EXISTS settings_snapshots (
    target_id   UUID PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    settings    JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO scheduler_jobs (job_name, job_kind, interval_seconds) VALUES
    ('lock_snapshot_cycle', 'interval', 15),
    ('events_cycle', 'interval', 60)
    ON CONFLICT (job_name) DO NOTHING;
