// In the Docker deployment, nginx serves this build and reverse-proxies
// /api/* to the backend (see ui/nginx.conf), so a relative base keeps every
// request same-origin — no CORS, no hardcoded backend host/port. Local
// `npm run dev` (no nginx in front) still needs an absolute backend URL,
// defaulted to the docker-compose default port.
const API_BASE = import.meta.env.VITE_API_BASE || (import.meta.env.PROD ? "" : "http://localhost:8000");
const TOKEN_STORAGE_KEY = "pgdba.authToken";

// sessionStorage, not localStorage — the token dies with the tab, which is
// the right default for a shared-secret gate (see AuthGate.jsx).
let authToken = sessionStorage.getItem(TOKEN_STORAGE_KEY) || "";
let unauthorizedHandler = null;

export function getAuthToken() {
  return authToken;
}

export function setAuthToken(token) {
  authToken = token || "";
  if (authToken) sessionStorage.setItem(TOKEN_STORAGE_KEY, authToken);
  else sessionStorage.removeItem(TOKEN_STORAGE_KEY);
}

// AuthGate registers itself here so a 401 surfacing from ANY screen's
// request (e.g. the backend restarted with a rotated token mid-session) can
// bounce the whole app back to the token prompt, not just the initial check.
export function onUnauthorized(handler) {
  unauthorizedHandler = handler;
}

// Raised for any non-2xx response, carrying the HTTP status so callers (and
// the query client's retry policy) can tell a 4xx from a network blip.
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function send(path, options = {}) {
  const { headers: extraHeaders, ...rest } = options;
  const headers = {
    "Content-Type": "application/json",
    ...extraHeaders,
    ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
  };

  const res = await fetch(`${API_BASE}${path}`, { ...rest, headers });

  if (res.status === 401) {
    const body = await res.json().catch(() => ({}));
    setAuthToken("");
    unauthorizedHandler?.();
    throw new ApiError(body.detail || "Invalid or missing auth token.", 401);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail || `Request failed (${res.status})`, res.status);
  }
  return res;
}

async function request(path, options = {}) {
  const res = await send(path, options);
  return res.status === 204 ? null : res.json();
}

// For the few endpoints that answer with a document rather than JSON (the
// weekly report's HTML).
async function requestText(path, options = {}) {
  const res = await send(path, options);
  return res.text();
}

// Every read takes an optional trailing `opts` ({ signal }) — TanStack
// Query passes an AbortSignal, so switching database or leaving a screen
// cancels the in-flight request instead of letting a slow response from the
// old target land on the new one.
export const api = {
  checkAuth: (opts) => request("/api/auth/check", opts),
  listTargets: (opts) => request("/api/targets", opts),
  createTarget: (payload) => request("/api/targets", { method: "POST", body: JSON.stringify(payload) }),
  deleteTarget: (id) => request(`/api/targets/${id}`, { method: "DELETE" }),
  updateTarget: (id, payload) => request(`/api/targets/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  testNewConnection: (payload) => request("/api/targets/test", { method: "POST", body: JSON.stringify(payload) }),
  retestTarget: (id) => request(`/api/targets/${id}/test`, { method: "POST" }),
  getMetric: (targetId, metricName, opts) => request(`/api/targets/${targetId}/metrics/${metricName}`, opts),
  getActivity: (targetId, opts) => request(`/api/targets/${targetId}/activity`, opts),
  cancelQuery: (targetId, pid) => request(`/api/targets/${targetId}/activity/${pid}/cancel`, { method: "POST" }),
  terminateSession: (targetId, pid) =>
    request(`/api/targets/${targetId}/activity/${pid}/terminate`, { method: "POST" }),
  getTableHealth: (targetId, opts) => request(`/api/targets/${targetId}/table-health`, opts),
  getTableHealthFindings: (targetId, schemaName, tableName, opts) =>
    request(
      `/api/targets/${targetId}/table-health/${encodeURIComponent(schemaName)}/${encodeURIComponent(tableName)}/findings`,
      opts
    ),
  // The server always re-derives the DDL itself from schema_name/table_name
  // + finding_id — it never runs DDL text supplied by the client.
  applyTableHealthFinding: (targetId, schemaName, tableName, findingId) =>
    request(`/api/targets/${targetId}/table-health/apply`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName, table_name: tableName, finding_id: findingId }),
    }),
  getTableHealthDeepScanStatus: (targetId, opts) => request(`/api/targets/${targetId}/table-health/deep-scan`, opts),
  runTableHealthDeepScan: (targetId) =>
    request(`/api/targets/${targetId}/table-health/deep-scan`, { method: "POST" }),
  getDashboard: (targetId, opts) => request(`/api/targets/${targetId}/dashboard`, opts),
  getExtensionStatus: (targetId, opts) => request(`/api/targets/${targetId}/extension-status`, opts),
  getDashboardSettings: (opts) => request("/api/dashboard-settings", opts),
  updateDashboardSettings: (payload) =>
    request("/api/dashboard-settings", { method: "PUT", body: JSON.stringify(payload) }),
  getWaitEvents: (targetId, opts) => request(`/api/targets/${targetId}/wait-events`, opts),
  getWaitEventTrend: (targetId, hours, opts) =>
    request(`/api/targets/${targetId}/wait-events/trend?hours=${hours}`, opts),
  getQueryStats: (targetId, opts) => request(`/api/targets/${targetId}/query-stats`, opts),
  explainQuery: (targetId, query) =>
    request(`/api/targets/${targetId}/explain`, { method: "POST", body: JSON.stringify({ query }) }),
  diagnose: (targetId, opts) => request(`/api/targets/${targetId}/diagnose`, opts),
  getIndexAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/index-advisor`, opts),
  // Same story as applyTableHealthFinding — the server re-derives the DDL
  // from finding_id (or, for an AI covering-index suggestion, looks it up
  // by analysis_id from what it already generated and stored itself).
  applyIndexFinding: (targetId, findingId) =>
    request(`/api/targets/${targetId}/index-advisor/apply`, {
      method: "POST",
      body: JSON.stringify({ finding_id: findingId }),
    }),
  applyIndexCoverageAnalysis: (targetId, analysisId) =>
    request(`/api/targets/${targetId}/index-advisor/apply`, {
      method: "POST",
      body: JSON.stringify({ analysis_id: analysisId }),
    }),
  getSchemaLint: (targetId, opts) => request(`/api/targets/${targetId}/schema-lint`, opts),
  getConfigAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/config-advisor`, opts),
  getReplicationAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/replication-advisor`, opts),
  getBackupAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/backup-advisor`, opts),
  getPreUpgradeAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/pre-upgrade-advisor`, opts),
  getSecurityAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/security-advisor`, opts),
  getExtensionAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/extension-advisor`, opts),
  getPgBouncerConnection: (targetId, opts) => request(`/api/targets/${targetId}/pgbouncer`, opts),
  testNewPgBouncerConnection: (targetId, payload) =>
    request(`/api/targets/${targetId}/pgbouncer/test`, { method: "POST", body: JSON.stringify(payload) }),
  savePgBouncerConnection: (targetId, payload) =>
    request(`/api/targets/${targetId}/pgbouncer`, { method: "POST", body: JSON.stringify(payload) }),
  retestPgBouncerConnection: (targetId) =>
    request(`/api/targets/${targetId}/pgbouncer/retest`, { method: "POST" }),
  deletePgBouncerConnection: (targetId) => request(`/api/targets/${targetId}/pgbouncer`, { method: "DELETE" }),
  getIndexTesting: (targetId, opts) => request(`/api/targets/${targetId}/index-testing`, opts),
  dropTrackedIndex: (targetId, trackedId) =>
    request(`/api/targets/${targetId}/index-testing/${trackedId}/drop`, { method: "POST" }),
  archiveFinding: (targetId, finding) =>
    request(`/api/targets/${targetId}/advisor/archive`, { method: "POST", body: JSON.stringify(finding) }),
  getArchivedFindings: (targetId, opts) => request(`/api/targets/${targetId}/advisor/archive`, opts),
  restoreFinding: (targetId, findingId) =>
    request(`/api/targets/${targetId}/advisor/archive/${encodeURIComponent(findingId)}`, { method: "DELETE" }),
  detectHardware: (targetId, opts) => request(`/api/targets/${targetId}/hardware-profile/detect`, opts),
  getHardwareProfile: (targetId, opts) => request(`/api/targets/${targetId}/hardware-profile`, opts),
  saveHardwareProfile: (targetId, payload) =>
    request(`/api/targets/${targetId}/hardware-profile`, { method: "POST", body: JSON.stringify(payload) }),
  getConfigTuning: (targetId, opts) => request(`/api/targets/${targetId}/config-tuning`, opts),
  analyzeTable: (targetId, schemaName, tableName) =>
    request(`/api/targets/${targetId}/maintenance/analyze`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName, table_name: tableName }),
    }),
  vacuumTable: (targetId, schemaName, tableName, analyze) =>
    request(`/api/targets/${targetId}/maintenance/vacuum`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName, table_name: tableName, analyze }),
    }),
  resetTableStats: (targetId, schemaName, tableName) =>
    request(`/api/targets/${targetId}/maintenance/reset-table-stats`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName, table_name: tableName }),
    }),
  // ANALYZE every table in a schema, as a background job: poll
  // getAnalyzeSchemaJob until its status isn't "running".
  analyzeSchema: (targetId, schemaName) =>
    request(`/api/targets/${targetId}/maintenance/analyze-schema`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName }),
    }),
  getAnalyzeSchemaJob: (targetId, jobId, opts) =>
    request(`/api/targets/${targetId}/maintenance/analyze-schema/${encodeURIComponent(jobId)}`, opts),
  resetQueryStats: (targetId) =>
    request(`/api/targets/${targetId}/maintenance/reset-query-stats`, { method: "POST" }),
  getTrends: (targetId, hours, opts) => request(`/api/targets/${targetId}/trends?hours=${hours}`, opts),
  getTableTrendsSummary: (targetId, metric, opts) =>
    request(`/api/targets/${targetId}/table-trends/summary?metric=${metric}`, opts),
  getTableTrends: (targetId, metric, hours, tables, opts) => {
    const tableParams = tables.map((t) => `tables=${encodeURIComponent(t)}`).join("&");
    return request(`/api/targets/${targetId}/table-trends?metric=${metric}&hours=${hours}&${tableParams}`, opts);
  },
  getSchedulerJobs: (opts) => request("/api/scheduler/jobs", opts),
  updateSchedulerJob: (jobName, payload) =>
    request(`/api/scheduler/jobs/${encodeURIComponent(jobName)}`, { method: "PUT", body: JSON.stringify(payload) }),
  getDeepScanFindings: (targetId, status, opts) =>
    request(`/api/targets/${targetId}/deep-scan/findings${status ? `?status=${status}` : ""}`, opts),
  getAiSettings: (opts) => request("/api/ai/settings", opts),
  updateAiSettings: (payload) => request("/api/ai/settings", { method: "PUT", body: JSON.stringify(payload) }),
  getAlertSettings: (opts) => request("/api/alerts/settings", opts),
  updateAlertSettings: (payload) => request("/api/alerts/settings", { method: "PUT", body: JSON.stringify(payload) }),
  testAlertWebhook: () => request("/api/alerts/test", { method: "POST" }),
  anonymizeQueryPreview: (targetId, query, opts) =>
    request(`/api/targets/${targetId}/anonymize-preview`, { method: "POST", body: JSON.stringify({ query }), ...opts }),
  createQueryAnalysis: (targetId, payload) =>
    request(`/api/targets/${targetId}/query-analyses`, { method: "POST", body: JSON.stringify(payload) }),
  getQueryAnalyses: (targetId, opts) => request(`/api/targets/${targetId}/query-analyses`, opts),
  createIndexCoverageAnalysis: (targetId, findingId) =>
    request(`/api/targets/${targetId}/index-coverage-analyses`, {
      method: "POST",
      body: JSON.stringify({ finding_id: findingId }),
    }),
  getIndexCoverageAnalyses: (targetId, findingId, opts) =>
    request(`/api/targets/${targetId}/index-coverage-analyses?finding_id=${encodeURIComponent(findingId)}`, opts),
  getQueryHistorySummary: (targetId, opts) => request(`/api/targets/${targetId}/query-history/summary`, opts),
  getQueryHistory: (targetId, hours, queryids, opts) => {
    const idParams = queryids.map((id) => `queryids=${encodeURIComponent(id)}`).join("&");
    return request(`/api/targets/${targetId}/query-history?hours=${hours}&${idParams}`, opts);
  },
  getPlanRegressions: (targetId, opts) => request(`/api/targets/${targetId}/plan-regressions`, opts),
  getRefactoringAdvisor: (targetId, opts) => request(`/api/targets/${targetId}/refactoring-advisor`, opts),
  // Read-only: HypoPG plans with hypothetical indexes kept in the session's
  // memory; nothing is created on the target.
  runWhatIf: (targetId, candidates, hours) =>
    request(`/api/targets/${targetId}/what-if`, { method: "POST", body: JSON.stringify({ candidates, hours }) }),
  // queryidText is the decimal string from the finding's id, never a JSON
  // number — a 64-bit queryid would be rounded.
  getPlanHistory: (targetId, queryidText, opts) =>
    request(`/api/targets/${targetId}/plan-regressions/${encodeURIComponent(queryidText)}/plans`, opts),
  // `until` (ISO string, optional) ends the window in the past — the map's
  // time slider.
  getWorkloadGraph: (targetId, hours, opts, until) =>
    request(
      `/api/targets/${targetId}/workload-graph?hours=${hours}${until ? `&until=${encodeURIComponent(until)}` : ""}`,
      opts
    ),
  getTimeline: (targetId, hours, until, opts) =>
    request(
      `/api/targets/${targetId}/timeline?hours=${hours}${until ? `&until=${encodeURIComponent(until)}` : ""}`,
      opts
    ),
  getTimelineMoment: (targetId, at, opts) =>
    request(`/api/targets/${targetId}/timeline/moment?at=${encodeURIComponent(at)}`, opts),
  // Sends facts (counts, table/lock names, finding titles) — never query
  // text — to the configured AI provider. Starts a background job; poll
  // getTimelineExplanation until its status isn't "pending".
  explainTimeline: (targetId, start, end) =>
    request(`/api/targets/${targetId}/timeline/explain`, { method: "POST", body: JSON.stringify({ start, end }) }),
  getTimelineExplanation: (targetId, jobId, opts) =>
    request(`/api/targets/${targetId}/timeline/explain/${encodeURIComponent(jobId)}`, opts),
  // Nothing in `sql` is executed: the backend parses it with PostgreSQL's
  // grammar. With a targetId it also reads that database's live lock and
  // size information to predict the impact.
  lintMigration: (sql, targetId) =>
    request("/api/migration-lint", { method: "POST", body: JSON.stringify({ sql, target_id: targetId ?? null }) }),
  getReports: (targetId, opts) => request(`/api/targets/${targetId}/reports`, opts),
  createReport: (targetId) => request(`/api/targets/${targetId}/reports`, { method: "POST" }),
  getReport: (reportId, opts) => request(`/api/reports/${encodeURIComponent(reportId)}`, opts),
  getReportHtml: (reportId, opts) => requestText(`/api/reports/${encodeURIComponent(reportId)}/html`, opts),
  getIntegrations: (opts) => request("/api/integrations", opts),
  // Feature flags from the server's .env (APP_FEATURE_*).
  getFeatures: (opts) => request("/api/features", opts),
  getWorkloadTable: (targetId, schemaName, tableName, hours, opts, until) =>
    request(
      `/api/targets/${targetId}/workload-graph/tables/${encodeURIComponent(schemaName)}/${encodeURIComponent(tableName)}?hours=${hours}${until ? `&until=${encodeURIComponent(until)}` : ""}`,
      opts
    ),
};
