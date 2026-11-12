-- Ecosystem (Sprint 8): the weekly health report. Additive only — one new
-- table, one new column with a default and one new scheduler job; the
-- previous backend ignores all three.

-- One row per generated report. `data` is the report's content as it was
-- at generation time (counts, metric ranges, top queries with literals
-- masked, lock incidents, ...); the HTML is rendered from it on request
-- (app/health_report.py), so a report reads the same later even after the
-- underlying history has been pruned.
CREATE TABLE IF NOT EXISTS health_reports (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id        UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    period_start     TIMESTAMPTZ NOT NULL,
    period_end       TIMESTAMPTZ NOT NULL,
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger          TEXT NOT NULL DEFAULT 'scheduled',  -- 'scheduled' | 'manual'
    overall_severity TEXT NOT NULL,
    data             JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS health_reports_target_generated_idx
    ON health_reports (target_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS health_reports_generated_idx
    ON health_reports (generated_at);

-- Whether the weekly job also posts a short summary to the alert webhook.
-- Off by default: an existing install's channel gets nothing new until
-- someone switches it on in Settings.
ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS report_webhook_enabled BOOLEAN NOT NULL DEFAULT false;

-- Mondays 07:00 (the backend container's clock, UTC by default).
INSERT INTO scheduler_jobs (job_name, job_kind, cron_expr) VALUES
    ('weekly_report', 'cron', '0 7 * * 1')
    ON CONFLICT (job_name) DO NOTHING;
