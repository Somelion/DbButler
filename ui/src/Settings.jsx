import { useEffect, useState } from "react";
import { api } from "./api";
import { formatRelativeTime } from "./format";

const JOB_META = {
  collection_cycle: {
    label: "Fast metrics",
    description: "Cache hit rate, connection count, worst table bloat, database size.",
  },
  table_metrics_cycle: {
    label: "Per-table metrics",
    description: "Per-table dead-tuple % and size, behind Trends' per-table picker.",
  },
  plan_regression_cycle: {
    label: "Plan regression capture",
    description: "Captures each tracked query's EXPLAIN plan shape, behind the Plan Regressions screen.",
  },
  live_findings_cycle: {
    label: "Live findings history",
    description:
      "Persists Dashboard/Diagnose Now's live findings with first-seen tracking, and drives Actionable Alerting below.",
  },
  retention_cycle: {
    label: "Metric retention",
    description: "Prunes metric samples older than 30 days.",
  },
  deep_scan: {
    label: "Deep scan",
    description:
      "Runs Index Advisor, Schema Lint, and Configuration Advisor against every active target and " +
      "records what's still open, since when.",
  },
};

export default function Settings() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <SchedulerSettings />
      <AiProviderSettings />
      <AlertSettings />
    </div>
  );
}

function SchedulerSettings() {
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    api
      .getSchedulerJobs()
      .then((data) => setJobs(data.jobs))
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    setError(null);
    load();
  }, []);

  const handleUpdated = (updated) => {
    setJobs((prev) => prev.map((job) => (job.job_name === updated.job_name ? updated : job)));
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          Scheduler
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          How often each background job runs. Saving takes effect immediately — no backend restart needed.
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {jobs && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {jobs.map((job) => (
            <JobRow key={job.job_name} job={job} onUpdated={handleUpdated} setError={setError} />
          ))}
        </div>
      )}
    </div>
  );
}

function JobRow({ job, onUpdated, setError }) {
  const meta = JOB_META[job.job_name] ?? { label: job.job_name, description: "" };
  const [intervalSeconds, setIntervalSeconds] = useState(job.interval_seconds ?? 60);
  const [cronExpr, setCronExpr] = useState(job.cron_expr ?? "");
  const [enabled, setEnabled] = useState(job.enabled);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  const handleSave = () => {
    setSaving(true);
    setJustSaved(false);
    api
      .updateSchedulerJob(job.job_name, {
        job_kind: job.job_kind,
        interval_seconds: job.job_kind === "interval" ? Number(intervalSeconds) : null,
        cron_expr: job.job_kind === "cron" ? cronExpr : null,
        enabled,
      })
      .then((updated) => {
        onUpdated(updated);
        setJustSaved(true);
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  };

  const inputStyle = {
    padding: "5px 8px",
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg)",
    color: "var(--text)",
    fontSize: 12,
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        padding: "14px 16px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 13.5, color: "var(--text)" }}>
          {meta.label}
        </span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{meta.description}</span>
        <span style={{ fontSize: 10.5, color: "var(--text-muted)" }}>
          last updated {formatRelativeTime(job.updated_at)}
          {justSaved && " · saved"}
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>

        {job.job_kind === "interval" ? (
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-muted)" }}>
            <input
              type="number"
              min="1"
              value={intervalSeconds}
              onChange={(e) => setIntervalSeconds(e.target.value)}
              style={{ ...inputStyle, width: 80 }}
            />
            seconds
          </label>
        ) : (
          <input
            type="text"
            value={cronExpr}
            onChange={(e) => setCronExpr(e.target.value)}
            placeholder="0 2 * * *"
            style={{ ...inputStyle, width: 140, fontFamily: "IBM Plex Mono, monospace" }}
          />
        )}

        <button className="button-primary" onClick={handleSave} disabled={saving} style={{ padding: "5px 12px", fontSize: 12 }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function AiProviderSettings() {
  const [settings, setSettings] = useState(null);
  const [provider, setProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    api
      .getAiSettings()
      .then((data) => {
        setSettings(data);
        setProvider(data.provider);
        setEndpointUrl(data.endpoint_url ?? "");
        setModelName(data.model_name);
      })
      .catch((err) => setError(err.message));
  }, []);

  const handleSave = () => {
    setSaving(true);
    setJustSaved(false);
    setError(null);
    api
      .updateAiSettings({
        provider,
        api_key: apiKey === "" ? null : apiKey,
        endpoint_url: provider === "local" ? endpointUrl : null,
        model_name: modelName,
      })
      .then((updated) => {
        setSettings(updated);
        setApiKey("");
        setJustSaved(true);
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  };

  const inputStyle = {
    padding: "6px 9px",
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg)",
    color: "var(--text)",
    fontSize: 12.5,
    width: "100%",
    boxSizing: "border-box",
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          AI Query Analysis
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Configures the AI provider used by Query Intelligence's "Analyze with AI" button. Your query text and
          its EXPLAIN plan are sent to this provider — use the Anonymize option in the analysis window if that's
          a concern for a cloud provider.
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {settings && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 420 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
            Provider
            <select value={provider} onChange={(e) => setProvider(e.target.value)} style={inputStyle}>
              <option value="gemini">Google Gemini (cloud, free tier)</option>
              <option value="local">Local model (OpenAI-compatible endpoint, e.g. Ollama)</option>
            </select>
          </label>

          {provider === "local" && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
              Endpoint URL
              <input
                type="text"
                value={endpointUrl}
                onChange={(e) => setEndpointUrl(e.target.value)}
                placeholder="http://localhost:11434/v1/chat/completions"
                style={{ ...inputStyle, fontFamily: "IBM Plex Mono, monospace" }}
              />
            </label>
          )}

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
            Model name
            <input
              type="text"
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder={provider === "gemini" ? "gemini-2.5-flash" : "llama3"}
              style={inputStyle}
            />
            {provider === "gemini" && (
              <span style={{ fontSize: 10.5 }}>
                Google retires model names fairly often — if analysis starts failing with a "model not found"
                error, check{" "}
                <a
                  href="https://ai.google.dev/gemini-api/docs/models"
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "var(--accent)" }}
                >
                  the current model list
                </a>{" "}
                and update this field.
              </span>
            )}
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
            {provider === "gemini" ? "API key" : "API key (optional)"}
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={settings.has_api_key ? "•••• already set — leave blank to keep it" : "Paste your API key"}
              style={inputStyle}
            />
          </label>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="button-primary" onClick={handleSave} disabled={saving} style={{ padding: "5px 14px", fontSize: 12 }}>
              {saving ? "Saving…" : "Save"}
            </button>
            {justSaved && <span style={{ fontSize: 11.5, color: "var(--healthy-text)" }}>Saved.</span>}
          </div>
        </div>
      )}
    </div>
  );
}

// Mirrors app/alerting.py::detect_webhook_kind — kept in sync by hand since
// there's no shared code between the two ecosystems here. Used only for the
// live "Detected: X" label as the user types; the backend's own detection
// (run fresh from the stored URL) is what actually decides delivery format.
const WEBHOOK_KIND_PATTERNS = [
  { kind: "slack", pattern: /hooks\.slack\.com/i },
  { kind: "discord", pattern: /discord(?:app)?\.com\/api\/webhooks/i },
  { kind: "teams", pattern: /webhook\.office\.com|logic\.azure\.com|powerplatform\.com|powerautomate\.com|flow\.microsoft\.com/i },
];

function detectWebhookKind(url) {
  if (!url) return null;
  const match = WEBHOOK_KIND_PATTERNS.find(({ pattern }) => pattern.test(url));
  return match ? match.kind : "generic";
}

const WEBHOOK_KIND_LABEL = {
  slack: "Slack",
  discord: "Discord",
  teams: "Microsoft Teams",
  generic: "Generic JSON (Mattermost and others compatible with Slack's format still work)",
};

function AlertSettings() {
  const [settings, setSettings] = useState(null);
  const [enabled, setEnabled] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [minSeverity, setMinSeverity] = useState("critical");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  useEffect(() => {
    api
      .getAlertSettings()
      .then((data) => {
        setSettings(data);
        setEnabled(data.enabled);
        setMinSeverity(data.min_severity);
      })
      .catch((err) => setError(err.message));
  }, []);

  // Whatever's freshly typed takes priority over the previously-saved kind —
  // the whole point is showing what the URL currently in the box will
  // detect as, not what's still stored from before.
  const detectedKind = webhookUrl ? detectWebhookKind(webhookUrl) : settings?.webhook_kind;

  const handleSave = () => {
    setSaving(true);
    setJustSaved(false);
    setError(null);
    api
      .updateAlertSettings({
        enabled,
        webhook_url: webhookUrl === "" ? null : webhookUrl,
        min_severity: minSeverity,
      })
      .then((updated) => {
        setSettings(updated);
        setWebhookUrl("");
        setJustSaved(true);
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  };

  const handleTest = () => {
    setTesting(true);
    setTestResult(null);
    setError(null);
    api
      .testAlertWebhook()
      .then(setTestResult)
      .catch((err) => setError(err.message))
      .finally(() => setTesting(false));
  };

  const inputStyle = {
    padding: "6px 9px",
    borderRadius: 6,
    border: "1px solid var(--border)",
    background: "var(--bg)",
    color: "var(--text)",
    fontSize: 12.5,
    width: "100%",
    boxSizing: "border-box",
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          Actionable Alerting
        </span>
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Sends a webhook the moment a finding first opens at or above the chosen severity — driven by the "Live
          findings history" job above, so a problem is reported once when it starts, not every cycle it stays open.
          Paste a Slack, Discord, or Microsoft Teams webhook URL and the right payload format is detected
          automatically — no separate setting to pick.
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {settings && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 420 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--text)" }}>
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
            Webhook URL
            <input
              type="password"
              value={webhookUrl}
              onChange={(e) => setWebhookUrl(e.target.value)}
              placeholder={settings.has_webhook_url ? "•••• already set — leave blank to keep it" : "https://hooks.slack.com/services/..."}
              style={{ ...inputStyle, fontFamily: "IBM Plex Mono, monospace" }}
            />
          </label>

          {detectedKind && (
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              Detected: <strong style={{ color: "var(--text)" }}>{WEBHOOK_KIND_LABEL[detectedKind]}</strong>
            </span>
          )}

          <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-muted)" }}>
            Minimum severity
            <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)} style={inputStyle}>
              <option value="critical">Critical only</option>
              <option value="attention">Attention and critical</option>
            </select>
          </label>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="button-primary" onClick={handleSave} disabled={saving} style={{ padding: "5px 14px", fontSize: 12 }}>
              {saving ? "Saving…" : "Save"}
            </button>
            <button className="button-secondary" onClick={handleTest} disabled={testing} style={{ padding: "5px 14px", fontSize: 12 }}>
              {testing ? "Sending…" : "Send test alert"}
            </button>
            {justSaved && <span style={{ fontSize: 11.5, color: "var(--healthy-text)" }}>Saved.</span>}
          </div>

          {testResult && (
            <span style={{ fontSize: 11.5, color: testResult.ok ? "var(--healthy-text)" : "var(--critical-text)" }}>
              {testResult.ok ? "Test alert sent — check your webhook's destination." : testResult.message}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
