import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronRight, Eraser, RefreshCw } from "lucide-react";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { formatBytes, formatRelativeTime } from "./format";
import HeaderHint from "./HeaderHint";
import TableHealthDetail from "./TableHealthDetail";
import CategoryFilterDropdown from "./CategoryFilterDropdown";
import { useFirstSeenMap } from "./useFirstSeenMap";
import { Alert, Card, EmptyState, SeverityIcon, Skeleton, SortHeader, useConfirm, useToast } from "./components";
import tableStyles from "./components/Table.module.css";
import tone from "./components/tone.module.css";
import styles from "./TableHealth.module.css";

const POLL_INTERVAL_MS = 15000;

// Shown when the live count is the catalog's estimate (app/live_tuples.py).
// A translation key, translated at render.
const ESTIMATED_ROWS_HINT = "estimatedRowsHint";
const SEVERITY_RANK = { critical: 2, attention: 1, healthy: 0 };

const hiddenSchemasKey = (targetId) => `pgdba.tableHealth.hiddenSchemas.${targetId}`;

// Query keys. All three sit under "table-health" so the whole screen can be
// refreshed at once, but each part is invalidated on its own after an action.
const tablesKey = (targetId) => targetKey(targetId, "table-health", "tables");
const deepScanKey = (targetId) => targetKey(targetId, "table-health", "deep-scan");
const findingsKey = (targetId, schemaName, tableName) =>
  targetKey(targetId, "table-health", "findings", schemaName, tableName);

const fullNameOf = (table) => `${table.schema_name}.${table.table_name}`;

// A manual Vacuum/Analyze (maintenance.py) updates Postgres's last_vacuum/
// last_analyze columns, never the last_autovacuum/last_autoanalyze ones —
// so picking whichever of the two is non-null with a fixed priority (as
// this used to do) can keep showing a stale autovacuum timestamp right
// after a fresh manual run. Compare and take whichever actually happened
// more recently instead.
function mostRecent(a, b) {
  if (!a) return b;
  if (!b) return a;
  return new Date(a) > new Date(b) ? a : b;
}

// `select` for the table list query: a stable module-level function, so
// TanStack Query only re-runs it when the fetched data actually changes.
function selectTables(data) {
  return data.tables.map((t) => ({
    ...t,
    last_vacuum_at: mostRecent(t.last_vacuum, t.last_autovacuum),
    last_analyze_at: mostRecent(t.last_analyze, t.last_autoanalyze),
  }));
}

function loadHiddenSchemas(targetId) {
  try {
    const raw = localStorage.getItem(hiddenSchemasKey(targetId));
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed) : new Set();
  } catch {
    return new Set();
  }
}

// label/help are translation keys, translated at render.
const COLUMNS = [
  { key: "table_name", label: "columns.table.label", help: "columns.table.help" },
  { key: "live_tuples", label: "columns.rows.label", help: "columns.rows.help" },
  { key: "dead_pct", label: "columns.deadPct.label", help: "columns.deadPct.help" },
  { key: "last_vacuum_at", label: "columns.lastVacuum.label", help: "columns.lastVacuum.help" },
  { key: "last_analyze_at", label: "columns.lastAnalyze.label", help: "columns.lastAnalyze.help" },
  { key: "xid_age", label: "columns.wraparound.label", help: "columns.wraparound.help" },
  { key: "cache_hit_pct", label: "columns.cacheHit.label", help: "columns.cacheHit.help" },
  { key: "total_bytes", label: "columns.size.label", help: "columns.size.help" },
  { key: "", label: "columns.actions.label" },
];

// ?sort= is validated against these; anything else falls back to the default.
const SORT_KEYS = new Set(COLUMNS.map((col) => col.key).filter(Boolean));
const DEFAULT_SORT_KEY = "dead_pct";

export default function TableHealth({ targetId }) {
  const { t } = useTranslation("tableHealth");
  const queryClient = useQueryClient();
  const idPrefix = useId();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const sortParam = searchParams.get("sort");
  const dirParam = searchParams.get("dir");
  const sortKey = SORT_KEYS.has(sortParam) ? sortParam : DEFAULT_SORT_KEY;
  const sortDir = dirParam === "asc" || dirParam === "desc" ? dirParam : "desc";
  // ?table=<schema>.<table> — the most recently expanded row. Shareable:
  // opening the URL expands (and scrolls to) that table.
  const tableParam = searchParams.get("table") || null;
  // Set on our own ?table= updates (row expand/collapse), so only a table
  // named by an incoming link gets scrolled to — never the row the user
  // just clicked.
  const tableParamFromToggle = location.state?.tableToggle === true;

  const [firstSeenByFindingId, refreshFirstSeen] = useFirstSeenMap(targetId);
  // null = not yet initialized. Defaults to "all collapsed" once we know
  // there's more than one schema (so you open only the one you're
  // interested in); a single-schema database just stays fully expanded,
  // matching this screen's pre-grouping behavior. Never reset after that,
  // so a later poll doesn't undo the user's own expand/collapse choices.
  // A different target remounts this screen (App keys it on targetId), so
  // it starts over from null for the new schema list.
  const [collapsedSchemas, setCollapsedSchemas] = useState(null);
  const [hiddenSchemas, setHiddenSchemas] = useState(() => loadHiddenSchemas(targetId));
  // Rows expanded locally, on top of whichever one ?table= names — several
  // can be open at once, only the most recent one goes into the URL.
  const [expandedTables, setExpandedTables] = useState(() => new Set());
  // The deep-linked table still to be revealed and scrolled to; cleared by
  // the first expand/collapse/filter the user does, so it never fights them.
  const [scrollTarget, setScrollTarget] = useState(tableParam);
  const [seenTableParam, setSeenTableParam] = useState(tableParam);
  if (tableParam !== seenTableParam) {
    setSeenTableParam(tableParam);
    if (tableParam && !tableParamFromToggle) setScrollTarget(tableParam);
  }

  const tablesQuery = useQuery({
    queryKey: tablesKey(targetId),
    queryFn: ({ signal }) => api.getTableHealth(targetId, { signal }),
    select: selectTables,
    refetchInterval: POLL_INTERVAL_MS,
  });
  const tables = tablesQuery.data;

  const deepScanQuery = useQuery({
    queryKey: deepScanKey(targetId),
    queryFn: ({ signal }) => api.getTableHealthDeepScanStatus(targetId, { signal }),
  });
  const deepScan = deepScanQuery.data ?? null;

  const deepScanMutation = useMutation({
    mutationFn: () => api.runTableHealthDeepScan(targetId),
    onSuccess: (data) => {
      queryClient.setQueryData(deepScanKey(targetId), data);
      refreshFirstSeen();
    },
  });
  const deepScanError = deepScanMutation.error?.message ?? deepScanQuery.error?.message ?? null;

  // Only initializes once per target (skipped once non-null), off the first
  // data that arrives — React's "adjust state while rendering" pattern, not
  // an effect, so it can't race a refetch. The deep-linked table's schema
  // starts expanded so its row is actually on screen.
  const linkedSchema =
    scrollTarget && tables ? tables.find((t) => fullNameOf(t) === scrollTarget)?.schema_name : undefined;
  if (tables && collapsedSchemas === null) {
    const schemaNames = [...new Set(tables.map((t) => t.schema_name))];
    const initial = schemaNames.length > 1 ? new Set(schemaNames) : new Set();
    if (linkedSchema) initial.delete(linkedSchema);
    setCollapsedSchemas(initial);
  } else if (linkedSchema && collapsedSchemas?.has(linkedSchema)) {
    const next = new Set(collapsedSchemas);
    next.delete(linkedSchema);
    setCollapsedSchemas(next);
  }

  // Resolves to the freshly-fetched rows (not just void) so a caller that
  // just ran Analyze/Vacuum/Reset stats on one table (TableRow.runAction
  // below) can look up that table's own new numbers and show a real
  // before/after comparison, instead of just "it ran" with no way to tell
  // whether it actually changed anything. invalidateQueries waits for the
  // active list query to refetch, so the cache holds the new rows after it;
  // a failed refetch keeps the old rows, which mustn't pass for "after".
  const refreshTables = async () => {
    const key = tablesKey(targetId);
    const updatedBefore = queryClient.getQueryState(key)?.dataUpdatedAt ?? 0;
    await queryClient.invalidateQueries({ queryKey: key });
    const state = queryClient.getQueryState(key);
    return state && state.dataUpdatedAt > updatedBefore ? state.data?.tables : undefined;
  };

  const sorted = useMemo(() => {
    const copy = [...(tables ?? [])];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return copy;
  }, [tables, sortKey, sortDir]);

  const handleSort = (key) => {
    const nextDir = key === sortKey ? (sortDir === "asc" ? "desc" : "asc") : "desc";
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("sort", key);
        next.set("dir", nextDir);
        return next;
      },
      { replace: true }
    );
  };

  // Partitions the already-sorted list by schema, preserving each table's
  // position relative to others in the same schema — grouping never
  // disturbs whatever column/direction is currently sorted.
  const grouped = useMemo(() => {
    const bySchema = new Map();
    for (const table of sorted) {
      if (!bySchema.has(table.schema_name)) bySchema.set(table.schema_name, []);
      bySchema.get(table.schema_name).push(table);
    }
    return [...bySchema.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [sorted]);

  const toggleSchema = (schemaName, isCollapsed) => {
    setScrollTarget(null);
    setCollapsedSchemas((prev) => {
      const next = new Set(prev);
      if (isCollapsed) next.delete(schemaName);
      else next.add(schemaName);
      return next;
    });
  };

  const toggleTable = (fullName, isExpanded) => {
    setScrollTarget(null);
    setExpandedTables((prev) => {
      const next = new Set(prev);
      if (isExpanded) {
        next.delete(fullName);
      } else {
        next.add(fullName);
        // The row ?table= named so far stays open locally once the URL
        // moves on to this one.
        if (tableParam) next.add(tableParam);
      }
      return next;
    });
    // Collapsing a row other than the URL's one leaves ?table= alone.
    if (isExpanded && tableParam !== fullName) return;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (isExpanded) next.delete("table");
        else next.set("table", fullName);
        return next;
      },
      { replace: true, state: { tableToggle: true } }
    );
  };

  const persistHiddenSchemas = (next) => {
    setScrollTarget(null);
    setHiddenSchemas(next);
    try {
      localStorage.setItem(hiddenSchemasKey(targetId), JSON.stringify([...next]));
    } catch {
      // Storage blocked — the filter just won't persist.
    }
  };

  const toggleSchemaHidden = (schemaName) => {
    const next = new Set(hiddenSchemas);
    if (next.has(schemaName)) next.delete(schemaName);
    else next.add(schemaName);
    persistHiddenSchemas(next);
  };

  const allSchemaNames = useMemo(() => grouped.map(([schemaName]) => schemaName), [grouped]);

  const visibleGrouped = useMemo(
    () => grouped.filter(([schemaName]) => !hiddenSchemas.has(schemaName)),
    [grouped, hiddenSchemas]
  );

  const visibleTableCount = useMemo(
    () => visibleGrouped.reduce((sum, [, schemaTables]) => sum + schemaTables.length, 0),
    [visibleGrouped]
  );

  return (
    <Card
      actions={
        <>
          {tables && (
            <span className={styles.meta}>
              {t("tableCount", { count: visibleTableCount })}
              {hiddenSchemas.size > 0 && ` (${t("schemasHidden", { count: hiddenSchemas.size })})`}
            </span>
          )}
          <CategoryFilterDropdown
            label={t("schemas")}
            items={allSchemaNames}
            hiddenItems={hiddenSchemas}
            onToggle={toggleSchemaHidden}
            onSelectAll={() => persistHiddenSchemas(new Set())}
            onSelectNone={() => persistHiddenSchemas(new Set(allSchemaNames))}
          />
        </>
      }
    >
      <div className={styles.deepScanBar}>
        <span className={styles.meta} title={t("deepScan.autoTitle")}>
          {deepScan
            ? deepScan.last_run_at
              ? `${t("deepScan.lastRun", { time: formatRelativeTime(deepScan.last_run_at) })}${
                  deepScan.last_run_status === "error"
                    ? ` (${t("deepScan.failed")})`
                    : ` · ${t("deepScan.openFindings", { count: deepScan.open_findings })}`
                }`
              : t("deepScan.neverRun")
            : t("deepScan.unknown")}
        </span>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={deepScanMutation.isPending}
          title={t("deepScan.runTitle")}
          onClick={() => deepScanMutation.mutate()}
        >
          {deepScanMutation.isPending ? t("deepScan.running") : t("deepScan.run")}
        </button>
      </div>
      {deepScanError && (
        <Alert tone="critical" compact>
          {deepScanError}
        </Alert>
      )}

      {tablesQuery.error && (
        <Alert tone="critical" compact>
          {tablesQuery.error.message}
        </Alert>
      )}
      {tablesQuery.isPending && <Skeleton lines={6} label={t("loadingTables")} />}
      {tables && tables.length === 0 && <EmptyState title={t("noTables")} />}
      {tables && tables.length > 0 && visibleGrouped.length === 0 && (
        <p className={styles.note}>{t("allSchemasHidden")}</p>
      )}
      {visibleGrouped.length > 0 && (
        <div className={tableStyles.wrap}>
          <table className={tableStyles.table}>
            <thead>
              <tr>
                {COLUMNS.map((col) =>
                  col.key ? (
                    <SortHeader
                      key={col.key}
                      column={col.key}
                      sortKey={sortKey}
                      sortDir={sortDir}
                      onSort={handleSort}
                      hint={<HeaderHint text={t(col.help)} />}
                    >
                      {t(col.label)}
                    </SortHeader>
                  ) : (
                    <th key="actions" scope="col" className={tableStyles.th}>
                      <span className="sr-only">{t(col.label)}</span>
                    </th>
                  )
                )}
              </tr>
            </thead>
            {visibleGrouped.map(([schemaName, schemaTables], groupIndex) => {
              const isCollapsed = collapsedSchemas?.has(schemaName) ?? false;
              const worstInSchema = schemaTables.reduce(
                (worst, t) => ((SEVERITY_RANK[worstSeverity(t)] ?? 0) >= (SEVERITY_RANK[worst] ?? 0) ? worstSeverity(t) : worst),
                "healthy"
              );
              const groupId = `${idPrefix}-schema-${groupIndex}`;
              return (
                // One <tbody> for the schema's header row and one for its
                // tables, so the toggle's aria-controls has a region to name.
                <SchemaGroup
                  key={schemaName}
                  schemaName={schemaName}
                  tableCount={schemaTables.length}
                  severity={worstInSchema}
                  isCollapsed={isCollapsed}
                  groupId={groupId}
                  onToggle={() => toggleSchema(schemaName, isCollapsed)}
                  action={
                    <SchemaMaintenanceActions
                      targetId={targetId}
                      schemaName={schemaName}
                      tableCount={schemaTables.length}
                      onFinished={refreshTables}
                    />
                  }
                >
                  {!isCollapsed &&
                    schemaTables.map((table) => {
                      const fullName = fullNameOf(table);
                      const expanded = expandedTables.has(fullName) || fullName === tableParam;
                      return (
                        <TableRow
                          // The screen remounts per target (App keys it on
                          // targetId), so schema.table alone is unique here —
                          // and the per-table findings live in the query cache
                          // under a target-scoped key, so a row can never show
                          // another database's detail.
                          key={fullName}
                          table={table}
                          targetId={targetId}
                          expanded={expanded}
                          onToggleExpand={() => toggleTable(fullName, expanded)}
                          scrollIntoView={scrollTarget === fullName}
                          onChanged={refreshTables}
                          firstSeenByFindingId={firstSeenByFindingId}
                        />
                      );
                    })}
                </SchemaGroup>
              );
            })}
          </table>
        </div>
      )}
    </Card>
  );
}

function SchemaGroup({ schemaName, tableCount, severity, isCollapsed, groupId, onToggle, action, children }) {
  const { t } = useTranslation("tableHealth");
  const flagged = severity === "critical" || severity === "attention";
  return (
    <>
      <tbody>
        <tr className={tableStyles.groupRow}>
          <td colSpan={COLUMNS.length} className={tableStyles.td}>
            <div className={styles.schemaHeader}>
              <button
                type="button"
                className={tableStyles.expander}
                aria-expanded={!isCollapsed}
                aria-controls={groupId}
                onClick={onToggle}
              >
                <ChevronRight size={14} className={tableStyles.chevron} aria-hidden="true" />
                <span className={`${styles.schemaName} ${flagged ? tone[severity] : ""}`}>{schemaName}</span>
                {flagged && <SeverityIcon severity={severity} size={13} />}
                <span className={styles.schemaCount}>{t("tableCount", { count: tableCount })}</span>
              </button>
              {action}
            </div>
          </td>
        </tr>
      </tbody>
      <tbody id={groupId}>{children}</tbody>
    </>
  );
}

const SCHEMA_JOB_POLL_MS = 1500;

// The two whole-schema operations. Their texts (label, confirm dialog,
// progress and toast) live under schemaOps.<operation> in the translations.
const SCHEMA_OPERATIONS = {
  analyze: { Icon: RefreshCw },
  vacuum: { Icon: Eraser },
};

// "Analyze all" / "Vacuum all" for one schema: confirms, starts the
// background job, shows progress while it runs, then reports what happened
// — including tables PostgreSQL skipped because the monitoring role may not
// process them — and refreshes the table list so the new numbers appear.
// One job per schema at a time: both buttons wait while either runs.
function SchemaMaintenanceActions({ targetId, schemaName, tableCount, onFinished }) {
  const { t } = useTranslation("tableHealth");
  const confirm = useConfirm();
  const toast = useToast();
  const [active, setActive] = useState(null); // { jobId, operation }
  const [startError, setStartError] = useState(null);
  const reportedJobs = useRef(new Set());

  // Reported from the poll itself (not an effect), once, when the job stops.
  const report = (job) => {
    if (reportedJobs.current.has(job.job_id)) return;
    reportedJobs.current.add(job.job_id);
    setActive(null);
    onFinished();
    const ns = `schemaOps.${job.operation}`;
    const parts = [t(`${ns}.done`, { count: job.total, completed: job.completed, schema: schemaName })];
    if (job.skipped.length > 0) {
      parts.push(t(`${ns}.skipped`, { count: job.skipped.length }));
    }
    if (job.failed.length > 0) {
      parts.push(
        t("schemaOps.failed", {
          count: job.failed.length,
          list: job.failed.map((f) => `${f.table} (${f.error})`).join("; "),
        })
      );
    }
    if (job.status === "error") parts.push(t("schemaOps.stoppedEarly", { error: job.error }));
    toast(parts.join(" "));
  };

  const jobQuery = useQuery({
    queryKey: targetKey(targetId, "schema-job", active?.jobId),
    queryFn: async ({ signal }) => {
      const data = await api.getSchemaJob(targetId, active.jobId, { signal });
      if (data.status !== "running") report(data);
      return data;
    },
    enabled: Boolean(active),
    refetchInterval: (query) => (query.state.data?.status === "running" ? SCHEMA_JOB_POLL_MS : false),
  });
  const job = active ? jobQuery.data : undefined;

  const start = async (operation) => {
    const ok = await confirm({
      title: t(`schemaOps.${operation}.confirmTitle`, { schema: schemaName }),
      message: t(`schemaOps.${operation}.confirm`, { count: tableCount }),
      confirmLabel: t(`schemaOps.${operation}.label`),
    });
    if (!ok) return;
    setStartError(null);
    try {
      const started = await api.startSchemaMaintenance(targetId, operation, schemaName);
      setActive({ jobId: started.job_id, operation });
    } catch (err) {
      setStartError(err.message);
    }
  };

  return (
    <span className={styles.schemaAction}>
      {startError && (
        <span className={styles.schemaActionError} role="alert">
          {startError}
        </span>
      )}
      {Object.entries(SCHEMA_OPERATIONS).map(([operation, op]) => {
        const running = active?.operation === operation;
        return (
          <button
            key={operation}
            type="button"
            className="button-secondary button-sm"
            onClick={() => start(operation)}
            disabled={Boolean(active)}
            aria-live="polite"
          >
            <op.Icon size={12} aria-hidden="true" className={running ? styles.spin : undefined} />
            {running
              ? t(`schemaOps.${operation}.progress`, {
                  done: job ? Math.min(job.done + 1, job.total) : 1,
                  total: job?.total ?? tableCount,
                })
              : t(`schemaOps.${operation}.label`)}
          </button>
        );
      })}
    </span>
  );
}

function worstSeverity(table) {
  const candidates = [table.dead_pct_severity, table.wraparound_severity, table.cache_hit_pct_severity];
  return candidates.reduce((worst, s) => ((SEVERITY_RANK[s] ?? 0) >= (SEVERITY_RANK[worst] ?? 0) ? s : worst));
}

// A metric tinted by its severity, with the severity icon alongside so the
// status isn't carried by colour alone.
function SeverityValue({ severity, children }) {
  const flagged = severity === "critical" || severity === "attention";
  return (
    <span className={`${styles.severityValue} ${flagged ? tone[severity] : ""}`}>
      {children}
      {flagged && <SeverityIcon severity={severity} size={12} />}
    </span>
  );
}

const ACTION_SUCCESS_TIMEOUT_MS = 6000;

function TableRow({
  table,
  targetId,
  expanded,
  onToggleExpand,
  scrollIntoView,
  onChanged,
  firstSeenByFindingId,
}) {
  const { t } = useTranslation("tableHealth");
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const detailId = useId();
  const rowRef = useRef(null);
  const [busyVerb, setBusyVerb] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);
  const [archiveError, setArchiveError] = useState(null);
  const busy = busyVerb !== null;
  const severity = worstSeverity(table);
  const fullName = fullNameOf(table);
  const detailKey = findingsKey(targetId, table.schema_name, table.table_name);

  // Only fetched while the row is open; kept in the cache after collapsing,
  // so re-opening shows it straight away (refetching if it has gone stale).
  const findingsQuery = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) => api.getTableHealthFindings(targetId, table.schema_name, table.table_name, { signal }),
    enabled: expanded,
  });
  const detail = findingsQuery.data ?? null;
  const hasDetail = detail !== null;

  // Deep link (?table=): bring the row into view once it has rendered, and
  // again when its findings arrive and push the layout around.
  useEffect(() => {
    if (scrollIntoView) rowRef.current?.scrollIntoView({ block: "start" });
  }, [scrollIntoView, hasDetail]);

  // verb: "analyze" | "vacuum" | "resetStats" — also the row.<verb> translation keys.
  const runAction = async (verb, confirmOptions, fn) => {
    if (!(await confirm(confirmOptions))) return;
    const before = table;
    setBusyVerb(verb);
    setActionError(null);
    setActionSuccess(null);
    try {
      const result = await fn();
      const freshTables = await onChanged();
      // The table's suggestions (dead tuples, never analyzed, …) can change
      // with it — refetched if the row is open, marked stale otherwise.
      queryClient.invalidateQueries({ queryKey: detailKey });
      let message = result?.message || t(`row.${verb}.succeeded`);
      // Vacuum's whole point is reclaiming dead tuples — show the actual
      // before/after count rather than just "it ran," since a VACUUM that
      // completes without error can still leave dead_tup unchanged when
      // something else (a long-running/idle-in-transaction session
      // elsewhere, an inactive replication slot) is holding back the
      // cleanup horizon. That's a real Postgres condition, not a bug in
      // this screen — but "it ran, nothing changed" is exactly what a user
      // needs to see to go looking for that cause instead of assuming the
      // click did nothing.
      if (verb === "vacuum" && freshTables) {
        const after = freshTables.find(
          (row) => row.schema_name === before.schema_name && row.table_name === before.table_name
        );
        if (after) {
          message +=
            after.dead_tuples === before.dead_tuples
              ? ` ${t("row.deadTuplesUnchanged", { dead: before.dead_tuples.toLocaleString() })}`
              : ` ${t("row.deadTuplesChanged", {
                  before: before.dead_tuples.toLocaleString(),
                  after: after.dead_tuples.toLocaleString(),
                })}`;
        }
      } else if (verb === "analyze") {
        // Analyze has no visible row change to point at (it refreshes
        // planner statistics, not row counts) — the plain "it ran" message
        // is easy to read as "nothing happened" without something concrete
        // to anchor it, so name what actually changed.
        message += ` ${t("row.statsRefreshed")}`;
      }
      setActionSuccess(message);
      setTimeout(() => setActionSuccess(null), ACTION_SUCCESS_TIMEOUT_MS);
    } catch (err) {
      setActionError(t(`row.${verb}.failed`, { error: err.message }));
    } finally {
      setBusyVerb(null);
    }
  };

  const removeFinding = (findingId) =>
    queryClient.setQueryData(detailKey, (prev) =>
      prev ? { ...prev, findings: prev.findings.filter((f) => f.id !== findingId) } : prev
    );

  const handleArchive = (finding) => {
    setArchiveError(null);
    api
      .archiveFinding(targetId, finding)
      .then(() => {
        removeFinding(finding.id);
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "archived-findings") });
      })
      .catch((err) => setArchiveError(err.message));
  };

  // FindingCard shows its own confirmation and any error from the returned
  // promise. On success the finding drops out straight away; the list and
  // this table's findings are then refetched for the server's view.
  const handleApply = (finding) =>
    api.applyTableHealthFinding(targetId, table.schema_name, table.table_name, finding.id).then(() => {
      removeFinding(finding.id);
      queryClient.invalidateQueries({ queryKey: tablesKey(targetId) });
      queryClient.invalidateQueries({ queryKey: detailKey });
    });

  const findingsError = findingsQuery.error?.message ?? archiveError;
  const rowClass = severity === "critical" ? styles.rowCritical : severity === "attention" ? styles.rowAttention : "";

  return (
    <>
      <tr ref={rowRef} className={`${tableStyles.row} ${rowClass} ${styles.scrollAnchor}`}>
        <td className={`${tableStyles.td} ${tableStyles.mono}`}>
          <button
            type="button"
            className={tableStyles.expander}
            aria-expanded={expanded}
            aria-controls={detailId}
            title={t("row.showSuggestions")}
            onClick={onToggleExpand}
          >
            <ChevronRight size={14} className={tableStyles.chevron} aria-hidden="true" />
            {table.table_name}
          </button>
        </td>
        <td
          className={`${tableStyles.td} ${tableStyles.mono} ${styles.secondary}`}
          title={table.live_tuples_estimated ? t(ESTIMATED_ROWS_HINT) : undefined}
        >
          {table.live_tuples_estimated && (
            <span className={styles.estimate} aria-hidden="true">
              ≈
            </span>
          )}
          {table.live_tuples.toLocaleString()} / {table.dead_tuples.toLocaleString()}
          {table.live_tuples_estimated && <span className="sr-only"> ({t(ESTIMATED_ROWS_HINT)})</span>}
        </td>
        <td className={tableStyles.td}>
          <SeverityValue severity={table.dead_pct_severity}>{table.dead_pct.toFixed(1)}%</SeverityValue>
        </td>
        <td className={`${tableStyles.td} ${styles.secondary}`}>{formatRelativeTime(table.last_vacuum_at)}</td>
        <td className={`${tableStyles.td} ${styles.secondary}`}>{formatRelativeTime(table.last_analyze_at)}</td>
        <td className={tableStyles.td}>
          <SeverityValue severity={table.wraparound_severity}>{table.xid_age.toLocaleString()}</SeverityValue>
        </td>
        <td className={tableStyles.td}>
          <SeverityValue severity={table.cache_hit_pct_severity}>
            {table.cache_hit_pct != null ? `${table.cache_hit_pct.toFixed(1)}%` : "—"}
          </SeverityValue>
        </td>
        <td className={`${tableStyles.td} ${styles.secondary}`}>
          {table.total_bytes_as_of ? (
            <>
              {formatBytes(table.total_bytes)}
              <div className={styles.asOf}>{t("row.asOf", { time: formatRelativeTime(table.total_bytes_as_of) })}</div>
            </>
          ) : (
            t("row.pending")
          )}
        </td>
        <td className={tableStyles.td}>
          <div className={styles.actions}>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title={t("row.analyze.title")}
              onClick={() =>
                runAction(
                  "analyze",
                  {
                    title: t("row.analyze.confirmTitle", { name: fullName }),
                    message: t("row.analyze.confirmMessage"),
                    confirmLabel: t("row.analyze.label"),
                  },
                  () => api.analyzeTable(targetId, table.schema_name, table.table_name)
                )
              }
            >
              {busyVerb === "analyze" ? t("row.analyze.busy") : t("row.analyze.label")}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title={t("row.vacuum.title")}
              onClick={() =>
                runAction(
                  "vacuum",
                  {
                    title: t("row.vacuum.confirmTitle", { name: fullName }),
                    message: t("row.vacuum.confirmMessage"),
                    confirmLabel: t("row.vacuum.label"),
                    tone: "danger",
                  },
                  () => api.vacuumTable(targetId, table.schema_name, table.table_name, true)
                )
              }
            >
              {busyVerb === "vacuum" ? t("row.vacuum.busy") : t("row.vacuum.label")}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title={t("row.resetStats.title")}
              onClick={() =>
                runAction(
                  "resetStats",
                  {
                    title: t("row.resetStats.confirmTitle", { name: fullName }),
                    message: t("row.resetStats.confirmMessage"),
                    confirmLabel: t("row.resetStats.label"),
                    tone: "danger",
                  },
                  () => api.resetTableStats(targetId, table.schema_name, table.table_name)
                )
              }
            >
              {busyVerb === "resetStats" ? t("row.resetStats.busy") : t("row.resetStats.label")}
            </button>
          </div>
        </td>
      </tr>
      {actionError && (
        <tr>
          <td colSpan={COLUMNS.length} className={styles.messageCell}>
            <Alert tone="critical" compact>
              {actionError}
            </Alert>
          </td>
        </tr>
      )}
      {actionSuccess && (
        <tr>
          <td colSpan={COLUMNS.length} className={styles.messageCell}>
            <Alert tone="healthy" compact>
              {actionSuccess}
            </Alert>
          </td>
        </tr>
      )}
      {expanded && (
        <tr id={detailId}>
          <td colSpan={COLUMNS.length} className={styles.detailCell}>
            {findingsQuery.isPending && <Skeleton lines={4} label={t("row.loadingDetails", { name: fullName })} />}
            {findingsError && (
              <Alert tone="critical" compact>
                {findingsError}
              </Alert>
            )}
            {detail && (
              <TableHealthDetail
                detail={detail}
                onArchive={handleArchive}
                onApply={handleApply}
                firstSeenByFindingId={firstSeenByFindingId}
              />
            )}
          </td>
        </tr>
      )}
    </>
  );
}
