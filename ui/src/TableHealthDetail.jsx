import { useTranslation } from "react-i18next";
import { formatBytes } from "./format";
import FindingCard from "./FindingCard";
import CollapsibleGroup from "./CollapsibleGroup";
import HeaderHint from "./HeaderHint";
import tableStyles from "./components/Table.module.css";
import styles from "./TableHealthDetail.module.css";

// Finer-grained than the flat "table health" category every finding here
// shares — mirrors Advisor.jsx's id-prefix grouping so related suggestions
// (e.g. never-vacuumed and dead-tuple accumulation) sit together.
// Labels are translation keys, translated at render.
const FINDING_GROUPS = [
  { prefix: "table-health-never-vacuumed-", label: "detail.groups.vacuumAnalyze" },
  { prefix: "table-health-never-analyzed-", label: "detail.groups.vacuumAnalyze" },
  { prefix: "table-health-dead-tuples-", label: "detail.groups.vacuumAnalyze" },
  { prefix: "table-health-dead-tuple-forecast-", label: "detail.groups.vacuumAnalyze" },
  { prefix: "table-health-toast-heavy-", label: "detail.groups.storageToast" },
  { prefix: "table-health-wide-column-", label: "detail.groups.storageToast" },
  { prefix: "table-health-low-stats-target-", label: "detail.groups.statistics" },
  { prefix: "table-health-autovacuum-scale-", label: "detail.groups.autovacuumUpdates" },
  { prefix: "table-health-hot-update-ratio-", label: "detail.groups.autovacuumUpdates" },
];
const OTHER_GROUP = "detail.groups.other";

// Translation keys under detail.columnHeaders.<id>.{label,hint}.
const COLUMN_HEADERS = ["column", "type", "avgWidth", "nullPct", "distinct", "statsTarget", "indexed", "storage"];

function groupFindings(findings) {
  const order = [];
  const byLabel = new Map();
  for (const finding of findings) {
    const match = FINDING_GROUPS.find((g) => finding.id.startsWith(g.prefix));
    const label = match ? match.label : OTHER_GROUP;
    if (!byLabel.has(label)) {
      byLabel.set(label, []);
      order.push(label);
    }
    byLabel.get(label).push(finding);
  }
  return order.map((label) => ({ label, findings: byLabel.get(label) }));
}

export default function TableHealthDetail({ detail, onArchive, onApply, firstSeenByFindingId = {} }) {
  const { t } = useTranslation("tableHealth");
  const { findings, columns, storage, autovacuum } = detail;
  const groups = groupFindings(findings);
  const toastPct = storage.total_bytes > 0 ? (100 * storage.toast_bytes) / storage.total_bytes : 0;

  return (
    <div className={styles.detail}>
      <div className={styles.statGroups}>
        <StatGroup title={t("detail.storage.title")}>
          <Stat label={t("detail.storage.heap")} hint={t("detail.storage.heapHint")} value={formatBytes(storage.heap_bytes)} />
          <Stat
            label={t("detail.storage.toast")}
            value={`${formatBytes(storage.toast_bytes)}${storage.toast_bytes > 0 ? ` (${toastPct.toFixed(0)}%)` : ""}`}
            hint={t("detail.storage.toastHint")}
          />
          <Stat
            label={t("detail.storage.indexes")}
            value={formatBytes(storage.index_bytes)}
            hint={t("detail.storage.indexesHint")}
          />
          <Stat label={t("detail.storage.total")} value={formatBytes(storage.total_bytes)} hint={t("detail.storage.totalHint")} />
        </StatGroup>

        <StatGroup title={t("detail.autovacuum.title")}>
          <Stat
            label={t("detail.autovacuum.autovacuum")}
            value={autovacuum.autovacuum_enabled ? t("detail.autovacuum.enabled") : t("detail.autovacuum.disabled")}
            hint={t("detail.autovacuum.autovacuumHint")}
          />
          <Stat
            label={t("detail.autovacuum.vacuumScaleFactor")}
            value={`${(autovacuum.vacuum_scale_factor * 100).toFixed(0)}%${
              autovacuum.has_custom_scale_factor ? "" : ` (${t("detail.autovacuum.serverDefault")})`
            }`}
            hint={t("detail.autovacuum.vacuumScaleFactorHint")}
          />
          <Stat
            label={t("detail.autovacuum.analyzeScaleFactor")}
            value={`${(autovacuum.analyze_scale_factor * 100).toFixed(0)}%`}
            hint={t("detail.autovacuum.analyzeScaleFactorHint")}
          />
          <Stat
            label={t("detail.autovacuum.fillfactor")}
            value={autovacuum.fillfactor}
            hint={t("detail.autovacuum.fillfactorHint")}
          />
          <Stat
            label={t("detail.autovacuum.hotRatio")}
            value={autovacuum.hot_update_ratio != null ? `${(autovacuum.hot_update_ratio * 100).toFixed(0)}%` : "—"}
            hint={t("detail.autovacuum.hotRatioHint")}
          />
          <Stat
            label={t("detail.autovacuum.vacuumStatus")}
            value={
              autovacuum.vacuum_in_progress
                ? t("detail.autovacuum.running", {
                    details: `${autovacuum.vacuum_phase}${
                      autovacuum.vacuum_progress_pct != null ? `, ${autovacuum.vacuum_progress_pct.toFixed(0)}%` : ""
                    }`,
                  })
                : t("detail.autovacuum.notRunning")
            }
            hint={t("detail.autovacuum.vacuumStatusHint")}
          />
        </StatGroup>
      </div>

      {columns.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>{t("detail.columnsTitle")}</h3>
          <div className={tableStyles.wrap}>
            <table className={`${tableStyles.table} ${styles.columnsTable}`}>
              <thead>
                <tr>
                  {COLUMN_HEADERS.map((id) => (
                    <th key={id} scope="col" className={`${tableStyles.th} ${styles.cell}`}>
                      {t(`detail.columnHeaders.${id}.label`)}
                      <HeaderHint text={t(`detail.columnHeaders.${id}.hint`)} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {columns.map((col) => (
                  <tr key={col.attname}>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.mono}`}>{col.attname}</td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary} ${styles.nowrap}`}>{col.data_type}</td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>
                      {col.avg_width != null ? `${col.avg_width} B` : "—"}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>
                      {col.null_frac != null ? `${(col.null_frac * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>
                      {col.n_distinct != null ? col.n_distinct : "—"}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>
                      {col.attstattarget === -1 || col.attstattarget == null ? t("detail.default") : col.attstattarget}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>
                      {col.is_indexed ? t("detail.yes") : "—"}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>{col.storage}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>{t("detail.suggestionsTitle")}</h3>
        {findings.length === 0 ? (
          <p className={styles.empty}>{t("detail.noSuggestions")}</p>
        ) : (
          <div className={styles.groups}>
            {groups.map((group) => (
              <CollapsibleGroup key={group.label} label={t(group.label)} count={group.findings.length} defaultExpanded>
                {group.findings.map((finding) => (
                  <FindingCard
                    key={finding.id}
                    finding={finding}
                    onArchive={() => onArchive(finding)}
                    onApply={() => onApply(finding)}
                    firstSeenAt={firstSeenByFindingId[finding.id]}
                  />
                ))}
              </CollapsibleGroup>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatGroup({ title, children }) {
  return (
    <div className={styles.statGroup}>
      <h3 className={styles.sectionTitle}>{title}</h3>
      <dl className={styles.stats}>{children}</dl>
    </div>
  );
}

function Stat({ label, value, hint }) {
  return (
    <div className={styles.stat}>
      <dt className={styles.statLabel}>
        {label}
        <HeaderHint text={hint} />
      </dt>
      <dd className={styles.statValue}>{value}</dd>
    </div>
  );
}
