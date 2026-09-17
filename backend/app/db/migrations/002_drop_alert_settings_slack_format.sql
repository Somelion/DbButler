-- Actionable Alerting now detects the destination platform (Slack/Discord/
-- Teams/generic) from the webhook URL itself at send time
-- (app/alerting.py::detect_webhook_kind) instead of a manually-chosen
-- format flag, so this column no longer has a reader or writer.
ALTER TABLE alert_settings DROP COLUMN IF EXISTS slack_format;
