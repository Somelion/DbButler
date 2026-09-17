import { useEffect, useState } from "react";
import { api } from "./api";
import CategoryFilterDropdown from "./CategoryFilterDropdown";
import FindingCard from "./FindingCard";
import CollapsibleGroup from "./CollapsibleGroup";
import { useFirstSeenMap } from "./useFirstSeenMap";

// Categories are a fixed taxonomy per tab (GROUPS_BY_TAB below), the same
// for every target, so the hidden-category preference is persisted per tab
// only — not per target like Table Health's hidden schemas, which really do
// vary target to target.
const hiddenCategoriesKey = (tab) => `pgdba.advisor.hiddenCategories.${tab}`;

function loadHiddenCategories(tab) {
  try {
    const raw = localStorage.getItem(hiddenCategoriesKey(tab));
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed) : new Set();
  } catch {
    return new Set();
  }
}

// Which top-level tabs (Index Advisor, Schema Lint, ...) to show. Same
// reasoning as hidden categories — the tab list is fixed, not per-target —
// but there's only one such list, so a single key covers it.
const HIDDEN_TABS_KEY = "pgdba.advisor.hiddenTabs";

function loadHiddenTabs() {
  try {
    const raw = localStorage.getItem(HIDDEN_TABS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed) : new Set();
  } catch {
    return new Set();
  }
}

const TABS = [
  { key: "index", label: "Index Advisor" },
  { key: "schema", label: "Schema Lint" },
  { key: "config", label: "Configuration Advisor" },
  { key: "replication", label: "Replication Advisor" },
  { key: "backup", label: "Backup & WAL Health" },
  { key: "pre-upgrade", label: "Pre-Upgrade Scan" },
  { key: "security", label: "Security & Compliance" },
  { key: "extensions", label: "Extensions" },
];

// Maps a finding's id prefix to the collapsible group it belongs under —
// finer-grained than the "index advisor"/"schema lint"/"configuration
// advisor" category that already splits the three tabs, so each specific
// check gets its own expandable group.
const INDEX_GROUPS = [
  { prefix: "missing-fk-index-", label: "Missing FK Index" },
  { prefix: "unused-index-", label: "Unused Indexes" },
  { prefix: "duplicate-index-", label: "Duplicate Indexes" },
  { prefix: "redundant-index-", label: "Redundant Indexes" },
  { prefix: "invalid-index-", label: "Invalid Indexes" },
  { prefix: "seq-scan-heavy-", label: "Seq-Scan-Heavy" },
  { prefix: "seq-scan-no-index-", label: "Seq Scan — No Index" },
  { prefix: "over-indexed-", label: "Over-Indexed Tables" },
  { prefix: "low-cardinality-index-", label: "Low-Cardinality Indexes" },
  { prefix: "covering-index-", label: "Covering Index Candidates" },
];

const SCHEMA_GROUPS = [
  { prefix: "float-money-", label: "Bad Data Types" },
  { prefix: "varchar255-", label: "Bad Data Types" },
  { prefix: "char-type-", label: "Bad Data Types" },
  { prefix: "legacy-serial-", label: "Bad Data Types" },
  { prefix: "blob-in-db-", label: "Bad Data Types" },
  { prefix: "boolean-as-int-", label: "Bad Data Types" },
  { prefix: "naive-timestamp-", label: "Bad Data Types" },
  { prefix: "missing-partitioning-", label: "Missing Partitioning" },
  { prefix: "uuid-fragmentation-", label: "UUID Fragmentation" },
  { prefix: "missing-primary-key-", label: "Missing Primary Key" },
  { prefix: "sequence-exhaustion-", label: "Sequence Exhaustion Risk" },
  { prefix: "jsonb-overuse-", label: "JSONB Overuse" },
  { prefix: "fk-type-mismatch-", label: "FK Type Mismatch" },
  { prefix: "select-star", label: "Risky Query Patterns" },
  { prefix: "not-in-subquery", label: "Risky Query Patterns" },
  { prefix: "deep-offset-pagination", label: "Risky Query Patterns" },
  { prefix: "function-wrapped-column", label: "Risky Query Patterns" },
  { prefix: "disk-spill", label: "Risky Query Patterns" },
  { prefix: "n-plus-one-signature", label: "N+1 Query Signature" },
  { prefix: "unlogged-table-", label: "Unlogged Tables" },
  { prefix: "unvalidated-constraint-", label: "Unvalidated Constraints" },
];

const CONFIG_GROUPS = [
  { prefix: "autovacuum-disabled-", label: "Autovacuum Disabled" },
  { prefix: "fsync-disabled", label: "Durability Settings" },
  { prefix: "synchronous-commit-off", label: "Durability Settings" },
  { prefix: "full-page-writes-off", label: "Durability Settings" },
  { prefix: "data-checksums-disabled", label: "Durability Settings" },
  { prefix: "random-page-cost-", label: "Planner Cost Settings" },
  { prefix: "pg-stat-statements-eviction", label: "pg_stat_statements Coverage" },
  { prefix: "version-eol-", label: "PostgreSQL Version Support" },
];

const REPLICATION_GROUPS = [
  { prefix: "replication-lag-", label: "Replication Lag" },
  { prefix: "inactive-replication-slot-", label: "Inactive Replication Slots" },
  { prefix: "subscription-apply-worker-down-", label: "Logical Replication Subscriptions" },
];

const BACKUP_GROUPS = [
  { prefix: "wal-archiving-not-configured", label: "WAL Archiving" },
  { prefix: "wal-archiving-failures", label: "WAL Archiving" },
  { prefix: "wal-directory-oversized", label: "WAL Directory Size" },
];

const PRE_UPGRADE_GROUPS = [
  { prefix: "pre-upgrade-removed-setting-", label: "Removed or Renamed Settings" },
  { prefix: "pre-upgrade-reg-type-", label: "reg* Type Columns" },
];

const SECURITY_GROUPS = [
  { prefix: "audit-logging-gap", label: "Audit Logging" },
  { prefix: "ssl-disabled", label: "Encryption in Transit" },
  { prefix: "excess-superusers", label: "Least Privilege" },
  { prefix: "public-grant-", label: "PUBLIC Grants" },
  { prefix: "rls-policy-not-enforced-", label: "Row-Level Security" },
  { prefix: "rls-enabled-no-policy-", label: "Row-Level Security" },
  { prefix: "security-definer-search-path-", label: "SECURITY DEFINER Search Path" },
];

const EXTENSION_GROUPS = [
  { prefix: "timescaledb-uncompressed-", label: "TimescaleDB Compression" },
  { prefix: "pgvector-unindexed-", label: "pgvector Indexing" },
  { prefix: "postgis-unindexed-", label: "PostGIS Indexing" },
];

const GROUPS_BY_TAB = {
  index: INDEX_GROUPS,
  schema: SCHEMA_GROUPS,
  config: CONFIG_GROUPS,
  replication: REPLICATION_GROUPS,
  backup: BACKUP_GROUPS,
  "pre-upgrade": PRE_UPGRADE_GROUPS,
  security: SECURITY_GROUPS,
  extensions: EXTENSION_GROUPS,
};

const EMPTY_MESSAGE_BY_TAB = {
  index: "No missing, unused, duplicate, invalid, or redundant indexes found.",
  schema: "No schema or query anti-patterns found.",
  config: "No configuration anti-patterns, durability risks, or version-support gaps found.",
  replication:
    "No replication activity, replication slots, or logical subscription issues detected on this target.",
  backup: "No WAL archiving or WAL directory growth issues detected on this target.",
  "pre-upgrade": "No known pg_upgrade blockers or removed/renamed settings detected on this target.",
  security:
    "No audit logging, encryption, least-privilege, PUBLIC grant, row-level security, or SECURITY DEFINER search_path gaps detected on this target.",
  extensions:
    "No TimescaleDB, pgvector, or PostGIS extensions detected on this target, or no issues found in the ones that are installed.",
};

function groupFindings(findings, groupDefs) {
  const order = [];
  const byLabel = new Map();
  for (const finding of findings) {
    const match = groupDefs.find((g) => finding.id.startsWith(g.prefix));
    const label = match ? match.label : "Other";
    if (!byLabel.has(label)) {
      byLabel.set(label, []);
      order.push(label);
    }
    byLabel.get(label).push(finding);
  }
  return order.map((label) => ({ label, findings: byLabel.get(label) }));
}

export default function Advisor({ targetId, initialTab }) {
  // Lazy init only — a caller that navigates here with a specific tab in
  // mind (e.g. the Dashboard's Replication tile) only gets one shot at it,
  // right when this component mounts; manually switching tabs afterward
  // should behave completely normally, not keep snapping back.
  const [tab, setTab] = useState(() => initialTab || "index");
  const [findingsByTab, setFindingsByTab] = useState({
    index: null,
    schema: null,
    config: null,
    replication: null,
    backup: null,
    "pre-upgrade": null,
    security: null,
    extensions: null,
  });
  const [error, setError] = useState(null);
  const [firstSeenByFindingId] = useFirstSeenMap(targetId);
  const [hiddenCategories, setHiddenCategories] = useState(() => loadHiddenCategories(tab));
  const [hiddenTabs, setHiddenTabs] = useState(() => loadHiddenTabs());

  useEffect(() => {
    setHiddenCategories(loadHiddenCategories(tab));
  }, [tab]);

  useEffect(() => {
    setError(null);
    Promise.all([
      api.getIndexAdvisor(targetId),
      api.getSchemaLint(targetId),
      api.getConfigAdvisor(targetId),
      api.getReplicationAdvisor(targetId),
      api.getBackupAdvisor(targetId),
      api.getPreUpgradeAdvisor(targetId),
      api.getSecurityAdvisor(targetId),
      api.getExtensionAdvisor(targetId),
    ])
      .then(
        ([
          indexData,
          schemaData,
          configData,
          replicationData,
          backupData,
          preUpgradeData,
          securityData,
          extensionData,
        ]) => {
          setFindingsByTab({
            index: indexData.findings,
            schema: schemaData.findings,
            config: configData.findings,
            replication: replicationData.findings,
            backup: backupData.findings,
            "pre-upgrade": preUpgradeData.findings,
            security: securityData.findings,
            extensions: extensionData.findings,
          });
        }
      )
      .catch((err) => setError(err.message));
  }, [targetId]);

  const tabLabels = TABS.map((t) => t.label);
  const visibleTabs = TABS.filter((t) => !hiddenTabs.has(t.label));

  useEffect(() => {
    if (visibleTabs.length > 0 && !visibleTabs.some((t) => t.key === tab)) {
      setTab(visibleTabs[0].key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hiddenTabs]);

  const activeFindings = findingsByTab[tab];
  const allGroups = activeFindings ? groupFindings(activeFindings, GROUPS_BY_TAB[tab]) : [];
  const categoryLabels = allGroups.map((g) => g.label);
  const visibleGroups = allGroups.filter((g) => !hiddenCategories.has(g.label));
  const visibleFindingCount = visibleGroups.reduce((sum, g) => sum + g.findings.length, 0);

  const persistHiddenCategories = (next) => {
    setHiddenCategories(next);
    localStorage.setItem(hiddenCategoriesKey(tab), JSON.stringify([...next]));
  };

  const toggleCategoryHidden = (label) => {
    const next = new Set(hiddenCategories);
    if (next.has(label)) next.delete(label);
    else next.add(label);
    persistHiddenCategories(next);
  };

  const persistHiddenTabs = (next) => {
    setHiddenTabs(next);
    localStorage.setItem(HIDDEN_TABS_KEY, JSON.stringify([...next]));
  };

  const toggleTabHidden = (label) => {
    const next = new Set(hiddenTabs);
    if (next.has(label)) next.delete(label);
    else next.add(label);
    persistHiddenTabs(next);
  };

  const handleArchive = (finding) => {
    api
      .archiveFinding(targetId, finding)
      .then(() =>
        setFindingsByTab((prev) => ({
          ...prev,
          [tab]: prev[tab].filter((f) => f.id !== finding.id),
        }))
      )
      .catch((err) => setError(err.message));
  };

  // Only Index Advisor findings are apply-able — Schema Lint/Configuration
  // Advisor's recommended_ddl (where present) is a data-type/setting change,
  // riskier to run unattended than a CREATE/DROP INDEX CONCURRENTLY.
  const handleApply = (finding) =>
    api.applyIndexFinding(targetId, finding.id).then(() =>
      setFindingsByTab((prev) => ({
        ...prev,
        index: prev.index.filter((f) => f.id !== finding.id),
      }))
    );

  // Covering Index Candidates' "Analyze with AI" panel can produce a
  // recommended_ddl the base finding didn't have (heuristic was
  // unconfident) — applied by referencing the stored analysis id, so the
  // server (not the client) is always the one supplying the actual DDL.
  const handleApplyAnalysis = (finding, analysisId) =>
    api.applyIndexCoverageAnalysis(targetId, analysisId).then(() =>
      setFindingsByTab((prev) => ({
        ...prev,
        index: prev.index.filter((f) => f.id !== finding.id),
      }))
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
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", padding: 3, background: "oklch(94% 0.004 250)", borderRadius: 9 }}>
          {visibleTabs.map((t) => (
            <span
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                padding: "6px 14px",
                fontSize: 12.5,
                fontWeight: tab === t.key ? 650 : 600,
                color: tab === t.key ? "white" : "var(--text-secondary)",
                background: tab === t.key ? "var(--accent)" : "transparent",
                borderRadius: 7,
                cursor: "pointer",
              }}
            >
              {t.label}
            </span>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginLeft: "auto" }}>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            {activeFindings &&
              `${visibleFindingCount} finding${visibleFindingCount === 1 ? "" : "s"}${
                hiddenCategories.size > 0 ? ` (${hiddenCategories.size} categor${hiddenCategories.size === 1 ? "y" : "ies"} hidden)` : ""
              }`}
          </span>
          <CategoryFilterDropdown
            label="Tabs"
            items={tabLabels}
            hiddenItems={hiddenTabs}
            onToggle={toggleTabHidden}
            onSelectAll={() => persistHiddenTabs(new Set())}
            onSelectNone={() => persistHiddenTabs(new Set(tabLabels))}
          />
          {visibleTabs.length > 0 && (
            <CategoryFilterDropdown
              label="Categories"
              items={categoryLabels}
              hiddenItems={hiddenCategories}
              onToggle={toggleCategoryHidden}
              onSelectAll={() => persistHiddenCategories(new Set())}
              onSelectNone={() => persistHiddenCategories(new Set(categoryLabels))}
            />
          )}
        </div>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {visibleTabs.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          All Advisor tabs are hidden — use the Tabs button above to show one.
        </span>
      )}

      {visibleTabs.length > 0 && activeFindings && activeFindings.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>{EMPTY_MESSAGE_BY_TAB[tab]}</span>
      )}

      {visibleTabs.length > 0 && activeFindings && activeFindings.length > 0 && visibleGroups.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          All categories are hidden — use the Categories button above to show some.
        </span>
      )}

      {visibleTabs.length > 0 && visibleGroups.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {visibleGroups.map((group) => (
            <CollapsibleGroup key={group.label} label={group.label} count={group.findings.length}>
              {group.findings.map((finding) => (
                <FindingCard
                  key={finding.id}
                  finding={finding}
                  targetId={tab === "index" ? targetId : undefined}
                  onArchive={() => handleArchive(finding)}
                  onApply={tab === "index" ? () => handleApply(finding) : undefined}
                  onApplyAnalysis={tab === "index" ? (analysisId) => handleApplyAnalysis(finding, analysisId) : undefined}
                  firstSeenAt={firstSeenByFindingId[finding.id]}
                />
              ))}
            </CollapsibleGroup>
          ))}
        </div>
      )}
    </div>
  );
}
