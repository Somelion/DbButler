const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
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

async function request(path, options = {}) {
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
    throw new Error(body.detail || "Invalid or missing auth token.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  checkAuth: () => request("/api/auth/check"),
  listTargets: () => request("/api/targets"),
  createTarget: (payload) => request("/api/targets", { method: "POST", body: JSON.stringify(payload) }),
  deleteTarget: (id) => request(`/api/targets/${id}`, { method: "DELETE" }),
  updateTarget: (id, payload) => request(`/api/targets/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  testNewConnection: (payload) => request("/api/targets/test", { method: "POST", body: JSON.stringify(payload) }),
  retestTarget: (id) => request(`/api/targets/${id}/test`, { method: "POST" }),
  getMetric: (targetId, metricName) => request(`/api/targets/${targetId}/metrics/${metricName}`),
  getActivity: (targetId) => request(`/api/targets/${targetId}/activity`),
  cancelQuery: (targetId, pid) => request(`/api/targets/${targetId}/activity/${pid}/cancel`, { method: "POST" }),
  terminateSession: (targetId, pid) =>
    request(`/api/targets/${targetId}/activity/${pid}/terminate`, { method: "POST" }),
  getTableHealth: (targetId) => request(`/api/targets/${targetId}/table-health`),
  getTableHealthFindings: (targetId, schemaName, tableName) =>
    request(
      `/api/targets/${targetId}/table-health/${encodeURIComponent(schemaName)}/${encodeURIComponent(tableName)}/findings`
    ),
  // The server always re-derives the DDL itself from schema_name/table_name
  // + finding_id — it never runs DDL text supplied by the client.
  applyTableHealthFinding: (targetId, schemaName, tableName, findingId) =>
    request(`/api/targets/${targetId}/table-health/apply`, {
      method: "POST",
      body: JSON.stringify({ schema_name: schemaName, table_name: tableName, finding_id: findingId }),
    }),
  getTableHealthDeepScanStatus: (targetId) => request(`/api/targets/${targetId}/table-health/deep-scan`),
  runTableHealthDeepScan: (targetId) =>
    request(`/api/targets/${targetId}/table-health/deep-scan`, { method: "POST" }),
  getDashboard: (targetId) => request(`/api/targets/${targetId}/dashboard`),
  getDashboardSettings: () => request("/api/dashboard-settings"),
  updateDashboardSettings: (payload) =>
    request("/api/dashboard-settings", { method: "PUT", body: JSON.stringify(payload) }),
  getWaitEvents: (targetId) => request(`/api/targets/${targetId}/wait-events`),
  getWaitEventTrend: (targetId, hours) => request(`/api/targets/${targetId}/wait-events/trend?hours=${hours}`),
  getQueryStats: (targetId) => request(`/api/targets/${targetId}/query-stats`),
  explainQuery: (targetId, query) =>
    request(`/api/targets/${targetId}/explain`, { method: "POST", body: JSON.stringify({ query }) }),
  diagnose: (targetId) => request(`/api/targets/${targetId}/diagnose`),
  getIndexAdvisor: (targetId) => request(`/api/targets/${targetId}/index-advisor`),
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
  getSchemaLint: (targetId) => request(`/api/targets/${targetId}/schema-lint`),
  getConfigAdvisor: (targetId) => request(`/api/targets/${targetId}/config-advisor`),
  getReplicationAdvisor: (targetId) => request(`/api/targets/${targetId}/replication-advisor`),
  getBackupAdvisor: (targetId) => request(`/api/targets/${targetId}/backup-advisor`),
  getPreUpgradeAdvisor: (targetId) => request(`/api/targets/${targetId}/pre-upgrade-advisor`),
  getSecurityAdvisor: (targetId) => request(`/api/targets/${targetId}/security-advisor`),
  getExtensionAdvisor: (targetId) => request(`/api/targets/${targetId}/extension-advisor`),
  getPgBouncerConnection: (targetId) => request(`/api/targets/${targetId}/pgbouncer`),
  testNewPgBouncerConnection: (targetId, payload) =>
    request(`/api/targets/${targetId}/pgbouncer/test`, { method: "POST", body: JSON.stringify(payload) }),
  savePgBouncerConnection: (targetId, payload) =>
    request(`/api/targets/${targetId}/pgbouncer`, { method: "POST", body: JSON.stringify(payload) }),
  retestPgBouncerConnection: (targetId) =>
    request(`/api/targets/${targetId}/pgbouncer/retest`, { method: "POST" }),
  deletePgBouncerConnection: (targetId) => request(`/api/targets/${targetId}/pgbouncer`, { method: "DELETE" }),
  getIndexTesting: (targetId) => request(`/api/targets/${targetId}/index-testing`),
  dropTrackedIndex: (targetId, trackedId) =>
    request(`/api/targets/${targetId}/index-testing/${trackedId}/drop`, { method: "POST" }),
  archiveFinding: (targetId, finding) =>
    request(`/api/targets/${targetId}/advisor/archive`, { method: "POST", body: JSON.stringify(finding) }),
  getArchivedFindings: (targetId) => request(`/api/targets/${targetId}/advisor/archive`),
  restoreFinding: (targetId, findingId) =>
    request(`/api/targets/${targetId}/advisor/archive/${encodeURIComponent(findingId)}`, { method: "DELETE" }),
  detectHardware: (targetId) => request(`/api/targets/${targetId}/hardware-profile/detect`),
  getHardwareProfile: (targetId) => request(`/api/targets/${targetId}/hardware-profile`),
  saveHardwareProfile: (targetId, payload) =>
    request(`/api/targets/${targetId}/hardware-profile`, { method: "POST", body: JSON.stringify(payload) }),
  getConfigTuning: (targetId) => request(`/api/targets/${targetId}/config-tuning`),
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
  resetQueryStats: (targetId) =>
    request(`/api/targets/${targetId}/maintenance/reset-query-stats`, { method: "POST" }),
  getTrends: (targetId, hours) => request(`/api/targets/${targetId}/trends?hours=${hours}`),
  getTableTrendsSummary: (targetId, metric) =>
    request(`/api/targets/${targetId}/table-trends/summary?metric=${metric}`),
  getTableTrends: (targetId, metric, hours, tables) => {
    const tableParams = tables.map((t) => `tables=${encodeURIComponent(t)}`).join("&");
    return request(`/api/targets/${targetId}/table-trends?metric=${metric}&hours=${hours}&${tableParams}`);
  },
  getSchedulerJobs: () => request("/api/scheduler/jobs"),
  updateSchedulerJob: (jobName, payload) =>
    request(`/api/scheduler/jobs/${encodeURIComponent(jobName)}`, { method: "PUT", body: JSON.stringify(payload) }),
  getDeepScanFindings: (targetId, status) =>
    request(`/api/targets/${targetId}/deep-scan/findings${status ? `?status=${status}` : ""}`),
  getAiSettings: () => request("/api/ai/settings"),
  updateAiSettings: (payload) => request("/api/ai/settings", { method: "PUT", body: JSON.stringify(payload) }),
  getAlertSettings: () => request("/api/alerts/settings"),
  updateAlertSettings: (payload) => request("/api/alerts/settings", { method: "PUT", body: JSON.stringify(payload) }),
  testAlertWebhook: () => request("/api/alerts/test", { method: "POST" }),
  anonymizeQueryPreview: (targetId, query) =>
    request(`/api/targets/${targetId}/anonymize-preview`, { method: "POST", body: JSON.stringify({ query }) }),
  createQueryAnalysis: (targetId, payload) =>
    request(`/api/targets/${targetId}/query-analyses`, { method: "POST", body: JSON.stringify(payload) }),
  getQueryAnalyses: (targetId) => request(`/api/targets/${targetId}/query-analyses`),
  createIndexCoverageAnalysis: (targetId, findingId) =>
    request(`/api/targets/${targetId}/index-coverage-analyses`, {
      method: "POST",
      body: JSON.stringify({ finding_id: findingId }),
    }),
  getIndexCoverageAnalyses: (targetId, findingId) =>
    request(`/api/targets/${targetId}/index-coverage-analyses?finding_id=${encodeURIComponent(findingId)}`),
  getQueryHistorySummary: (targetId) => request(`/api/targets/${targetId}/query-history/summary`),
  getQueryHistory: (targetId, hours, queryids) => {
    const idParams = queryids.map((id) => `queryids=${encodeURIComponent(id)}`).join("&");
    return request(`/api/targets/${targetId}/query-history?hours=${hours}&${idParams}`);
  },
  getPlanRegressions: (targetId) => request(`/api/targets/${targetId}/plan-regressions`),
};
