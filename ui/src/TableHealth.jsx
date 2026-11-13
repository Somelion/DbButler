import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, RefreshCw } from "lucide-react";
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
const ESTIMATED_ROWS_HINT =
  "Estimated from PostgreSQL's table statistics: the activity counters were reset (pg_stat_reset, a crash or a restore) " +
  "and the table hasn't been vacuumed or analyzed since. The dead count only covers changes since the reset. " +
  "Run ANALYZE on the table for exact numbers.";
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

const COLUMNS = [
  { key: "table_name", label: "Table", help: "Table name — grouped by schema below." },
  {
    key: "live_tuples",
    label: "Rows (live/dead)",
    help: "Estimated live row count and dead (deleted or updated but not yet reclaimed by VACUUM) row count for this table, from pg_stat_user_tables.",
  },
  {
    key: "dead_pct",
    label: "Dead %",
    help: "Percent of this table's rows that are dead (deleted or updated but not yet reclaimed by VACUUM). High values mean autovacuum is falling behind.",
  },
  {
    key: "last_vacuum_at",
    label: "Last vacuum",
    help: "When this table was last vacuumed (manual or automatic), reclaiming dead row space.",
  },
  {
    key: "last_analyze_at",
    label: "Last analyze",
    help: "When this table's query-planner statistics were last refreshed (manual or automatic).",
  },
  {
    key: "xid_age",
    label: "Wraparound age",
    help: "How many transactions old this table's oldest row is. Approaching the ~2 billion limit risks transaction ID wraparound — keep autovacuum running.",
  },
  {
    key: "cache_hit_pct",
    label: "Cache hit %",
    help: "Percent of this table's reads served from memory rather than disk (target: above 95%). A single hot table can run low even when the database-wide average on the Dashboard looks healthy.",
  },
  {
    key: "total_bytes",
    label: "Size",
    help: "Total on-disk size, including all of this table's indexes. Refreshed on the background table-metrics cycle (every 5 minutes by default), not computed live on every load — kept off the hot path since it's expensive to compute for every table on a large database.",
  },
  { key: "", label: "Actions" },
];

// ?sort= is validated against these; anything else falls back to the default.
const SORT_KEYS = new Set(COLUMNS.map((col) => col.key).filter(Boolean));
const DEFAULT_SORT_KEY = "dead_pct";

export default function TableHealth({ targetId }) {
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
              {visibleTableCount} table{visibleTableCount === 1 ? "" : "s"}
              {hiddenSchemas.size > 0 && ` (${hiddenSchemas.size} schema${hiddenSchemas.size === 1 ? "" : "s"} hidden)`}
            </span>
          )}
          <CategoryFilterDropdown
            label="Schemas"
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
        <span className={styles.meta} title="Also runs automatically every night — see Settings.">
          {deepScan
            ? deepScan.last_run_at
              ? `Deep scan: ${formatRelativeTime(deepScan.last_run_at)}${
                  deepScan.last_run_status === "error" ? " (failed)" : ` · ${deepScan.open_findings} open finding${deepScan.open_findings === 1 ? "" : "s"}`
                }`
              : "Deep scan: never run"
            : "Deep scan: —"}
        </span>
        <button
          type="button"
          className="button-secondary button-sm"
          disabled={deepScanMutation.isPending}
          title="Runs every table's advisor checks now, across the whole database, and records the results — the same thing that runs nightly (see Settings)."
          onClick={() => deepScanMutation.mutate()}
        >
          {deepScanMutation.isPending ? "Running…" : "Run Deep Scan Now"}
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
      {tablesQuery.isPending && <Skeleton lines={6} label="Loading tables…" />}
      {tables && tables.length === 0 && <EmptyState title="No user tables found." />}
      {tables && tables.length > 0 && visibleGrouped.length === 0 && (
        <p className={styles.note}>All schemas are hidden — use the Schemas button above to show some.</p>
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
                      hint={<HeaderHint text={col.help} />}
                    >
                      {col.label}
                    </SortHeader>
                  ) : (
                    <th key="actions" scope="col" className={tableStyles.th}>
                      <span className="sr-only">{col.label}</span>
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
                    <AnalyzeSchemaButton
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
                <span className={styles.schemaCount}>
                  {tableCount} table{tableCount === 1 ? "" : "s"}
                </span>
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

const ANALYZE_POLL_MS = 1500;

// "Analyze all" for one schema: confirms, starts the background job, shows
// progress while it runs, then reports what happened — including tables
// ANALYZE skipped because the monitoring role may not analyze them — and
// refreshes the table list so the new counts appear.
function AnalyzeSchemaButton({ targetId, schemaName, tableCount, onFinished }) {
  const confirm = useConfirm();
  const toast = useToast();
  const [jobId, setJobId] = useState(null);
  const [startError, setStartError] = useState(null);
  const reportedJobs = useRef(new Set());

  // Reported from the poll itself (not an effect), once, when the job stops.
  const report = (job) => {
    if (reportedJobs.current.has(job.job_id)) return;
    reportedJobs.current.add(job.job_id);
    setJobId(null);
    onFinished();
    const parts = [`Analyzed ${job.analyzed} of ${job.total} table${job.total === 1 ? "" : "s"} in ${schemaName}.`];
    if (job.skipped.length > 0) {
      parts.push(
        `${job.skipped.length} skipped: DbButler's role may not analyze ${job.skipped.length === 1 ? "it" : "them"} (needs table ownership, or pg_maintain on PostgreSQL 17+).`
      );
    }
    if (job.failed.length > 0) {
      parts.push(`${job.failed.length} failed: ${job.failed.map((f) => `${f.table} (${f.error})`).join("; ")}.`);
    }
    if (job.status === "error") parts.push(`Stopped early: ${job.error}`);
    toast(parts.join(" "));
  };

  const jobQuery = useQuery({
    queryKey: targetKey(targetId, "analyze-schema", jobId),
    queryFn: async ({ signal }) => {
      const data = await api.getAnalyzeSchemaJob(targetId, jobId, { signal });
      if (data.status !== "running") report(data);
      return data;
    },
    enabled: Boolean(jobId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? ANALYZE_POLL_MS : false),
  });
  const job = jobId ? jobQuery.data : undefined;
  const running = Boolean(jobId);

  const handleClick = async () => {
    const ok = await confirm({
      title: `Analyze all tables in ${schemaName}?`,
      message: `Runs ANALYZE on ${tableCount} table${tableCount === 1 ? "" : "s"}, one after another, so their row counts and planner statistics are fresh. ANALYZE reads a sample of each table; reads and writes carry on while it runs.`,
      confirmLabel: "Analyze all",
    });
    if (!ok) return;
    setStartError(null);
    try {
      const started = await api.analyzeSchema(targetId, schemaName);
      setJobId(started.job_id);
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
      <button
        type="button"
        className="button-secondary button-sm"
        onClick={handleClick}
        disabled={running}
        aria-live="polite"
      >
        <RefreshCw size={12} aria-hidden="true" className={running ? styles.spin : undefined} />
        {running
          ? `Analyzing ${job ? Math.min(job.done + 1, job.total) : 1}/${job?.total ?? tableCount}…`
          : "Analyze all"}
      </button>
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
      let message = result?.message || `${verb} succeeded.`;
      // Vacuum's whole point is reclaiming dead tuples — show the actual
      // before/after count rather than just "it ran," since a VACUUM that
      // completes without error can still leave dead_tup unchanged when
      // something else (a long-running/idle-in-transaction session
      // elsewhere, an inactive replication slot) is holding back the
      // cleanup horizon. That's a real Postgres condition, not a bug in
      // this screen — but "it ran, nothing changed" is exactly what a user
      // needs to see to go looking for that cause instead of assuming the
      // click did nothing.
      if (verb === "Vacuum" && freshTables) {
        const after = freshTables.find(
          (t) => t.schema_name === before.schema_name && t.table_name === before.table_name
        );
        if (after) {
          message +=
            after.dead_tuples === before.dead_tuples
              ? ` Dead tuples unchanged (${before.dead_tuples.toLocaleString()}) — VACUUM completed without error, but something else may be holding the rows back from actually being reclaimed: check Activity for a long-running or idle-in-transaction session, or Advisor → Replication Advisor for an inactive replication slot.`
              : ` Dead tuples: ${before.dead_tuples.toLocaleString()} → ${after.dead_tuples.toLocaleString()}.`;
        }
      } else if (verb === "Analyze") {
        // Analyze has no visible row change to point at (it refreshes
        // planner statistics, not row counts) — the plain "it ran" message
        // is easy to read as "nothing happened" without something concrete
        // to anchor it, so name what actually changed.
        message += " Query planner statistics refreshed for this table.";
      }
      setActionSuccess(message);
      setTimeout(() => setActionSuccess(null), ACTION_SUCCESS_TIMEOUT_MS);
    } catch (err) {
      setActionError(`${verb} failed: ${err.message}`);
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
            title="Show suggestions for this table"
            onClick={onToggleExpand}
          >
            <ChevronRight size={14} className={tableStyles.chevron} aria-hidden="true" />
            {table.table_name}
          </button>
        </td>
        <td
          className={`${tableStyles.td} ${tableStyles.mono} ${styles.secondary}`}
          title={table.live_tuples_estimated ? ESTIMATED_ROWS_HINT : undefined}
        >
          {table.live_tuples_estimated && (
            <span className={styles.estimate} aria-hidden="true">
              ≈
            </span>
          )}
          {table.live_tuples.toLocaleString()} / {table.dead_tuples.toLocaleString()}
          {table.live_tuples_estimated && <span className="sr-only"> ({ESTIMATED_ROWS_HINT})</span>}
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
              <div className={styles.asOf}>as of {formatRelativeTime(table.total_bytes_as_of)}</div>
            </>
          ) : (
            "pending"
          )}
        </td>
        <td className={tableStyles.td}>
          <div className={styles.actions}>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title="Refresh the query planner's statistics for this table. Fast, no meaningful lock — safe to run any time, e.g. after a bulk load."
              onClick={() =>
                runAction(
                  "Analyze",
                  {
                    title: `Analyze ${fullName}?`,
                    message:
                      "Refreshes the query planner's statistics for this table. Fast, no meaningful lock — safe to run any time.",
                    confirmLabel: "Analyze",
                  },
                  () => api.analyzeTable(targetId, table.schema_name, table.table_name)
                )
              }
            >
              {busyVerb === "Analyze" ? "Analyzing…" : "Analyze"}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title="Reclaim dead-row space and refresh statistics (VACUUM ANALYZE). Runs alongside normal reads/writes, but uses real I/O — avoid running on many large tables at once."
              onClick={() =>
                runAction(
                  "Vacuum",
                  {
                    title: `Vacuum (analyze) ${fullName}?`,
                    message:
                      "Runs VACUUM ANALYZE: reclaims dead-row space and refreshes statistics. Runs alongside normal reads/writes, but uses real I/O — avoid running on many large tables at once.",
                    confirmLabel: "Vacuum",
                    tone: "danger",
                  },
                  () => api.vacuumTable(targetId, table.schema_name, table.table_name, true)
                )
              }
            >
              {busyVerb === "Vacuum" ? "Vacuuming…" : "Vacuum"}
            </button>
            <button
              type="button"
              className="button-secondary button-sm"
              disabled={busy}
              title="Reset this table's cumulative counters (dead/live tuple counts, index scan counts) back to zero. Useful right after a manual Vacuum to see a clean baseline. Does not change any data."
              onClick={() =>
                runAction(
                  "Reset stats",
                  {
                    title: `Reset statistics for ${fullName}?`,
                    message:
                      "Resets this table's cumulative counters (dead/live tuple counts, index scan counts) to zero. Does not change any data.",
                    confirmLabel: "Reset stats",
                    tone: "danger",
                  },
                  () => api.resetTableStats(targetId, table.schema_name, table.table_name)
                )
              }
            >
              {busyVerb === "Reset stats" ? "Resetting…" : "Reset stats"}
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
            {findingsQuery.isPending && <Skeleton lines={4} label={`Loading details for ${fullName}…`} />}
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
