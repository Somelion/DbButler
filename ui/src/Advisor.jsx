import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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

// `label` is the English name and the stable id the hidden-tabs preference
// is stored under; the displayed name is translated at render from
// tabs.<key>.
const TABS = [
  { key: "index", label: "Index Advisor" },
  { key: "schema", label: "Schema Lint" },
  { key: "config", label: "Configuration Advisor" },
  { key: "replication", label: "Replication Advisor" },
  { key: "backup", label: "Backup & WAL Health" },
  { key: "pre-upgrade", label: "Pre-Upgrade Scan" },
  { key: "security", label: "Security & Compliance" },
  { key: "extensions", label: "Extensions" },
  { key: "refactoring", label: "Refactoring" },
];

const TAB_KEY_BY_LABEL = new Map(TABS.map((tab) => [tab.label, tab.key]));

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
  refactoring: api.getRefactoringAdvisor,
};

const advisorKey = (targetId, tab) => targetKey(targetId, "advisor", tab);

// Maps a finding's id prefix to the collapsible group it belongs under —
// finer-grained than the "index advisor"/"schema lint"/"configuration
// advisor" category that already splits the three tabs, so each specific
// check gets its own expandable group. `label` is the English name and the
// stable id the hidden-categories preference is stored under; `key` names
// its translation (groups.<key>), looked up at render.
const INDEX_GROUPS = [
  { prefix: "missing-fk-index-", label: "Missing FK Index", key: "missingFkIndex" },
  { prefix: "unused-index-", label: "Unused Indexes", key: "unusedIndexes" },
  { prefix: "duplicate-index-", label: "Duplicate Indexes", key: "duplicateIndexes" },
  { prefix: "redundant-index-", label: "Redundant Indexes", key: "redundantIndexes" },
  { prefix: "invalid-index-", label: "Invalid Indexes", key: "invalidIndexes" },
  { prefix: "seq-scan-heavy-", label: "Seq-Scan-Heavy", key: "seqScanHeavy" },
  { prefix: "seq-scan-no-index-", label: "Seq Scan — No Index", key: "seqScanNoIndex" },
  { prefix: "over-indexed-", label: "Over-Indexed Tables", key: "overIndexedTables" },
  { prefix: "low-cardinality-index-", label: "Low-Cardinality Indexes", key: "lowCardinalityIndexes" },
  { prefix: "covering-index-", label: "Covering Index Candidates", key: "coveringIndexCandidates" },
];

const SCHEMA_GROUPS = [
  { prefix: "float-money-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "varchar255-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "char-type-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "legacy-serial-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "blob-in-db-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "boolean-as-int-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "naive-timestamp-", label: "Bad Data Types", key: "badDataTypes" },
  { prefix: "missing-partitioning-", label: "Missing Partitioning", key: "missingPartitioning" },
  { prefix: "uuid-fragmentation-", label: "UUID Fragmentation", key: "uuidFragmentation" },
  { prefix: "missing-primary-key-", label: "Missing Primary Key", key: "missingPrimaryKey" },
  { prefix: "sequence-exhaustion-", label: "Sequence Exhaustion Risk", key: "sequenceExhaustionRisk" },
  { prefix: "jsonb-overuse-", label: "JSONB Overuse", key: "jsonbOveruse" },
  { prefix: "fk-type-mismatch-", label: "FK Type Mismatch", key: "fkTypeMismatch" },
  { prefix: "select-star", label: "Risky Query Patterns", key: "riskyQueryPatterns" },
  { prefix: "not-in-subquery", label: "Risky Query Patterns", key: "riskyQueryPatterns" },
  { prefix: "deep-offset-pagination", label: "Risky Query Patterns", key: "riskyQueryPatterns" },
  { prefix: "function-wrapped-column", label: "Risky Query Patterns", key: "riskyQueryPatterns" },
  { prefix: "disk-spill", label: "Risky Query Patterns", key: "riskyQueryPatterns" },
  { prefix: "n-plus-one-signature", label: "N+1 Query Signature", key: "nPlusOneSignature" },
  { prefix: "unlogged-table-", label: "Unlogged Tables", key: "unloggedTables" },
  { prefix: "unvalidated-constraint-", label: "Unvalidated Constraints", key: "unvalidatedConstraints" },
];

const CONFIG_GROUPS = [
  { prefix: "autovacuum-disabled-", label: "Autovacuum Disabled", key: "autovacuumDisabled" },
  { prefix: "fsync-disabled", label: "Durability Settings", key: "durabilitySettings" },
  { prefix: "synchronous-commit-off", label: "Durability Settings", key: "durabilitySettings" },
  { prefix: "full-page-writes-off", label: "Durability Settings", key: "durabilitySettings" },
  { prefix: "data-checksums-disabled", label: "Durability Settings", key: "durabilitySettings" },
  { prefix: "random-page-cost-", label: "Planner Cost Settings", key: "plannerCostSettings" },
  { prefix: "pg-stat-statements-eviction", label: "pg_stat_statements Coverage", key: "pgStatStatementsCoverage" },
  { prefix: "version-eol-", label: "PostgreSQL Version Support", key: "postgresVersionSupport" },
];

const REPLICATION_GROUPS = [
  { prefix: "replication-lag-", label: "Replication Lag", key: "replicationLag" },
  { prefix: "inactive-replication-slot-", label: "Inactive Replication Slots", key: "inactiveReplicationSlots" },
  {
    prefix: "subscription-apply-worker-down-",
    label: "Logical Replication Subscriptions",
    key: "logicalReplicationSubscriptions",
  },
];

const BACKUP_GROUPS = [
  { prefix: "wal-archiving-not-configured", label: "WAL Archiving", key: "walArchiving" },
  { prefix: "wal-archiving-failures", label: "WAL Archiving", key: "walArchiving" },
  { prefix: "wal-directory-oversized", label: "WAL Directory Size", key: "walDirectorySize" },
];

const PRE_UPGRADE_GROUPS = [
  { prefix: "pre-upgrade-removed-setting-", label: "Removed or Renamed Settings", key: "removedSettings" },
  { prefix: "pre-upgrade-reg-type-", label: "reg* Type Columns", key: "regTypeColumns" },
];

const SECURITY_GROUPS = [
  { prefix: "audit-logging-gap", label: "Audit Logging", key: "auditLogging" },
  { prefix: "ssl-disabled", label: "Encryption in Transit", key: "encryptionInTransit" },
  { prefix: "excess-superusers", label: "Least Privilege", key: "leastPrivilege" },
  { prefix: "public-grant-", label: "PUBLIC Grants", key: "publicGrants" },
  { prefix: "rls-policy-not-enforced-", label: "Row-Level Security", key: "rowLevelSecurity" },
  { prefix: "rls-enabled-no-policy-", label: "Row-Level Security", key: "rowLevelSecurity" },
  {
    prefix: "security-definer-search-path-",
    label: "SECURITY DEFINER Search Path",
    key: "securityDefinerSearchPath",
  },
];

const EXTENSION_GROUPS = [
  { prefix: "timescaledb-uncompressed-", label: "TimescaleDB Compression", key: "timescaledbCompression" },
  { prefix: "pgvector-unindexed-", label: "pgvector Indexing", key: "pgvectorIndexing" },
  { prefix: "postgis-unindexed-", label: "PostGIS Indexing", key: "postgisIndexing" },
  { prefix: "installed-extensions-summary", label: "Installed Extensions", key: "installedExtensions" },
  { prefix: "recommended-extension-", label: "Recommended Extensions", key: "recommendedExtensions" },
];

// Refactoring Advisor (backend/app/refactoring_advisor.py): workload-driven
// schema suggestions, one group per rule.
const REFACTORING_GROUPS = [
  { prefix: "missing-fk-join-", label: "Hot Joins Without a Foreign Key", key: "hotJoinsWithoutFk" },
  { prefix: "missing-join-index-", label: "Join Columns Without an Index", key: "joinColumnsWithoutIndex" },
  { prefix: "partition-candidate-", label: "Partitioning Candidates", key: "partitioningCandidates" },
  { prefix: "write-amplification-", label: "Write Amplification", key: "writeAmplification" },
  { prefix: "consolidate-index-", label: "Index Consolidation", key: "indexConsolidation" },
  { prefix: "unused-fk-index-", label: "Unused FK Indexes", key: "unusedFkIndexes" },
  { prefix: "value-skew-", label: "Skewed Filter Columns", key: "skewedFilterColumns" },
  { prefix: "denormalize-", label: "Denormalization Hints", key: "denormalizationHints" },
  { prefix: "wide-row-", label: "Wide Rows", key: "wideRows" },
  { prefix: "cold-columns-", label: "Columns Not Seen in the Workload", key: "coldColumns" },
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
  refactoring: REFACTORING_GROUPS,
};

// The empty-state message per tab is translated at render from empty.<tab>.

function groupFindings(findings, groupDefs) {
  const order = [];
  const byLabel = new Map();
  const keyByLabel = new Map();
  for (const finding of findings) {
    const match = groupDefs.find((g) => finding.id.startsWith(g.prefix));
    const label = match ? match.label : "Other";
    if (!byLabel.has(label)) {
      byLabel.set(label, []);
      keyByLabel.set(label, match ? match.key : "other");
      order.push(label);
    }
    byLabel.get(label).push(finding);
  }
  return order.map((label) => ({ label, key: keyByLabel.get(label), findings: byLabel.get(label) }));
}

export default function Advisor({ targetId }) {
  const { t } = useTranslation("advisor");
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { tab: urlTab } = useParams();

  const [firstSeenByFindingId] = useFirstSeenMap(targetId);
  const [hiddenTabs, setHiddenTabs] = useState(() => loadStoredSet(HIDDEN_TABS_KEY));
  // Loaded once per mount: App remounts this screen (key={targetId}) when
  // the target changes, so a fresh target always reads its own key.
  const [hiddenSchemas, setHiddenSchemas] = useState(() => loadStoredSet(hiddenSchemasKey(targetId)));
  const [actionError, setActionError] = useState(null);

  const tabLabels = TABS.map((tabDef) => tabDef.label);
  const visibleTabs = TABS.filter((tabDef) => !hiddenTabs.has(tabDef.label));
  const visibleTabItems = visibleTabs.map((tabDef) => ({ ...tabDef, label: t(`tabs.${tabDef.key}`) }));
  const tabItemLabel = (label) => t(`tabs.${TAB_KEY_BY_LABEL.get(label)}`);

  // The active tab comes from the URL (/t/:id/advisor/:tab), so a deep link
  // (e.g. the Dashboard's Replication tile) or a reload lands on it. An
  // unknown or hidden tab falls back to Index Advisor, or to the first
  // visible tab when that one is hidden too; null when every tab is hidden.
  const isVisible = (key) => visibleTabs.some((tabDef) => tabDef.key === key);
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
  const groupKeyByLabel = new Map(allGroups.map((g) => [g.label, g.key]));
  const categoryItemLabel = (label) => t(`groups.${groupKeyByLabel.get(label) ?? "other"}`);
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
    `${t("count.findings", { count: visibleFindingCount })}${
      hiddenCategories.size > 0 ? ` (${t("count.categoriesHidden", { count: hiddenCategories.size })})` : ""
    }${hiddenSchemas.size > 0 ? ` (${t("count.schemasHidden", { count: hiddenSchemas.size })})` : ""}`;

  return (
    <Card>
      <div className={styles.toolbar}>
        {visibleTabs.length > 0 && (
          <Tabs label={t("sections")} idPrefix="advisor" items={visibleTabItems} value={tab} onChange={changeTab} />
        )}
        <div className={styles.filters}>
          {countText && <span className={styles.count}>{countText}</span>}
          <CategoryFilterDropdown
            label={t("filters.tabs")}
            items={tabLabels}
            itemLabel={tabItemLabel}
            hiddenItems={hiddenTabs}
            onToggle={toggleTabHidden}
            onSelectAll={() => persistHiddenTabs(new Set())}
            onSelectNone={() => persistHiddenTabs(new Set(tabLabels))}
          />
          {visibleTabs.length > 0 && (
            <CategoryFilterDropdown
              label={t("filters.categories")}
              items={categoryLabels}
              itemLabel={categoryItemLabel}
              hiddenItems={hiddenCategories}
              onToggle={toggleCategoryHidden}
              onSelectAll={() => persistHiddenCategories(new Set())}
              onSelectNone={() => persistHiddenCategories(new Set(categoryLabels))}
            />
          )}
          {visibleTabs.length > 0 && allSchemaNames.length > 0 && (
            <CategoryFilterDropdown
              label={t("filters.schemas")}
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
        <p className={styles.note}>{t("allTabsHidden")}</p>
      )}

      {tab != null && (
        <TabPanel idPrefix="advisor" value={tab} className={styles.panel}>
          {findingsQuery.error && <Alert tone="critical">{findingsQuery.error.message}</Alert>}

          {findingsQuery.isPending && <Skeleton lines={4} label={t("loadingFindings")} />}

          {activeFindings && activeFindings.length === 0 && (
            <EmptyState icon={CircleCheck} title={t("noFindings")}>
              {t(`empty.${tab}`)}
            </EmptyState>
          )}

          {activeFindings && activeFindings.length > 0 && schemaFilteredFindings.length === 0 && (
            <p className={styles.note}>{t("allSchemasHidden")}</p>
          )}

          {activeFindings &&
            activeFindings.length > 0 &&
            schemaFilteredFindings.length > 0 &&
            visibleGroups.length === 0 && <p className={styles.note}>{t("allCategoriesHidden")}</p>}

          {visibleGroups.length > 0 && (
            <div className={styles.groups}>
              {visibleGroups.map((group) => (
                <CollapsibleGroup key={group.label} label={t(`groups.${group.key}`)} count={group.findings.length}>
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
