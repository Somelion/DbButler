import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { formatRelativeTime } from "./format";
import { Alert, Badge, Card, Checkbox, CopyButton, Field, Skeleton } from "./components";
import styles from "./Settings.module.css";

const SCHEDULER_JOBS_KEY = ["scheduler-jobs"];
const AI_SETTINGS_KEY = ["settings", "ai"];
const ALERT_SETTINGS_KEY = ["settings", "alerts"];

// Jobs with a label and description under settings:jobs.<job_name>;
// translated at render so a language switch applies.
const KNOWN_JOBS = new Set([
  "collection_cycle",
  "table_metrics_cycle",
  "plan_regression_cycle",
  "live_findings_cycle",
  "retention_cycle",
  "deep_scan",
  "weekly_report",
]);

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
  const { t } = useTranslation("settings");
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
    <Card title={t("integrations.title")} description={t("integrations.description")}>
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label={t("integrations.loading")} />}
      {data && (
        <div className={styles.integrations}>
          <section aria-labelledby="mcp-heading">
            <h3 id="mcp-heading" className={styles.integrationTitle}>
              {t("integrations.mcpTitle")}{" "}
              <Badge tone={data.mcp_enabled ? "healthy" : "neutral"}>
                {data.mcp_enabled ? t("integrations.on") : t("integrations.off")}
              </Badge>
            </h3>
            <p className={styles.integrationText}>
              {t("integrations.mcpText", { count: data.mcp_tools.length })}
              {!data.mcp_enabled && ` ${t("integrations.mcpOff")}`}
            </p>
            <div className={styles.snippet}>
              <code>{mcpCommand}</code>
              <CopyButton text={mcpCommand} />
            </div>
            <p className={styles.integrationText}>{t("integrations.tools", { tools: data.mcp_tools.join(", ") })}</p>
          </section>
          <section aria-labelledby="prometheus-heading">
            <h3 id="prometheus-heading" className={styles.integrationTitle}>
              Prometheus{" "}
              <Badge tone={data.metrics_enabled ? "healthy" : "neutral"}>
                {data.metrics_enabled ? t("integrations.on") : t("integrations.off")}
              </Badge>
            </h3>
            <p className={styles.integrationText}>
              {t("integrations.prometheusText")}
              {!data.metrics_enabled && ` ${t("integrations.metricsOff")}`}
            </p>
            <div className={styles.snippet}>
              <pre>{scrapeConfig}</pre>
              <CopyButton text={scrapeConfig} />
            </div>
          </section>
          {!data.public_url && (
            <Alert tone="info" compact>
              {t("integrations.publicUrl")}
            </Alert>
          )}
        </div>
      )}
    </Card>
  );
}

function SchedulerSettings() {
  const { t } = useTranslation("settings");
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
    <Card title={t("scheduler.title")} description={t("scheduler.description")}>
      {shownError && (
        <Alert tone="critical" compact>
          {shownError}
        </Alert>
      )}

      {isPending && <Skeleton lines={4} label={t("scheduler.loading")} />}

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
  const { t } = useTranslation("settings");
  const meta = KNOWN_JOBS.has(job.job_name)
    ? { label: t(`jobs.${job.job_name}.label`), description: t(`jobs.${job.job_name}.description`) }
    : { label: job.job_name, description: "" };
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
          {t("scheduler.lastUpdated", { time: formatRelativeTime(job.updated_at) })}
          <span role="status">{justSaved && ` · ${t("scheduler.saved")}`}</span>
        </span>
      </div>

      <div className={styles.jobControls}>
        <Checkbox checked={enabled} onChange={(e) => setEnabled(e.target.checked)}>
          {t("enabled")}
        </Checkbox>

        {job.job_kind === "interval" ? (
          <Field label={t("scheduler.interval")} className={styles.intervalField}>
            <input
              type="number"
              min="1"
              value={intervalSeconds}
              onChange={(e) => setIntervalSeconds(e.target.value)}
            />
          </Field>
        ) : (
          <Field label={t("scheduler.cron")} className={styles.cronField}>
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
          {saving ? t("common:actions.saving") : t("common:actions.save")}
        </button>
      </div>
    </form>
  );
}

function AiProviderSettings() {
  const { t } = useTranslation("settings");
  const { data: settings, error, isPending } = useQuery({
    queryKey: AI_SETTINGS_KEY,
    queryFn: ({ signal }) => api.getAiSettings({ signal }),
  });

  return (
    <Card title={t("ai.title")} description={t("ai.description")}>
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label={t("ai.loading")} />}
      {/* The form seeds its fields from the loaded settings once, on mount. */}
      {settings && <AiProviderForm settings={settings} />}
    </Card>
  );
}

// The link inside the "Model name" hint; Trans fills in its text.
function ModelListLink({ children }) {
  return (
    <a href="https://ai.google.dev/gemini-api/docs/models" target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

function AiProviderForm({ settings }) {
  const { t } = useTranslation("settings");
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

      <Field label={t("ai.provider")}>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="gemini">{t("ai.providerGemini")}</option>
          <option value="local">{t("ai.providerLocal")}</option>
        </select>
      </Field>

      {provider === "local" && (
        <Field label={t("ai.endpointUrl")}>
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
        label={t("ai.modelName")}
        hint={
          provider === "gemini" ? (
            <Trans
              t={t}
              i18nKey="ai.modelHint"
              components={{ a: <ModelListLink /> }}
            />
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

      <Field label={provider === "gemini" ? t("ai.apiKey") : t("ai.apiKeyOptional")}>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={settings.has_api_key ? t("alreadySet") : t("ai.apiKeyPlaceholder")}
          autoComplete="off"
        />
      </Field>

      <div className={styles.actions}>
        <button type="submit" className="button-primary button-sm" disabled={saving}>
          {saving ? t("common:actions.saving") : t("common:actions.save")}
        </button>
      </div>
      {justSaved && (
        <Alert tone="healthy" compact>
          {t("common:actions.saved")}
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

// Translation keys (settings namespace), resolved at render.
const WEBHOOK_KIND_LABEL = {
  slack: "alerts.kind.slack",
  discord: "alerts.kind.discord",
  teams: "alerts.kind.teams",
  generic: "alerts.kind.generic",
};

function AlertSettings() {
  const { t } = useTranslation("settings");
  const { data: settings, error, isPending } = useQuery({
    queryKey: ALERT_SETTINGS_KEY,
    queryFn: ({ signal }) => api.getAlertSettings({ signal }),
  });

  return (
    <Card title={t("alerts.title")} description={t("alerts.description")}>
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton lines={4} label={t("alerts.loading")} />}
      {/* The form seeds its fields from the loaded settings once, on mount. */}
      {settings && <AlertForm settings={settings} />}
    </Card>
  );
}

function AlertForm({ settings }) {
  const { t } = useTranslation("settings");
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
        {t("enabled")}
      </Checkbox>

      <Field
        label={t("alerts.webhookUrl")}
        hint={
          detectedKind ? (
            <>
              {t("alerts.detected")}{" "}
              <strong className={styles.detected}>{t(WEBHOOK_KIND_LABEL[detectedKind])}</strong>
            </>
          ) : undefined
        }
      >
        <input
          type="password"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
          placeholder={settings.has_webhook_url ? t("alreadySet") : "https://hooks.slack.com/services/..."}
          autoComplete="off"
        />
      </Field>

      <Field label={t("alerts.minSeverity")}>
        <select value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)}>
          <option value="critical">{t("alerts.criticalOnly")}</option>
          <option value="attention">{t("alerts.attentionAndCritical")}</option>
        </select>
      </Field>

      <Checkbox checked={reportWebhook} onChange={(e) => setReportWebhook(e.target.checked)}>
        {t("alerts.reportWebhook")}
      </Checkbox>

      <div className={styles.actions}>
        <button type="submit" className="button-primary button-sm" disabled={saving}>
          {saving ? t("common:actions.saving") : t("common:actions.save")}
        </button>
        <button type="button" className="button-secondary button-sm" onClick={handleTest} disabled={testing}>
          {testing ? t("alerts.sending") : t("alerts.sendTest")}
        </button>
      </div>

      {justSaved && (
        <Alert tone="healthy" compact>
          {t("common:actions.saved")}
        </Alert>
      )}

      {testResult && (
        <Alert tone={testResult.ok ? "healthy" : "critical"} compact>
          {testResult.ok ? t("alerts.testSent") : testResult.message}
        </Alert>
      )}
    </form>
  );
}
