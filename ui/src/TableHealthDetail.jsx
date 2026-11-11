import { formatBytes } from "./format";
import FindingCard from "./FindingCard";
import CollapsibleGroup from "./CollapsibleGroup";
import HeaderHint from "./HeaderHint";
import tableStyles from "./components/Table.module.css";
import styles from "./TableHealthDetail.module.css";

// Finer-grained than the flat "table health" category every finding here
// shares — mirrors Advisor.jsx's id-prefix grouping so related suggestions
// (e.g. never-vacuumed and dead-tuple accumulation) sit together.
const FINDING_GROUPS = [
  { prefix: "table-health-never-vacuumed-", label: "Vacuum & Analyze" },
  { prefix: "table-health-never-analyzed-", label: "Vacuum & Analyze" },
  { prefix: "table-health-dead-tuples-", label: "Vacuum & Analyze" },
  { prefix: "table-health-dead-tuple-forecast-", label: "Vacuum & Analyze" },
  { prefix: "table-health-toast-heavy-", label: "Storage & TOAST" },
  { prefix: "table-health-wide-column-", label: "Storage & TOAST" },
  { prefix: "table-health-low-stats-target-", label: "Statistics" },
  { prefix: "table-health-autovacuum-scale-", label: "Autovacuum & Updates" },
  { prefix: "table-health-hot-update-ratio-", label: "Autovacuum & Updates" },
];

const COLUMN_HEADERS = [
  { label: "Column", hint: "Column name." },
  { label: "Type", hint: "Postgres data type." },
  {
    label: "Avg width",
    hint: "Average on-disk size of this column's values, in bytes. Shows — until the table has been analyzed at least once.",
  },
  { label: "Null %", hint: "Share of this table's rows where this column is NULL." },
  {
    label: "Distinct",
    hint: "Estimated number of distinct values (pg_stats.n_distinct). A non-negative number is an absolute count; a negative number is a fraction of all rows (e.g. -0.5 means about half the rows are distinct).",
  },
  {
    label: "Stats target",
    hint: "How many rows Postgres samples to build this column's planner statistics. \"default\" uses the server-wide default_statistics_target (100 unless changed) rather than a per-column override.",
  },
  { label: "Indexed", hint: "Whether this column is part of any index (alone or combined with others)." },
  {
    label: "Storage",
    hint: "How large values in this column can be stored: plain (always inline, never TOASTed), extended/main (may move out-of-line into TOAST, compressed), external (moves out-of-line uncompressed).",
  },
];

function groupFindings(findings) {
  const order = [];
  const byLabel = new Map();
  for (const finding of findings) {
    const match = FINDING_GROUPS.find((g) => finding.id.startsWith(g.prefix));
    const label = match ? match.label : "Other";
    if (!byLabel.has(label)) {
      byLabel.set(label, []);
      order.push(label);
    }
    byLabel.get(label).push(finding);
  }
  return order.map((label) => ({ label, findings: byLabel.get(label) }));
}

export default function TableHealthDetail({ detail, onArchive, onApply, firstSeenByFindingId = {} }) {
  const { findings, columns, storage, autovacuum } = detail;
  const groups = groupFindings(findings);
  const toastPct = storage.total_bytes > 0 ? (100 * storage.toast_bytes) / storage.total_bytes : 0;

  return (
    <div className={styles.detail}>
      <div className={styles.statGroups}>
        <StatGroup title="Storage">
          <Stat label="Heap" hint="This table's own data pages — everything except TOAST and indexes." value={formatBytes(storage.heap_bytes)} />
          <Stat
            label="TOAST"
            value={`${formatBytes(storage.toast_bytes)}${storage.toast_bytes > 0 ? ` (${toastPct.toFixed(0)}%)` : ""}`}
            hint="Large text/bytea/jsonb values stored out-of-line, compressed, from the main table."
          />
          <Stat
            label="Indexes"
            value={formatBytes(storage.index_bytes)}
            hint="Combined size of every index on this table. Derived (total minus heap minus TOAST), not its own separate measurement."
          />
          <Stat label="Total" value={formatBytes(storage.total_bytes)} hint="Everything for this table combined — heap + TOAST + indexes." />
        </StatGroup>

        <StatGroup title="Autovacuum & updates">
          <Stat
            label="Autovacuum"
            value={autovacuum.autovacuum_enabled ? "Enabled" : "Disabled"}
            hint="Whether autovacuum runs on this table at all — should almost always be Enabled."
          />
          <Stat
            label="Vacuum scale factor"
            value={`${(autovacuum.vacuum_scale_factor * 100).toFixed(0)}%${autovacuum.has_custom_scale_factor ? "" : " (server default)"}`}
            hint="Fraction of a table's rows that must be dead before autovacuum runs. A table-level override, when set, replaces the server-wide default shown here."
          />
          <Stat
            label="Analyze scale factor"
            value={`${(autovacuum.analyze_scale_factor * 100).toFixed(0)}%`}
            hint="Fraction of a table's rows that must change before autovacuum re-analyzes it (refreshes planner statistics)."
          />
          <Stat
            label="Fillfactor"
            value={autovacuum.fillfactor}
            hint="Percent of each page Postgres fills before starting a new one — the rest is headroom for HOT updates."
          />
          <Stat
            label="HOT update ratio"
            value={autovacuum.hot_update_ratio != null ? `${(autovacuum.hot_update_ratio * 100).toFixed(0)}%` : "—"}
            hint="Share of updates that avoided touching any index (Heap-Only Tuple). Low means most updates are paying full index-maintenance cost."
          />
          <Stat
            label="Vacuum status"
            value={
              autovacuum.vacuum_in_progress
                ? `Running (${autovacuum.vacuum_phase}${
                    autovacuum.vacuum_progress_pct != null ? `, ${autovacuum.vacuum_progress_pct.toFixed(0)}%` : ""
                  })`
                : "Not running"
            }
            hint="Whether a VACUUM (manual or autovacuum) is actively running on this table right now, from pg_stat_progress_vacuum. Only reflects the current instant, not history."
          />
        </StatGroup>
      </div>

      {columns.length > 0 && (
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>Columns</h3>
          <div className={tableStyles.wrap}>
            <table className={`${tableStyles.table} ${styles.columnsTable}`}>
              <thead>
                <tr>
                  {COLUMN_HEADERS.map((col) => (
                    <th key={col.label} scope="col" className={`${tableStyles.th} ${styles.cell}`}>
                      {col.label}
                      <HeaderHint text={col.hint} />
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
                      {col.attstattarget === -1 || col.attstattarget == null ? "default" : col.attstattarget}
                    </td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>{col.is_indexed ? "Yes" : "—"}</td>
                    <td className={`${tableStyles.td} ${styles.cell} ${styles.secondary}`}>{col.storage}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Suggestions</h3>
        {findings.length === 0 ? (
          <p className={styles.empty}>No suggestions for this table.</p>
        ) : (
          <div className={styles.groups}>
            {groups.map((group) => (
              <CollapsibleGroup key={group.label} label={group.label} count={group.findings.length} defaultExpanded>
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
