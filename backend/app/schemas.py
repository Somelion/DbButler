import uuid
from datetime import datetime

from pydantic import BaseModel


class TargetCreate(BaseModel):
    name: str
    host: str
    port: int = 5432
    dbname: str
    username: str
    password: str
    sslmode: str = "prefer"
    # Optional schema allowlist — None/empty means "no filter, watch every
    # schema" (the default, backward-compatible behavior). When set, every
    # backend query that lists schemas/tables for this target only
    # considers the ones named here (app/schema_filter.py).
    allowed_schemas: list[str] | None = None


class TargetOut(BaseModel):
    id: uuid.UUID
    name: str
    host: str
    port: int
    dbname: str
    username: str
    sslmode: str
    detected_pg_version: str | None
    last_test_ok: bool | None
    last_test_at: datetime | None
    last_test_message: str | None
    is_active: bool
    created_at: datetime
    allowed_schemas: list[str] | None = None


class TargetUpdate(BaseModel):
    # All optional — a caller sends only the field(s) it wants to change
    # (rename, pause-or-resume background monitoring, replace the stored
    # password, and/or change the schema allowlist). At least one must be
    # set; the router 400s otherwise. password has no "clear" semantic
    # (unlike other write-only secrets in this app) — a blank/omitted value
    # always means "leave it alone". allowed_schemas: omit to leave
    # untouched, an empty list to clear it back to "no filter".
    name: str | None = None
    is_active: bool | None = None
    password: str | None = None
    allowed_schemas: list[str] | None = None


class ConnectionTestRequest(BaseModel):
    host: str
    port: int = 5432
    dbname: str
    username: str
    password: str
    sslmode: str = "prefer"


class ConnectionTestResult(BaseModel):
    ok: bool
    pg_version: str | None = None
    uptime_seconds: int | None = None
    uptime_human: str | None = None
    message: str | None = None


class PgBouncerConnectionCreate(BaseModel):
    host: str
    port: int = 6432
    username: str
    password: str
    sslmode: str = "prefer"


class PgBouncerConnectionOut(BaseModel):
    target_id: uuid.UUID
    host: str
    port: int
    username: str
    sslmode: str
    last_test_ok: bool | None
    last_test_at: datetime | None
    last_test_message: str | None
    created_at: datetime


class PgBouncerTestResult(BaseModel):
    ok: bool
    version: str | None = None
    message: str | None = None


class ActivitySession(BaseModel):
    pid: int
    usename: str | None
    application_name: str | None
    state: str | None
    wait_event_type: str | None
    wait_event: str | None
    query: str | None
    query_duration_seconds: int | None
    state_duration_seconds: int | None
    blocked_by_pids: list[int]
    blocking_pids: list[int]
    waiting_lock: str | None


class ActivityResponse(BaseModel):
    sessions: list[ActivitySession]


class TableHealthRow(BaseModel):
    schema_name: str
    table_name: str
    live_tuples: int
    dead_tuples: int
    dead_pct: float
    dead_pct_severity: str
    last_vacuum: datetime | None
    last_autovacuum: datetime | None
    last_analyze: datetime | None
    last_autoanalyze: datetime | None
    xid_age: int
    wraparound_severity: str
    total_bytes: int
    cache_hit_pct: float | None
    cache_hit_pct_severity: str


class TableHealthResponse(BaseModel):
    tables: list[TableHealthRow]


class CategoryStatus(BaseModel):
    key: str
    label: str
    severity: str
    value: str
    detail: str


class Finding(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    summary: str
    detail: str
    suggested_action: str
    # Set only on findings that are actually about one specific table (bloat,
    # wraparound, autovacuum staleness) — lets the Dashboard offer a direct
    # "Run Vacuum" action without the frontend parsing schema/table back out
    # of `detail`'s free-form text.
    schema_name: str | None = None
    table_name: str | None = None


class DashboardResponse(BaseModel):
    overall_severity: str
    categories: list[CategoryStatus]
    findings: list[Finding]


class ExtensionStatusResponse(BaseModel):
    """Backs the global pg_stat_statements banner (App.jsx) — a cheap,
    target-agnostic status the frontend can check on every screen, not just
    the Dashboard's own findings feed."""

    pg_stat_statements_enabled: bool
    message: str | None = None


class QueryStat(BaseModel):
    query: str
    calls: int
    total_exec_ms: float
    mean_exec_ms: float
    rows: int


class QueryStatsResponse(BaseModel):
    enabled: bool
    queries: list[QueryStat]
    message: str | None = None


class ExplainRequest(BaseModel):
    query: str


class ExplainFinding(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    summary: str
    detail: str
    suggested_action: str


class ExplainResponse(BaseModel):
    plan_text: str
    execution_time_ms: float | None
    findings: list[ExplainFinding]


class DiagnosisStep(BaseModel):
    step_number: int
    label: str
    severity: str
    value: str
    detail: str


class DiagnosisResponse(BaseModel):
    overall_severity: str
    top_finding: Finding | None
    steps: list[DiagnosisStep]
    top_queries: list[QueryStat]


class QueryOccurrence(BaseModel):
    # One specific query behind a finding that can match more than one query
    # at once (Schema Lint's Risky Query Patterns) — lets the UI expand a
    # single finding into the actual offending queries instead of naming
    # only the first match.
    query: str
    calls: int
    note: str | None = None


class IndexColumnInfo(BaseModel):
    # One index on a table this finding is about — used by Over-Indexed
    # Tables (every index on the table) and Covering Index Candidates (just
    # the one wide index in question) to let the UI render an index/column
    # coverage table instead of only a count.
    index_name: str
    columns: list[str]
    is_unique: bool
    method: str
    index_bytes: int | None = None


class CoveringColumnVerdict(BaseModel):
    column: str
    verdict: str  # "key" | "include_candidate" | "unused_in_sample" | "no_data"


class CoveringAnalysis(BaseModel):
    # The heuristic read on a wide index's trailing columns — see
    # find_covering_index_candidates in app/index_analysis.py.
    matched_query_count: int
    confident: bool
    columns: list[CoveringColumnVerdict]


class ExtensionInfo(BaseModel):
    # One row per pg_extension entry — Extension Advisor's "what's installed"
    # summary finding (app/extension_advisor.py::find_installed_extensions_summary),
    # rendered as pills by FindingCard.jsx so the actual names are visible
    # without switching to Advanced mode.
    name: str
    version: str


class IndexFinding(BaseModel):
    id: str
    category: str
    severity: str
    title: str
    summary: str
    detail: str
    suggested_action: str
    recommended_ddl: str | None = None
    occurrences: list[QueryOccurrence] | None = None
    index_columns: list[IndexColumnInfo] | None = None
    covering_analysis: CoveringAnalysis | None = None
    extensions: list[ExtensionInfo] | None = None
    # Set when a finding is genuinely about one specific schema/table — lets
    # Advisor's schema filter (ui/src/Advisor.jsx) narrow findings the same
    # way Table Health's own schema filter does. None on checks that are
    # inherently database/cluster-wide (a setting, a role, a query-pattern
    # check spanning multiple tables) — those are unaffected by the filter.
    schema_name: str | None = None
    table_name: str | None = None


class IndexAdvisorResponse(BaseModel):
    findings: list[IndexFinding]


class ApplyIndexFindingRequest(BaseModel):
    """Apply never accepts DDL text from the client — only a reference to
    something the server already computed (finding_id, re-derived fresh from
    the target's current catalogs) or already generated and stored itself
    (analysis_id, an AI covering-index suggestion). Exactly one is expected."""

    finding_id: str | None = None
    analysis_id: uuid.UUID | None = None


class ApplyTableHealthFindingRequest(BaseModel):
    schema_name: str
    table_name: str
    finding_id: str


class SchemaLintResponse(BaseModel):
    # Same shape as an index finding (severity/detail/optional DDL) — reused
    # rather than duplicated under a new name.
    findings: list[IndexFinding]


class ConfigAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class PlanRegressionsResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class ReplicationAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class BackupAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class PreUpgradeAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class SecurityAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class ExtensionAdvisorResponse(BaseModel):
    # Same reasoning as SchemaLintResponse — identical shape, no new type.
    findings: list[IndexFinding]


class TrackedIndexOut(BaseModel):
    id: uuid.UUID
    schema_name: str
    table_name: str
    index_name: str
    ddl: str
    created_at: datetime
    scans_since_added: int
    index_bytes: int
    seq_scans_since_added: int
    # "being_used" | "not_used_yet" | "too_early"
    verdict: str


class IndexTestingResponse(BaseModel):
    indexes: list[TrackedIndexOut]


class ArchivedFindingOut(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    finding_id: str
    category: str
    finding: IndexFinding
    archived_at: datetime


class ArchivedFindingsResponse(BaseModel):
    findings: list[ArchivedFindingOut]


class HardwareDetection(BaseModel):
    detected_ram_mb: int
    detected_cpu_cores: int
    caveat: str


class HardwareProfileCreate(BaseModel):
    confirmed_ram_mb: int
    confirmed_cpu_cores: int
    storage_type: str = "ssd"
    workload_type: str = "mixed"


class HardwareProfileOut(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    detected_ram_mb: int | None
    detected_cpu_cores: int | None
    confirmed_ram_mb: int
    confirmed_cpu_cores: int
    storage_type: str
    workload_type: str
    created_at: datetime


class ConfigDiffRow(BaseModel):
    name: str
    current_value: str
    recommended_value: str
    why: str
    requires_restart: bool


class ConfigDiffResponse(BaseModel):
    hardware_profile: HardwareProfileOut
    rows: list[ConfigDiffRow]


class TableMaintenanceRequest(BaseModel):
    schema_name: str
    table_name: str


class VacuumRequest(BaseModel):
    schema_name: str
    table_name: str
    analyze: bool = False


class MaintenanceResult(BaseModel):
    ok: bool
    message: str


class TrendPoint(BaseModel):
    ts: datetime
    value: float


class TrendForecast(BaseModel):
    # app/forecasting.py's linear fit over this series' own points — only
    # present for metrics routers/metrics.py::_build_forecast opts into
    # (currently database size and XID wraparound age), and only when the
    # trend is actually rising and there's enough history to trust it.
    slope_per_day: float
    span_days: float
    # Wraparound: days until crossing severity.py's WRAPAROUND_CRITICAL.
    days_to_threshold: float | None = None
    threshold_label: str | None = None
    # Database size: no fixed threshold, just where the trend points.
    projected_value_30d: float | None = None


class TrendSeries(BaseModel):
    metric_name: str
    label: str
    unit: str
    points: list[TrendPoint]
    forecast: TrendForecast | None = None


class TrendsResponse(BaseModel):
    hours: int
    series: list[TrendSeries]


class TableTrendSeries(BaseModel):
    schema_name: str
    table_name: str
    points: list[TrendPoint]


class TableTrendsResponse(BaseModel):
    metric: str
    label: str
    unit: str
    hours: int
    tables: list[TableTrendSeries]


class TableTrendSummaryRow(BaseModel):
    schema_name: str
    table_name: str
    latest_value: float


class TableTrendSummaryResponse(BaseModel):
    metric: str
    label: str
    unit: str
    tables: list[TableTrendSummaryRow]


class QueryHistorySummaryItem(BaseModel):
    queryid: int
    query: str
    calls: int
    mean_exec_ms: float
    last_seen_at: datetime


class QueryHistorySummaryResponse(BaseModel):
    queries: list[QueryHistorySummaryItem]


class QueryHistoryPoint(BaseModel):
    ts: datetime
    calls: int
    mean_exec_ms: float


class QueryHistorySeries(BaseModel):
    queryid: int
    query: str
    points: list[QueryHistoryPoint]


class QueryHistoryResponse(BaseModel):
    hours: int
    queries: list[QueryHistorySeries]


class SchedulerJobOut(BaseModel):
    job_name: str
    job_kind: str  # "interval" | "cron"
    interval_seconds: int | None
    cron_expr: str | None
    enabled: bool
    updated_at: datetime


class SchedulerJobUpdate(BaseModel):
    job_kind: str
    interval_seconds: int | None = None
    cron_expr: str | None = None
    enabled: bool = True


class SchedulerJobsResponse(BaseModel):
    jobs: list[SchedulerJobOut]


class DeepScanFindingOut(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    finding_id: str
    category: str
    finding: IndexFinding
    status: str  # "open" | "resolved"
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None


class DeepScanFindingsResponse(BaseModel):
    findings: list[DeepScanFindingOut]


class AiSettingsOut(BaseModel):
    provider: str  # "gemini" | "local"
    endpoint_url: str | None
    model_name: str
    has_api_key: bool
    updated_at: datetime


class AiSettingsUpdate(BaseModel):
    provider: str
    # None = leave the stored key unchanged; "" = clear it; anything else = replace it.
    api_key: str | None = None
    endpoint_url: str | None = None
    model_name: str


class AlertSettingsOut(BaseModel):
    enabled: bool
    has_webhook_url: bool
    # 'slack' | 'discord' | 'teams' | 'generic' | None (no URL configured, or
    # the stored one can no longer be decrypted) — detected fresh from the
    # stored URL on every read (app/alerting.py::detect_webhook_kind), never
    # itself persisted, so there's nothing to keep in sync when the URL
    # changes.
    webhook_kind: str | None
    min_severity: str
    updated_at: datetime


class AlertSettingsUpdate(BaseModel):
    enabled: bool
    # None = leave the stored webhook URL unchanged; "" = clear it; anything else = replace it.
    webhook_url: str | None = None
    min_severity: str = "critical"


class AlertTestResult(BaseModel):
    ok: bool
    message: str | None = None


class AnonymizePreviewRequest(BaseModel):
    query: str


class AnonymizePreviewResponse(BaseModel):
    anonymized_query: str


class QueryAnalysisRequest(BaseModel):
    query: str
    anonymize: bool = True


class QueryAnalysisOut(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    original_query: str
    anonymize: bool
    anonymized_query: str | None
    explain_plan: str | None
    provider: str | None
    model_name: str | None
    status: str  # "pending" | "done" | "error"
    ai_response: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class QueryAnalysesResponse(BaseModel):
    analyses: list[QueryAnalysisOut]


class IndexCoverageAnalysisRequest(BaseModel):
    finding_id: str


class IndexCoverageAnalysisOut(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    finding_id: str
    schema_name: str
    table_name: str
    index_name: str
    provider: str | None
    model_name: str | None
    status: str  # "pending" | "done" | "error"
    ai_response: str | None
    recommended_ddl: str | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class IndexCoverageAnalysesResponse(BaseModel):
    analyses: list[IndexCoverageAnalysisOut]


class TableColumnDetail(BaseModel):
    attname: str
    data_type: str
    avg_width: int | None
    null_frac: float | None
    n_distinct: float | None
    # NULL means "using the system default" on PostgreSQL 15+ (which
    # repurposed -1, used for the same meaning on PG13/14, to NULL) — never
    # assume this is always a real int.
    attstattarget: int | None
    is_indexed: bool
    storage: str  # 'plain' | 'extended' | 'external' | 'main'


class TableStorageDetail(BaseModel):
    heap_bytes: int
    toast_bytes: int
    index_bytes: int
    total_bytes: int


class TableAutovacuumDetail(BaseModel):
    autovacuum_enabled: bool
    vacuum_scale_factor: float
    analyze_scale_factor: float
    has_custom_scale_factor: bool
    fillfactor: int
    n_live_tup: int
    n_dead_tup: int
    n_tup_upd: int
    n_tup_hot_upd: int
    hot_update_ratio: float | None
    # From pg_stat_progress_vacuum — only populated while a VACUUM (manual or
    # autovacuum) is actively running against this table right now.
    vacuum_in_progress: bool
    vacuum_phase: str | None = None
    vacuum_progress_pct: float | None = None


class TableHealthDetailResponse(BaseModel):
    # Same IndexFinding shape every other Advisor-style screen uses, plus the
    # raw supporting data behind those checks — every column's stats, not
    # just the ones that triggered a finding (routers/table_health.py).
    findings: list[IndexFinding]
    columns: list[TableColumnDetail]
    storage: TableStorageDetail
    autovacuum: TableAutovacuumDetail


class WaitEventSnapshotRow(BaseModel):
    category: str
    count: int


class WaitEventSnapshotResponse(BaseModel):
    total_active: int
    rows: list[WaitEventSnapshotRow]


class WaitEventSeries(BaseModel):
    category: str
    points: list[TrendPoint]


class WaitEventTrendResponse(BaseModel):
    hours: int
    series: list[WaitEventSeries]


class DashboardCategoryInfo(BaseModel):
    # Describes one toggleable Dashboard tile/widget for the Customize
    # panel — a static registry (routers/dashboard_settings.py), not tied to
    # whether this particular target currently has data for it (Replication
    # and Pooler, for instance, are always listed even on a target with
    # neither configured).
    key: str
    label: str
    description: str


class DashboardSettingsOut(BaseModel):
    categories: list[DashboardCategoryInfo]
    hidden_categories: list[str]
    updated_at: datetime


class DashboardSettingsUpdate(BaseModel):
    hidden_categories: list[str]


class TableHealthDeepScanStatus(BaseModel):
    # Same shape returned by the read-only summary (page load) and by the
    # Run Deep Scan Now button (after it finishes) — routers/table_health.py.
    last_run_at: datetime | None
    last_run_status: str | None  # "ok" | "error" | None (never run yet)
    last_run_error: str | None
    open_findings: int
