import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import styles from "./Advisor.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { screenPath } from "./routes";
import { Alert, Card, EmptyState, Skeleton, TabPanel, Tabs } from "./components";
import CategoryFilterDropdown from "./CategoryFilterDropdown";
import FindingCard from "./FindingCard";
import CollapsibleGroup from "./CollapsibleGroup";
import { useFirstSeenMap } from "./useFirstSeenMap";

// All three show/hide preferences below are stored as a JSON array under
// one localStorage key each. A missing, corrupt or blocked entry reads as
// "nothing hidden"; a blocked write just means the preference won't persist.
function loadStoredSet(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed) : new Set();
  } catch {
    return new Set();
  }
}

function saveStoredSet(key, set) {
  try {
    localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    // Storage blocked — the preference just won't persist.
  }
}

// Categories are a fixed taxonomy per tab (GROUPS_BY_TAB below), the same
// for every target, so the hidden-category preference is persisted per tab
// only — not per target like Table Health's hidden schemas, which really do
// vary target to target.
const hiddenCategoriesKey = (tab) => `pgdba.advisor.hiddenCategories.${tab}`;

// Which top-level tabs (Index Advisor, Schema Lint, ...) to show. Same
// reasoning as hidden categories — the tab list is fixed, not per-target —
// but there's only one such list, so a single key covers it.
const HIDDEN_TABS_KEY = "pgdba.advisor.hiddenTabs";

// Per-target, like Table Health's own hidden-schemas filter (schemas really
// do vary target to target, unlike the fixed tab/category taxonomy above) —
// a further, personal client-side narrowing on top of whatever the
// connection's own allowed_schemas allowlist (Connections screen) already
// limited the server to computing in the first place.
const hiddenSchemasKey = (targetId) => `pgdba.advisor.hiddenSchemas.${targetId}`;

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

const DEFAULT_TAB = "index";

// One endpoint per tab. Each is its own query (advisorKey below), fetched
// only once its tab is opened and then served from cache, so one failing
// advisor only affects its own tab.
const FETCH_BY_TAB = {
  index: api.getIndexAdvisor,
  schema: api.getSchemaLint,
  config: api.getConfigAdvisor,
  replication: api.getReplicationAdvisor,
  backup: api.getBackupAdvisor,
  "pre-upgrade": api.getPreUpgradeAdvisor,
  security: api.getSecurityAdvisor,
  extensions: api.getExtensionAdvisor,
};

const advisorKey = (targetId, tab) => targetKey(targetId, "advisor", tab);

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
  { prefix: "installed-extensions-summary", label: "Installed Extensions" },
  { prefix: "recommended-extension-", label: "Recommended Extensions" },
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

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

export default function Advisor({ targetId }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { tab: urlTab } = useParams();

  const [firstSeenByFindingId] = useFirstSeenMap(targetId);
  const [hiddenTabs, setHiddenTabs] = useState(() => loadStoredSet(HIDDEN_TABS_KEY));
  // Loaded once per mount: App remounts this screen (key={targetId}) when
  // the target changes, so a fresh target always reads its own key.
  const [hiddenSchemas, setHiddenSchemas] = useState(() => loadStoredSet(hiddenSchemasKey(targetId)));
  const [actionError, setActionError] = useState(null);

  const tabLabels = TABS.map((t) => t.label);
  const visibleTabs = TABS.filter((t) => !hiddenTabs.has(t.label));

  // The active tab comes from the URL (/t/:id/advisor/:tab), so a deep link
  // (e.g. the Dashboard's Replication tile) or a reload lands on it. An
  // unknown or hidden tab falls back to Index Advisor, or to the first
  // visible tab when that one is hidden too; null when every tab is hidden.
  const isVisible = (key) => visibleTabs.some((t) => t.key === key);
  const tab = isVisible(urlTab)
    ? urlTab
    : isVisible(DEFAULT_TAB)
      ? DEFAULT_TAB
      : (visibleTabs[0]?.key ?? null);

  const changeTab = (key) => navigate(screenPath("advisor", targetId, key), { replace: true });

  // Hidden categories follow the active tab: reloaded from storage whenever
  // the tab changes (adjusted during render rather than in an effect).
  const [hiddenCategories, setHiddenCategories] = useState(() => loadStoredSet(hiddenCategoriesKey(tab)));
  const [categoriesTab, setCategoriesTab] = useState(tab);
  if (tab !== categoriesTab) {
    setCategoriesTab(tab);
    setHiddenCategories(loadStoredSet(hiddenCategoriesKey(tab)));
  }

  const findingsQuery = useQuery({
    queryKey: advisorKey(targetId, tab),
    queryFn: ({ signal }) => FETCH_BY_TAB[tab](targetId, { signal }),
    enabled: tab != null,
  });

  const activeFindings = findingsQuery.data?.findings ?? null;
  // Schema names present on the active tab's findings, regardless of current
  // hide state — the dropdown always lists every schema seen, same as
  // Categories does for group labels. Findings with no schema_name (a
  // database-wide check, e.g. a setting or role) are never affected by this
  // filter — they have no schema to match against.
  const allSchemaNames = activeFindings
    ? [...new Set(activeFindings.filter((f) => f.schema_name).map((f) => f.schema_name))].sort()
    : [];
  const schemaFilteredFindings = activeFindings
    ? activeFindings.filter((f) => !f.schema_name || !hiddenSchemas.has(f.schema_name))
    : null;
  const allGroups = schemaFilteredFindings ? groupFindings(schemaFilteredFindings, GROUPS_BY_TAB[tab]) : [];
  const categoryLabels = allGroups.map((g) => g.label);
  const visibleGroups = allGroups.filter((g) => !hiddenCategories.has(g.label));
  const visibleFindingCount = visibleGroups.reduce((sum, g) => sum + g.findings.length, 0);

  const persistHiddenCategories = (next) => {
    setHiddenCategories(next);
    saveStoredSet(hiddenCategoriesKey(tab), next);
  };

  const toggleCategoryHidden = (label) => {
    const next = new Set(hiddenCategories);
    if (next.has(label)) next.delete(label);
    else next.add(label);
    persistHiddenCategories(next);
  };

  const persistHiddenTabs = (next) => {
    setHiddenTabs(next);
    saveStoredSet(HIDDEN_TABS_KEY, next);
  };

  const toggleTabHidden = (label) => {
    const next = new Set(hiddenTabs);
    if (next.has(label)) next.delete(label);
    else next.add(label);
    persistHiddenTabs(next);
  };

  const persistHiddenSchemas = (next) => {
    setHiddenSchemas(next);
    saveStoredSet(hiddenSchemasKey(targetId), next);
  };

  const toggleSchemaHidden = (schemaName) => {
    const next = new Set(hiddenSchemas);
    if (next.has(schemaName)) next.delete(schemaName);
    else next.add(schemaName);
    persistHiddenSchemas(next);
  };

  // Drops a finding from one tab's cached result — what Archive and Apply
  // do on success, instead of refetching the whole advisor.
  const removeFinding = (tabKey, findingId) =>
    queryClient.setQueryData(advisorKey(targetId, tabKey), (prev) =>
      prev ? { ...prev, findings: prev.findings.filter((f) => f.id !== findingId) } : prev
    );

  const handleArchive = (finding) => {
    const tabKey = tab;
    setActionError(null);
    api
      .archiveFinding(targetId, finding)
      .then(() => {
        removeFinding(tabKey, finding.id);
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "archived-findings") });
      })
      .catch((err) => setActionError(err.message));
  };

  // Only Index Advisor findings are apply-able — Schema Lint/Configuration
  // Advisor's recommended_ddl (where present) is a data-type/setting change,
  // riskier to run unattended than a CREATE/DROP INDEX CONCURRENTLY.
  const handleApply = (finding) =>
    api.applyIndexFinding(targetId, finding.id).then(() => {
      removeFinding("index", finding.id);
      queryClient.invalidateQueries({ queryKey: targetKey(targetId, "index-testing") });
    });

  // Covering Index Candidates' "Analyze with AI" panel can produce a
  // recommended_ddl the base finding didn't have (heuristic was
  // unconfident) — applied by referencing the stored analysis id, so the
  // server (not the client) is always the one supplying the actual DDL.
  const handleApplyAnalysis = (finding, analysisId) =>
    api.applyIndexCoverageAnalysis(targetId, analysisId).then(() => {
      removeFinding("index", finding.id);
      queryClient.invalidateQueries({ queryKey: targetKey(targetId, "index-testing") });
    });

  const countText =
    activeFindings &&
    `${plural(visibleFindingCount, "finding", "findings")}${
      hiddenCategories.size > 0 ? ` (${plural(hiddenCategories.size, "category", "categories")} hidden)` : ""
    }${hiddenSchemas.size > 0 ? ` (${plural(hiddenSchemas.size, "schema", "schemas")} hidden)` : ""}`;

  return (
    <Card>
      <div className={styles.toolbar}>
        {visibleTabs.length > 0 && (
          <Tabs label="Advisor sections" idPrefix="advisor" items={visibleTabs} value={tab} onChange={changeTab} />
        )}
        <div className={styles.filters}>
          {countText && <span className={styles.count}>{countText}</span>}
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
          {visibleTabs.length > 0 && allSchemaNames.length > 0 && (
            <CategoryFilterDropdown
              label="Schemas"
              items={allSchemaNames}
              hiddenItems={hiddenSchemas}
              onToggle={toggleSchemaHidden}
              onSelectAll={() => persistHiddenSchemas(new Set())}
              onSelectNone={() => persistHiddenSchemas(new Set(allSchemaNames))}
            />
          )}
        </div>
      </div>

      {actionError && <Alert tone="critical">{actionError}</Alert>}

      {visibleTabs.length === 0 && (
        <p className={styles.note}>All Advisor tabs are hidden — use the Tabs button above to show one.</p>
      )}

      {tab != null && (
        <TabPanel idPrefix="advisor" value={tab} className={styles.panel}>
          {findingsQuery.error && <Alert tone="critical">{findingsQuery.error.message}</Alert>}

          {findingsQuery.isPending && <Skeleton lines={4} label="Loading findings…" />}

          {activeFindings && activeFindings.length === 0 && (
            <EmptyState icon={CircleCheck} title="No findings">
              {EMPTY_MESSAGE_BY_TAB[tab]}
            </EmptyState>
          )}

          {activeFindings && activeFindings.length > 0 && schemaFilteredFindings.length === 0 && (
            <p className={styles.note}>All schemas are hidden — use the Schemas button above to show some.</p>
          )}

          {activeFindings &&
            activeFindings.length > 0 &&
            schemaFilteredFindings.length > 0 &&
            visibleGroups.length === 0 && (
              <p className={styles.note}>
                All categories are hidden — use the Categories button above to show some.
              </p>
            )}

          {visibleGroups.length > 0 && (
            <div className={styles.groups}>
              {visibleGroups.map((group) => (
                <CollapsibleGroup key={group.label} label={group.label} count={group.findings.length}>
                  {group.findings.map((finding) => (
                    <FindingCard
                      key={finding.id}
                      finding={finding}
                      targetId={tab === "index" ? targetId : undefined}
                      onArchive={() => handleArchive(finding)}
                      onApply={tab === "index" ? () => handleApply(finding) : undefined}
                      onApplyAnalysis={
                        tab === "index" ? (analysisId) => handleApplyAnalysis(finding, analysisId) : undefined
                      }
                      firstSeenAt={firstSeenByFindingId[finding.id]}
                    />
                  ))}
                </CollapsibleGroup>
              ))}
            </div>
          )}
        </TabPanel>
      )}
    </Card>
  );
}
