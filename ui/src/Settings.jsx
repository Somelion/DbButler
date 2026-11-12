import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { formatRelativeTime } from "./format";
import { Alert, Badge, Card, Checkbox, CopyButton, Field, Skeleton } from "./components";
import styles from "./Settings.module.css";

const SCHEDULER_JOBS_KEY = ["scheduler-jobs"];
const AI_SETTINGS_KEY = ["settings", "ai"];
const ALERT_SETTINGS_KEY = ["settings", "alerts"];

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
  weekly_report: {
    label: "Weekly report",
    description: "Builds each database's weekly health report (Monitor → Weekly Reports) and, if switched on below, posts its summary to the webhook.",
  },
};

export default function Settings() {
  return (
    <div className={styles.screen}>
      <SchedulerSettings />
      <AiProviderSettings />
      <AlertSettings />
      <IntegrationSettings />
    </div>
  );
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return window.location.host;
  }
}

// How to connect an AI agent (MCP) and Prometheus. The tokens live in the
// server's .env and are never shown here — only whether each one is set.
function IntegrationSettings() {
  const { data, error, isPending } = useQuery({
    queryKey: ["settings", "integrations"],
    queryFn: ({ signal }) => api.getIntegrations({ signal }),
  });
  const base = data?.public_url ?? window.location.origin;
  const mcpCommand = `claude mcp add --transport http dbbutler ${base}/api/mcp --header "Authorization: Bearer $DBBUTLER_MCP_TOKEN"`;
  const scrapeConfig = [
    "scrape_configs:",
    "  - job_name: dbbutler",
    "    metrics_path: /api/metrics",
    ...(base.startsWith("https:") ? ["    scheme: https"] : []),
    "    authorization:",
    "      credentials_file: /etc/prometheus/dbbutler_metrics_token",
    "    static_configs:",
    `      - targets: ["${hostOf(base)}"]`,
  ].join("\n");

  return (
    <Card
      title="Integrations"
      description="Read-only access for machines, each with its own token set in the server's .env (APP_MCP_TOKEN, APP_METRICS_TOKEN) — neither can use the rest of the API."
    >
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label="Loading integrations…" />}
      {data && (
        <div className={styles.integrations}>
          <section aria-labelledby="mcp-heading">
            <h3 id="mcp-heading" className={styles.integrationTitle}>
              AI agents (MCP) <Badge tone={data.mcp_enabled ? "healthy" : "neutral"}>{data.mcp_enabled ? "on" : "off"}</Badge>
            </h3>
            <p className={styles.integrationText}>
              Lets Claude Code, Cursor or any MCP client ask &quot;what&apos;s wrong with this database?&quot; through{" "}
              {data.mcp_tools.length} preset read-only tools. No tool runs SQL it&apos;s given, and query text goes out
              with literal values masked.
              {!data.mcp_enabled && " Set APP_MCP_TOKEN in .env and restart the backend to switch it on."}
            </p>
            <div className={styles.snippet}>
              <code>{mcpCommand}</code>
              <CopyButton text={mcpCommand} />
            </div>
            <p className={styles.integrationText}>Tools: {data.mcp_tools.join(", ")}.</p>
          </section>
          <section aria-labelledby="prometheus-heading">
            <h3 id="prometheus-heading" className={styles.integrationTitle}>
              Prometheus{" "}
              <Badge tone={data.metrics_enabled ? "healthy" : "neutral"}>{data.metrics_enabled ? "on" : "off"}</Badge>
            </h3>
            <p className={styles.integrationText}>
              Collected metrics, open-finding counts and lock waits per database, for Grafana and Alertmanager. Also
              served at /metrics on the backend&apos;s own port.
              {!data.metrics_enabled && " Set APP_METRICS_TOKEN in .env and restart the backend to switch it on."}
            </p>
            <div className={styles.snippet}>
              <pre>{scrapeConfig}</pre>
              <CopyButton text={scrapeConfig} />
            </div>
          </section>
          {!data.public_url && (
            <Alert tone="info" compact>
              Set APP_PUBLIC_URL in .env to the address people open DbButler at: the weekly report&apos;s webhook
              message then links to the report, and the snippets above use it.
            </Alert>
          )}
        </div>
      )}
    </Card>
  );
}

function SchedulerSettings() {
  const queryClient = useQueryClient();
  const [saveError, setSaveError] = useState(null);

  const { data, error, isPending } = useQuery({
    queryKey: SCHEDULER_JOBS_KEY,
    queryFn: ({ signal }) => api.getSchedulerJobs({ signal }),
  });

  const handleUpdated = (updated) => {
    queryClient.setQueryData(SCHEDULER_JOBS_KEY, (prev) =>
      prev ? { ...prev, jobs: prev.jobs.map((job) => (job.job_name === updated.job_name ? updated : job)) } : prev
    );
  };

  const shownError = saveError ?? error?.message;

  return (
    <Card
      title="Scheduler"
      description="How often each background job runs. Saving takes effect immediately — no backend restart needed."
    >
      {shownError && (
        <Alert tone="critical" compact>
          {shownError}
        </Alert>
      )}

      {isPending && <Skeleton lines={4} label="Loading scheduler jobs…" />}

      {data && (
        <div className={styles.jobList}>
          {data.jobs.map((job) => (
            <JobRow key={job.job_name} job={job} onUpdated={handleUpdated} setError={setSaveError} />
          ))}
        </div>
      )}
    </Card>
  );
}

function JobRow({ job, onUpdated, setError }) {
  const meta = JOB_META[job.job_name] ?? { label: job.job_name, description: "" };
  const [intervalSeconds, setIntervalSeconds] = useState(job.interval_seconds ?? 60);
  const [cronExpr, setCronExpr] = useState(job.cron_expr ?? "");
  const [enabled, setEnabled] = useState(job.enabled);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    setSaving(true);
    setJustSaved(false);
    setError(null);
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

  return (
    <form onSubmit={handleSubmit} className={styles.jobRow}>
      <div className={styles.jobInfo}>
        <h3 className={styles.jobTitle}>{meta.label}</h3>
        <span className={styles.jobDescription}>{meta.description}</span>
        <span className={styles.jobMeta}>
          last updated {formatRelativeTime(job.updated_at)}
          <span role="status">{justSaved && " · saved"}</span>
        </span>
      </div>

      <div className={styles.jobControls}>
        <Checkbox checked={enabled} onChange={(e) => setEnabled(e.target.checked)}>
          Enabled
        </Checkbox>

        {job.job_kind === "interval" ? (
          <Field label="Interval (seconds)" className={styles.intervalField}>
            <input
              type="number"
              min="1"
              value={intervalSeconds}
              onChange={(e) => setIntervalSeconds(e.target.value)}
            />
          </Field>
        ) : (
          <Field label="Schedule (cron)" className={styles.cronField}>
            <input
              type="text"
              value={cronExpr}
              onChange={(e) => setCronExpr(e.target.value)}
              placeholder="0 2 * * *"
              autoComplete="off"
            />
          </Field>
        )}

        <button type="submit" className="button-primary button-sm" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function AiProviderSettings() {
  const { data: settings, error, isPending } = useQuery({
    queryKey: AI_SETTINGS_KEY,
    queryFn: ({ signal }) => api.getAiSettings({ signal }),
  });

  return (
    <Card
      title="AI Query Analysis"
      description={
        <>
          Configures the AI provider used by Query Intelligence's "Analyze with AI" button. Your query text and its
          EXPLAIN plan are sent to this provider — use the Anonymize option in the analysis window if that's a concern
          for a cloud provider.
        </>
      }
    >
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label="Loading AI settings…" />}
      {/* The form seeds its fields from the loaded settings once, on mount. */}
      {settings && <AiProviderForm settings={settings} />}
    </Card>
  );
}

function AiProviderForm({ settings }) {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState(settings.provider);
  const [apiKey, setApiKey] = useState("");
  const [endpointUrl, setEndpointUrl] = useState(settings.endpoint_url ?? "");
  const [modelName, setModelName] = useState(settings.model_name);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
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
        queryClient.setQueryData(AI_SETTINGS_KEY, updated);
        setApiKey("");
        setJustSaved(true);
      })
      .catch((err) => setError(err.message))
      .finally(() => setSaving(false));
  };

  return (
    <form onSubmit={handleSubmit} className={styles.form}>
      {error && (
        <Alert tone="critical" compact>
          {error}
        </Alert>
      )}

      <Field label="Provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="gemini">Google Gemini (cloud, free tier)</option>
          <option value="local">Local model (OpenAI-compatible endpoint, e.g. Ollama)</option>
        </select>
      </Field>

      {provider === "local" && (
        <Field label="Endpoint URL">
          <input
            type="text"
            value={endpointUrl}
            onChange={(e) => setEndpointUrl(e.target.value)}
            placeholder="http://localhost:11434/v1/chat/completions"
            autoComplete="off"
          />
        </Field>
      )}

      <Field
        label="Model name"
        hint={
          provider === "gemini" ? (
            <>
              Google retires model names fairly often — if analysis starts failing with a "model not found" error,
              check{" "}
              <a href="https://ai.google.dev/gemini-api/docs/models" target="_blank" rel="noreferrer">
                the current model list
              </a>{" "}
              and update this field.
            </>
          ) : undefined
        }
      >
        <input
          type="text"
          value={modelName}
          onChange={(e) => setModelName(e.target.value)}
          placeholder={provider === "gemini" ? "gemini-2.5-flash" : "llama3"}
          autoComplete="off"
        />
      </Field>

      <Field label={provider === "gemini" ? "API key" : "API key (optional)"}>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={settings.has_api_key ? "•••• already set — leave blank to keep it" : "Paste your API key"}
          autoComplete="off"
        />
      </Field>

      <div className={styles.actions}>
        <button type="submit" className="button-primary button-sm" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {justSaved && (
        <Alert tone="healthy" compact>
          Saved.
        </Alert>
      )}
    </form>
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
  const { data: settings, error, isPending } = useQuery({
    queryKey: ALERT_SETTINGS_KEY,
    queryFn: ({ signal }) => api.getAlertSettings({ signal }),
  });

  return (
    <Card
      title="Actionable Alerting"
      description={
        <>
          Sends a webhook the moment a finding first opens at or above the chosen severity — driven by the "Live
          findings history" job above, so a problem is reported once when it starts, not every cycle it stays open.
          Paste a Slack, Discord, or Microsoft Teams webhook URL and the right payload format is detected
          automatically — no separate setting to pick.
        </>
      }
    >
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label="Loading alert settings…" />}
      {/* The form seeds its fields from the loaded settings once, on mount. */}
      {settings && <AlertForm settings={settings} />}
    </Card>
  );
}

function AlertForm({ settings }) {
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(settings.enabled);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [minSeverity, setMinSeverity] = useState(settings.min_severity);
  const [reportWebhook, setReportWebhook] = useState(settings.report_webhook_enabled ?? false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  // Whatever's freshly typed takes priority over the previously-saved kind —
  // the whole point is showing what the URL currently in the box will
  // detect as, not what's still stored from before.
  const detectedKind = webhookUrl ? detectWebhookKind(webhookUrl) : settings.webhook_kind;

  const handleSubmit = (e) => {
    e.preventDefault();
    setSaving(true);
    setJustSaved(false);
    setError(null);
    api
      .updateAlertSettings({
        enabled,
        webhook_url: webhookUrl === "" ? null : webhookUrl,
        min_severity: minSeverity,
        report_webhook_enabled: reportWebhook,
      })
      .then((updated) => {
        queryClient.setQueryData(ALERT_SETTINGS_KEY, updated);
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

  return (
    <form onSubmit={handleSubmit} className={styles.form}>
      {error && (
        <Alert tone="critical" compact>
          {error}
        </Alert>
      )}

      <Checkbox checked={enabled} onChange={(e) => setEnabled(e.target.checked)}>
        Enabled
      </Checkbox>

      <Field
        label="Webhook URL"
        hint={
          detectedKind ? (
            <>
              Detected: <strong className={styles.detected}>{WEBHOOK_KIND_LABEL[detectedKind]}</strong>
            </>
          ) : undefined
        }
      >
        <input
          type="password"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
          placeholder={
            settings.has_webhook_url ? "•••• already set — leave blank to keep it" : "https://hooks.slack.com/services/..."
          }
          autoComplete="off"
        />
      </Field>

      <Field label="Minimum severity">
        <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)}>
          <option value="critical">Critical only</option>
          <option value="attention">Attention and critical</option>
        </select>
      </Field>

      <Checkbox checked={reportWebhook} onChange={(e) => setReportWebhook(e.target.checked)}>
        Also post each weekly report&apos;s summary to this webhook (independent of the Enabled switch above)
      </Checkbox>

      <div className={styles.actions}>
        <button type="submit" className="button-primary button-sm" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" className="button-secondary button-sm" onClick={handleTest} disabled={testing}>
          {testing ? "Sending…" : "Send test alert"}
        </button>
      </div>

      {justSaved && (
        <Alert tone="healthy" compact>
          Saved.
        </Alert>
      )}

      {testResult && (
        <Alert tone={testResult.ok ? "healthy" : "critical"} compact>
          {testResult.ok ? "Test alert sent — check your webhook's destination." : testResult.message}
        </Alert>
      )}
    </form>
  );
}
