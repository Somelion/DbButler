-- pgdba-store schema
-- Applied to the app's own history/metrics database (never a monitored target).
-- Built out incrementally, one table per epic — see docs/ARCHITECTURE.md for the full
-- intended shape.

CREATE TABLE IF NOT EXISTS targets (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    host                TEXT NOT NULL,
    port                INTEGER NOT NULL DEFAULT 5432,
    dbname              TEXT NOT NULL,
    username            TEXT NOT NULL,
    -- Encrypted at the application layer before insert; never stored in plaintext.
    encrypted_password  TEXT NOT NULL,
    sslmode             TEXT NOT NULL DEFAULT 'prefer',
    detected_pg_version TEXT,
    last_test_ok        BOOLEAN,
    last_test_at        TIMESTAMPTZ,
    last_test_message   TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Optional PgBouncer admin-console connection for a target — a distinct
-- service from the target's own Postgres connection above, reached over
-- the same libpq wire protocol but via PgBouncer's magic "pgbouncer" admin
-- database (SHOW POOLS/SHOW VERSION, not ordinary SQL) — see
-- app/pgbouncer_conn.py, app/pgbouncer_probe.py, routers/pgbouncer.py. One
-- row per target (PK is target_id itself, not its own UUID — a target has
-- at most one PgBouncer connection), only present once a DBA has actually
-- configured pooler visibility; most targets never get a row here at all.
CREATE TABLE IF NOT EXISTS pgbouncer_connections (
    target_id           UUID PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
    host                TEXT NOT NULL,
    port                INTEGER NOT NULL DEFAULT 6432,
    username            TEXT NOT NULL,
    encrypted_password  TEXT NOT NULL,
    sslmode             TEXT NOT NULL DEFAULT 'prefer',
    last_test_ok        BOOLEAN,
    last_test_at        TIMESTAMPTZ,
    last_test_message   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Generic time-series store for collector output. A typed table per collector
-- is worth it once a metric needs its own indexes/columns; until then this
-- covers new metrics without a migration.
CREATE TABLE IF NOT EXISTS metric_points (
    id           BIGSERIAL PRIMARY KEY,
    target_id    UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    metric_name  TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    value        DOUBLE PRECISION NOT NULL,
    labels       JSONB
);

CREATE INDEX IF NOT EXISTS metric_points_target_metric_time_idx
    ON metric_points (target_id, metric_name, collected_at);

-- Operational self-log so a failing collector is visible, not silent.
CREATE TABLE IF NOT EXISTS collector_runs (
    id          BIGSERIAL PRIMARY KEY,
    target_id   UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    job_name    TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL,
    error       TEXT
);

-- Query History's dimension table: the latest known cumulative
-- pg_stat_statements counters per (target, queryid), for the up-to-50
-- heaviest queries each collection cycle sees (scheduler.py::TOP_N_TRACKED_QUERIES)
-- — unlike metric_points' fixed/per-table metrics, distinct queryids aren't
-- naturally bounded, so this caps cardinality by design rather than storing
-- everything. last_calls/last_total_exec_ms are the baseline
-- run_query_history_cycle diffs the next cumulative reading against to
-- compute an interval's delta; a query dropping below its own baseline means
-- pg_stat_statements_reset() ran (maintenance.py's "Reset query stats"
-- button), handled as a skipped interval, not a negative delta.
CREATE TABLE IF NOT EXISTS tracked_queries (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id           UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    queryid             BIGINT NOT NULL,
    query_text          TEXT NOT NULL,
    last_calls          BIGINT NOT NULL,
    last_total_exec_ms  DOUBLE PRECISION NOT NULL,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (target_id, queryid)
);

-- Query History's fact table: one row per tracked query per collection
-- interval it actually ran in (a query with zero new calls that cycle gets
-- no row — a gap in the chart, not a misleading zero).
CREATE TABLE IF NOT EXISTS query_stat_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    tracked_query_id UUID NOT NULL REFERENCES tracked_queries(id) ON DELETE CASCADE,
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    calls            BIGINT NOT NULL,
    mean_exec_ms     DOUBLE PRECISION NOT NULL,
    rows             BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS query_stat_snapshots_query_time_idx
    ON query_stat_snapshots (tracked_query_id, collected_at DESC);

-- Query Plan Regression Detector's fact table: one row per tracked query per
-- cycle whose EXPLAIN plan was successfully captured AND whose structural
-- fingerprint (app/plan_fingerprint.py) actually differs from the previous
-- capture — an unchanged plan writes nothing, so "the latest row's
-- collected_at" is exactly "when this query's plan last changed," not just
-- "when it was last checked." fingerprint_hash is a stable digest over node
-- type/relation/index/join shape only (never costs or row estimates), so a
-- changed hash means the planner chose something structurally different.
-- plan_summary is a short human-readable rendering of that same shape for
-- display in a finding; plan_json is the full fingerprint tree it was built
-- from, kept for a future plan-history viewer. Read by
-- routers/plan_regressions.py, which correlates a fingerprint change here
-- against query_stat_snapshots' latency before/after it, computed fresh on
-- every request rather than persisted (see that router's docstring).
CREATE TABLE IF NOT EXISTS query_plan_snapshots (
    id               BIGSERIAL PRIMARY KEY,
    tracked_query_id UUID NOT NULL REFERENCES tracked_queries(id) ON DELETE CASCADE,
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    fingerprint_hash TEXT NOT NULL,
    plan_summary     TEXT NOT NULL,
    plan_json        JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS query_plan_snapshots_query_time_idx
    ON query_plan_snapshots (tracked_query_id, collected_at DESC);

-- Append-only: each save inserts a new row rather than updating in place, so
-- past profiles stay visible. The most recent row per target is "current."
CREATE TABLE IF NOT EXISTS hardware_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id           UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    -- What psutil saw inside the backend container at save time — a hint,
    -- never trusted as ground truth (see docs/ARCHITECTURE.md's Docker/host
    -- RAM-visibility risk).
    detected_ram_mb     INTEGER,
    detected_cpu_cores  INTEGER,
    confirmed_ram_mb    INTEGER NOT NULL,
    confirmed_cpu_cores INTEGER NOT NULL,
    storage_type        TEXT NOT NULL DEFAULT 'ssd',
    workload_type       TEXT NOT NULL DEFAULT 'mixed',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS hardware_profiles_target_created_idx
    ON hardware_profiles (target_id, created_at DESC);

-- Index Advisor / Schema Lint findings the DBA has archived. Findings
-- themselves are never persisted (recomputed fresh every request — see
-- routers/index_advisor.py, routers/schema_lint.py), so archiving stores a
-- full snapshot here rather than a bare reference: the underlying issue may
-- already be fixed by the time someone looks at the Archive screen, and the
-- snapshot is what lets that history stay readable. A row's continued
-- presence is also what keeps the same finding_id out of future live
-- results (see routers/advisor_archive.py::get_archived_finding_ids) — it
-- reappears automatically once restored, if the underlying issue is still
-- there.
CREATE TABLE IF NOT EXISTS archived_findings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id   UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    finding_id  TEXT NOT NULL,
    category    TEXT NOT NULL,
    finding     JSONB NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (target_id, finding_id)
);

CREATE INDEX IF NOT EXISTS archived_findings_target_archived_idx
    ON archived_findings (target_id, archived_at DESC);

-- Indexes created directly from the Index Advisor's "Add to database" button
-- (routers/index_advisor.py's /apply endpoint), so the Index Testing screen
-- can show whether each one has actually earned its keep since it was added.
-- baseline_seq_scan is the target table's pg_stat_user_tables.seq_scan count
-- captured at creation time — a newly created index's own idx_scan already
-- starts at 0, so no baseline is needed for that side, only for the table's
-- seq_scan (to compute "sequential scans on this table since adding this
-- index" as a delta rather than a raw, meaningless-on-its-own cumulative
-- counter). dropped_at is set (soft-delete, not a row delete) whether the
-- drop happened via this screen's own button or was reconciled after
-- noticing the index gone during a routine read.
CREATE TABLE IF NOT EXISTS tracked_indexes (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id          UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    schema_name        TEXT NOT NULL,
    table_name         TEXT NOT NULL,
    index_name         TEXT NOT NULL,
    ddl                TEXT NOT NULL,
    source_finding_id  TEXT,
    baseline_seq_scan  BIGINT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    dropped_at         TIMESTAMPTZ,
    UNIQUE (target_id, schema_name, index_name)
);

CREATE INDEX IF NOT EXISTS tracked_indexes_target_created_idx
    ON tracked_indexes (target_id, created_at DESC);

-- Scheduler cadence, DB-backed so it can be edited from the Settings screen
-- and hot-swapped into the live APScheduler instance without a restart (see
-- scheduler.py::reconcile). One row per job name, global rather than
-- per-target — this app's product surface is single-target even though the
-- scheduler loops over every active target (docs/DATA_MODEL.md).
CREATE TABLE IF NOT EXISTS scheduler_jobs (
    job_name         TEXT PRIMARY KEY,
    job_kind         TEXT NOT NULL,             -- 'interval' | 'cron'
    interval_seconds INTEGER,                   -- set when job_kind = 'interval'
    cron_expr        TEXT,                      -- set when job_kind = 'cron'
    enabled          BOOLEAN NOT NULL DEFAULT true,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO scheduler_jobs (job_name, job_kind, interval_seconds) VALUES
    ('collection_cycle', 'interval', 30),
    ('table_metrics_cycle', 'interval', 300),
    ('query_history_cycle', 'interval', 300),
    ('plan_regression_cycle', 'interval', 300),
    ('live_findings_cycle', 'interval', 60),
    ('retention_cycle', 'interval', 3600)
    ON CONFLICT (job_name) DO NOTHING;

INSERT INTO scheduler_jobs (job_name, job_kind, cron_expr) VALUES
    ('deep_scan', 'cron', '0 2 * * *')
    ON CONFLICT (job_name) DO NOTHING;

-- Persisted Index Advisor / Schema Lint / Configuration Advisor / Table
-- Health findings, written only by the nightly deep scan
-- (scheduler.py::run_deep_scan_cycle), never by the on-demand screens
-- themselves (those still recompute fresh per request, unchanged). Deduped
-- by (target_id, finding_id) rather than an events-per-scan table: nothing
-- downstream needs per-scan replay yet, just "is this still open, and since
-- when."
CREATE TABLE IF NOT EXISTS deep_scan_findings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id     UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    finding_id    TEXT NOT NULL,
    category      TEXT NOT NULL,                -- 'index advisor' | 'schema lint' | 'config advisor'
    finding       JSONB NOT NULL,                -- latest IndexFinding.model_dump() snapshot
    status        TEXT NOT NULL DEFAULT 'open',  -- 'open' | 'resolved'
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at   TIMESTAMPTZ,
    UNIQUE (target_id, finding_id)
);

CREATE INDEX IF NOT EXISTS deep_scan_findings_target_status_idx
    ON deep_scan_findings (target_id, status);

-- Operational self-log for the deep scan, one row per target per category
-- per cycle — mirrors collector_runs above. A category's status here gates
-- whether run_deep_scan_cycle is allowed to auto-resolve that category's
-- stale findings (an error must never silently read as "all clear").
CREATE TABLE IF NOT EXISTS deep_scan_runs (
    id          BIGSERIAL PRIMARY KEY,
    target_id   UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status      TEXT NOT NULL,
    error       TEXT
);

-- Singleton row (id always 1) holding the AI provider PostgreDba's AI Query
-- Analysis feature calls. api_key is Fernet-encrypted the same way
-- targets.encrypted_password is (app/crypto.py) and never returned decrypted
-- by any endpoint. endpoint_url is only meaningful for provider='local' (a
-- self-hosted OpenAI-compatible chat/completions URL, e.g. Ollama); it's
-- ignored for 'gemini', which calls a fixed Google endpoint.
--
-- model_name's default is only what a *fresh* pgdba-store gets on first
-- init (docker-entrypoint-initdb.d only runs against an empty data
-- directory) — it does not retroactively update an already-provisioned
-- row. Google retires Gemini model names fairly often (gemini-1.5-flash,
-- this table's original default, was retired and now 404s); an existing
-- install with a stale model name needs the Settings screen's Model name
-- field updated directly, not a schema change.
CREATE TABLE IF NOT EXISTS ai_settings (
    id                SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    provider          TEXT NOT NULL DEFAULT 'gemini',
    encrypted_api_key TEXT,
    endpoint_url      TEXT,
    model_name        TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO ai_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Singleton row (id always 1) for Actionable Alerting (app/alerting.py) —
-- a single outbound webhook fired when a finding newly opens (never seen
-- before, or reopened after being resolved — scheduler.py::_reconcile_
-- category_findings) at or above min_severity. encrypted_webhook_url is
-- Fernet-encrypted the same way targets.encrypted_password/ai_settings.
-- encrypted_api_key are: a webhook URL is a bearer credential in practice
-- (anyone holding a Slack incoming-webhook URL can post as that
-- integration). slack_format=true sends Slack's expected {"text": ...}
-- shape (works for Slack/Mattermost-compatible receivers); false sends a
-- generic JSON payload with the full finding objects instead, for a
-- webhook receiver that parses its own format. enabled defaults false —
-- alerting is opt-in, same as AI Query Analysis needing a provider
-- configured before it does anything.
CREATE TABLE IF NOT EXISTS alert_settings (
    id                    SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled               BOOLEAN NOT NULL DEFAULT false,
    encrypted_webhook_url TEXT,
    slack_format          BOOLEAN NOT NULL DEFAULT true,
    min_severity          TEXT NOT NULL DEFAULT 'critical',
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO alert_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Singleton row (id always 1) for the Dashboard's "Customize" panel
-- (routers/dashboard_settings.py) — which tiles/widgets a DBA has hidden
-- from the at-a-glance view. Global rather than per-target, same reasoning
-- as scheduler_jobs/alert_settings above. Storing the hidden set (rather
-- than an enabled set) means a newly added tile shows up by default for
-- every existing install without a data migration — absence here just
-- means "not hidden."
CREATE TABLE IF NOT EXISTS dashboard_settings (
    id                SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    hidden_categories TEXT[] NOT NULL DEFAULT '{}',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO dashboard_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- One row per AI query analysis request, from Query Intelligence's "Analyze
-- with AI" button. anonymize/anonymized_query/anonymization_map are only
-- populated when the user opted to anonymize identifiers before sending the
-- query+plan to an external AI provider — see app/query_anonymizer.py.
-- Runs as a FastAPI background task (not the scheduler — this is a one-off
-- per user click, not a recurring job), so status starts 'pending' and the
-- UI polls until it flips to 'done'/'error'.
CREATE TABLE IF NOT EXISTS query_analyses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id           UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    original_query      TEXT NOT NULL,
    anonymize           BOOLEAN NOT NULL DEFAULT true,
    anonymized_query    TEXT,
    anonymization_map   JSONB,
    explain_plan        TEXT,
    prompt              TEXT,
    provider            TEXT,
    model_name          TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    ai_response         TEXT,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS query_analyses_target_created_idx
    ON query_analyses (target_id, created_at DESC);

-- One row per AI covering-index analysis request, from Index Advisor's
-- Covering Index Candidates "Analyze with AI" button
-- (routers/index_coverage_analysis.py) — same pending/done/error background-
-- task shape as query_analyses above. recommended_ddl is only set when the
-- AI's reply parses to a clear INCLUDE(...) verdict; the AI is never asked
-- to write DDL itself, only to judge which columns to move — see
-- app/index_analysis.py::build_covering_index_ddl, which both this and the
-- synchronous heuristic check call to generate DDL identically either way.
CREATE TABLE IF NOT EXISTS index_coverage_analyses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id       UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    finding_id      TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    index_name      TEXT NOT NULL,
    prompt          TEXT,
    provider        TEXT,
    model_name      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    ai_response     TEXT,
    recommended_ddl TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS index_coverage_analyses_target_created_idx
    ON index_coverage_analyses (target_id, created_at DESC);

-- Migration tracking (app/db/migrate.py). This file is frozen as of the
-- migration system's introduction — every schema change from here on is a
-- new numbered file under app/db/migrations/, never an edit above this
-- line. The row below marks a fresh install (this file only ever runs via
-- docker-entrypoint-initdb.d, on a brand-new empty volume) as already
-- having this baseline, so the runner only ever applies migrations numbered
-- after it. An existing volume from before this table existed gets the same
-- row inserted by the runner itself on first startup instead.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version, filename) VALUES (1, 'schema.sql (baseline)')
ON CONFLICT (version) DO NOTHING;
