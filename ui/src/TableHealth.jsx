import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { formatBytes, formatRelativeTime } from "./format";
import HeaderHint from "./HeaderHint";
import TableHealthDetail from "./TableHealthDetail";
import CategoryFilterDropdown from "./CategoryFilterDropdown";
import { useFirstSeenMap } from "./useFirstSeenMap";

const POLL_INTERVAL_MS = 15000;
const SEVERITY_RANK = { critical: 2, attention: 1, healthy: 0 };

const hiddenSchemasKey = (targetId) => `pgdba.tableHealth.hiddenSchemas.${targetId}`;

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
  { key: "total_bytes", label: "Size", help: "Total on-disk size, including all of this table's indexes." },
  { key: "", label: "" },
];

export default function TableHealth({ targetId }) {
  const [tables, setTables] = useState([]);
  const [error, setError] = useState(null);
  const [sortKey, setSortKey] = useState("dead_pct");
  const [sortDir, setSortDir] = useState("desc");
  const [deepScan, setDeepScan] = useState(null);
  const [deepScanRunning, setDeepScanRunning] = useState(false);
  const [deepScanError, setDeepScanError] = useState(null);
  const [firstSeenByFindingId, refreshFirstSeen] = useFirstSeenMap(targetId);
  // null = not yet initialized. Defaults to "all collapsed" once we know
  // there's more than one schema (so you open only the one you're
  // interested in); a single-schema database just stays fully expanded,
  // matching this screen's pre-grouping behavior. Never reset after that,
  // so a later poll doesn't undo the user's own expand/collapse choices.
  const [collapsedSchemas, setCollapsedSchemas] = useState(null);
  const [hiddenSchemas, setHiddenSchemas] = useState(() => loadHiddenSchemas(targetId));

  const refresh = () =>
    api
      .getTableHealth(targetId)
      .then((data) => {
        const mapped = data.tables.map((t) => ({
          ...t,
          last_vacuum_at: mostRecent(t.last_vacuum, t.last_autovacuum),
          last_analyze_at: mostRecent(t.last_analyze, t.last_autoanalyze),
        }));
        setTables(mapped);
        // Only initializes once per target (skipped once non-null) — done
        // here, off the data that just arrived for THIS target, rather than
        // in a separate effect reacting to `tables`. Reacting separately
        // raced the target switch below: it fired off the previous target's
        // still-current `tables` before this fetch resolved, locked in a
        // collapse set keyed to the old target's schema names, and then
        // never got a chance to recompute for the new one.
        setCollapsedSchemas((prev) => {
          if (prev !== null) return prev;
          const schemaNames = [...new Set(mapped.map((t) => t.schema_name))];
          return schemaNames.length > 1 ? new Set(schemaNames) : new Set();
        });
        setError(null);
      })
      .catch((err) => setError(err.message));

  const refreshDeepScanStatus = () =>
    api
      .getTableHealthDeepScanStatus(targetId)
      .then((data) => setDeepScan(data))
      .catch((err) => setDeepScanError(err.message));

  useEffect(() => {
    // A different target has a different schema list entirely — re-derive
    // the default expand/collapse state for it rather than carrying over
    // the previous target's choices (still never reset on same-target
    // polling, per the comment above).
    setCollapsedSchemas(null);
    setHiddenSchemas(loadHiddenSchemas(targetId));
    refresh();
    refreshDeepScanStatus();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId]);

  const handleRunDeepScan = () => {
    setDeepScanRunning(true);
    setDeepScanError(null);
    api
      .runTableHealthDeepScan(targetId)
      .then((data) => {
        setDeepScan(data);
        refreshFirstSeen();
      })
      .catch((err) => setDeepScanError(err.message))
      .finally(() => setDeepScanRunning(false));
  };

  const sorted = useMemo(() => {
    const copy = [...tables];
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
    if (!key) return;
    if (key === sortKey) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
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

  const toggleSchema = (schemaName) => {
    setCollapsedSchemas((prev) => {
      const next = new Set(prev);
      if (next.has(schemaName)) next.delete(schemaName);
      else next.add(schemaName);
      return next;
    });
  };

  const persistHiddenSchemas = (next) => {
    setHiddenSchemas(next);
    localStorage.setItem(hiddenSchemasKey(targetId), JSON.stringify([...next]));
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Table Health</span>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            {visibleTableCount} table{visibleTableCount === 1 ? "" : "s"}
            {hiddenSchemas.size > 0 && ` (${hiddenSchemas.size} schema${hiddenSchemas.size === 1 ? "" : "s"} hidden)`}
          </span>
          <CategoryFilterDropdown
            label="Schemas"
            items={allSchemaNames}
            hiddenItems={hiddenSchemas}
            onToggle={toggleSchemaHidden}
            onSelectAll={() => persistHiddenSchemas(new Set())}
            onSelectNone={() => persistHiddenSchemas(new Set(allSchemaNames))}
          />
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 10,
          padding: "8px 12px",
          background: "var(--row-bg)",
          borderRadius: 9,
        }}
      >
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }} title="Also runs automatically every night — see Settings.">
          {deepScan
            ? deepScan.last_run_at
              ? `Deep scan: ${formatRelativeTime(deepScan.last_run_at)}${
                  deepScan.last_run_status === "error" ? " (failed)" : ` · ${deepScan.open_findings} open finding${deepScan.open_findings === 1 ? "" : "s"}`
                }`
              : "Deep scan: never run"
            : "Deep scan: —"}
        </span>
        <button
          className="button-secondary"
          disabled={deepScanRunning}
          title="Runs every table's advisor checks now, across the whole database, and records the results — the same thing that runs nightly (see Settings)."
          style={{ padding: "4px 10px", fontSize: 11 }}
          onClick={handleRunDeepScan}
        >
          {deepScanRunning ? "Running…" : "Run Deep Scan Now"}
        </button>
      </div>
      {deepScanError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{deepScanError}</span>}

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
      {tables.length === 0 && !error && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No user tables found.</span>
      )}
      {tables.length > 0 && visibleGrouped.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          All schemas are hidden — use the Schemas button above to show some.
        </span>
      )}
      {visibleGrouped.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th
                    key={col.key || "actions"}
                    onClick={() => handleSort(col.key)}
                    style={{
                      textAlign: "left",
                      padding: "6px 10px",
                      fontSize: 10.5,
                      fontWeight: 700,
                      textTransform: "uppercase",
                      letterSpacing: "0.02em",
                      color: "var(--text-muted)",
                      cursor: col.key ? "pointer" : "default",
                      userSelect: "none",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {col.label}
                    <HeaderHint text={col.help} />
                    {sortKey === col.key && col.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleGrouped.map(([schemaName, schemaTables]) => {
                const isCollapsed = collapsedSchemas?.has(schemaName) ?? false;
                const worstInSchema = schemaTables.reduce(
                  (worst, t) => ((SEVERITY_RANK[worstSeverity(t)] ?? 0) >= (SEVERITY_RANK[worst] ?? 0) ? worstSeverity(t) : worst),
                  "healthy"
                );
                return (
                  <Fragment key={schemaName}>
                    <tr onClick={() => toggleSchema(schemaName)} style={{ cursor: "pointer", userSelect: "none", background: "var(--row-bg)" }}>
                      <td colSpan={COLUMNS.length} style={{ padding: "7px 10px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ display: "inline-block", width: 10, fontSize: 10, color: "var(--text-muted)" }}>
                            {isCollapsed ? "▸" : "▾"}
                          </span>
                          <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 12.5, color: severityColor(worstInSchema) }}>
                            {schemaName}
                          </span>
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                            {schemaTables.length} table{schemaTables.length === 1 ? "" : "s"}
                          </span>
                        </div>
                      </td>
                    </tr>
                    {!isCollapsed &&
                      schemaTables.map((table) => (
                        <TableRow
                          // targetId is part of the key, not just a prop —
                          // otherwise two different databases that happen to
                          // share a schema.table name would make React reuse
                          // the same row instance across a target switch,
                          // carrying over its expanded/detail state from the
                          // OLD database into a row now rendering the NEW
                          // one's table (TableHealthDetail assumes detail's
                          // shape without guarding against that mismatch).
                          key={`${targetId}.${table.schema_name}.${table.table_name}`}
                          table={table}
                          targetId={targetId}
                          onChanged={refresh}
                          firstSeenByFindingId={firstSeenByFindingId}
                        />
                      ))}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function worstSeverity(table) {
  const candidates = [table.dead_pct_severity, table.wraparound_severity, table.cache_hit_pct_severity];
  return candidates.reduce((worst, s) => ((SEVERITY_RANK[s] ?? 0) >= (SEVERITY_RANK[worst] ?? 0) ? s : worst));
}

function severityColor(severity) {
  if (severity === "critical") return "var(--critical-text)";
  if (severity === "attention") return "var(--attention-text)";
  return "var(--text)";
}

const ACTION_SUCCESS_TIMEOUT_MS = 6000;

function TableRow({ table, targetId, onChanged, firstSeenByFindingId }) {
  const [busyVerb, setBusyVerb] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState(null);
  const [findingsError, setFindingsError] = useState(null);
  const [findingsLoading, setFindingsLoading] = useState(false);
  const busy = busyVerb !== null;
  const severity = worstSeverity(table);
  const rowBg = severity === "critical" ? "var(--critical-bg)" : severity === "attention" ? "var(--attention-bg)" : "transparent";
  const fullName = `${table.schema_name}.${table.table_name}`;

  const runAction = async (label, verb, fn) => {
    if (!window.confirm(`${label} ${fullName}?`)) return;
    setBusyVerb(verb);
    setActionError(null);
    setActionSuccess(null);
    try {
      const result = await fn();
      await onChanged();
      setActionSuccess(result?.message || `${verb} succeeded.`);
      setTimeout(() => setActionSuccess(null), ACTION_SUCCESS_TIMEOUT_MS);
    } catch (err) {
      setActionError(`${verb} failed: ${err.message}`);
    } finally {
      setBusyVerb(null);
    }
  };

  const loadFindings = () => {
    setFindingsLoading(true);
    setFindingsError(null);
    api
      .getTableHealthFindings(targetId, table.schema_name, table.table_name)
      .then((data) => setDetail(data))
      .catch((err) => setFindingsError(err.message))
      .finally(() => setFindingsLoading(false));
  };

  const handleToggleExpand = () => {
    setExpanded((prev) => {
      const next = !prev;
      if (next && detail === null) loadFindings();
      return next;
    });
  };

  const removeFinding = (findingId) =>
    setDetail((prev) => ({ ...prev, findings: prev.findings.filter((f) => f.id !== findingId) }));

  const handleArchive = (finding) => {
    api
      .archiveFinding(targetId, finding)
      .then(() => removeFinding(finding.id))
      .catch((err) => setFindingsError(err.message));
  };

  const handleApply = (finding) =>
    api
      .applyTableHealthFinding(targetId, table.schema_name, table.table_name, finding.id)
      .then(() => removeFinding(finding.id));

  return (
    <>
      <tr style={{ background: rowBg }}>
        <td
          onClick={handleToggleExpand}
          style={{
            padding: "8px 10px",
            fontFamily: "IBM Plex Mono, monospace",
            color: "var(--text)",
            cursor: "pointer",
            userSelect: "none",
          }}
          title="Show suggestions for this table"
        >
          <span style={{ display: "inline-block", width: 12, color: "var(--text-muted)" }}>
            {expanded ? "▾" : "▸"}
          </span>
          {table.table_name}
        </td>
        <td style={{ padding: "8px 10px", color: "var(--text-secondary)", fontFamily: "IBM Plex Mono, monospace" }}>
          {table.live_tuples.toLocaleString()} / {table.dead_tuples.toLocaleString()}
        </td>
        <td style={{ padding: "8px 10px", fontWeight: 650, color: severityColor(table.dead_pct_severity) }}>
          {table.dead_pct.toFixed(1)}%
        </td>
        <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{formatRelativeTime(table.last_vacuum_at)}</td>
        <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{formatRelativeTime(table.last_analyze_at)}</td>
        <td style={{ padding: "8px 10px", fontWeight: 650, color: severityColor(table.wraparound_severity) }}>
          {table.xid_age.toLocaleString()}
        </td>
        <td style={{ padding: "8px 10px", fontWeight: 650, color: severityColor(table.cache_hit_pct_severity) }}>
          {table.cache_hit_pct != null ? `${table.cache_hit_pct.toFixed(1)}%` : "—"}
        </td>
        <td style={{ padding: "8px 10px", color: "var(--text-secondary)" }}>{formatBytes(table.total_bytes)}</td>
        <td style={{ padding: "8px 10px" }}>
          <div style={{ display: "flex", gap: 4 }}>
            <button
              className="button-secondary"
              disabled={busy}
              title="Refresh the query planner's statistics for this table. Fast, no meaningful lock — safe to run any time, e.g. after a bulk load."
              style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={() =>
                runAction("Analyze", "Analyze", () => api.analyzeTable(targetId, table.schema_name, table.table_name))
              }
            >
              {busyVerb === "Analyze" ? "Analyzing…" : "Analyze"}
            </button>
            <button
              className="button-secondary"
              disabled={busy}
              title="Reclaim dead-row space and refresh statistics (VACUUM ANALYZE). Runs alongside normal reads/writes, but uses real I/O — avoid running on many large tables at once."
              style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={() =>
                runAction("Vacuum (analyze)", "Vacuum", () =>
                  api.vacuumTable(targetId, table.schema_name, table.table_name, true)
                )
              }
            >
              {busyVerb === "Vacuum" ? "Vacuuming…" : "Vacuum"}
            </button>
            <button
              className="button-secondary"
              disabled={busy}
              title="Reset this table's cumulative counters (dead/live tuple counts, index scan counts) back to zero. Useful right after a manual Vacuum to see a clean baseline. Does not change any data."
              style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={() =>
                runAction("Reset statistics for", "Reset stats", () =>
                  api.resetTableStats(targetId, table.schema_name, table.table_name)
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
          <td colSpan={COLUMNS.length} style={{ padding: "4px 10px", fontSize: 11, color: "var(--critical-text)" }}>
            {actionError}
          </td>
        </tr>
      )}
      {actionSuccess && (
        <tr>
          <td colSpan={COLUMNS.length} style={{ padding: "4px 10px", fontSize: 11, color: "var(--healthy-text)" }}>
            {actionSuccess}
          </td>
        </tr>
      )}
      {expanded && (
        <tr>
          <td colSpan={COLUMNS.length} style={{ padding: "14px", background: "var(--row-bg)" }}>
            {findingsLoading && <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Loading…</span>}
            {findingsError && <span style={{ fontSize: 12, color: "var(--critical-text)" }}>{findingsError}</span>}
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
