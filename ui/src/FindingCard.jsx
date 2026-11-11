import { useId, useState } from "react";
import { Link } from "react-router";
import { useMode } from "./ModeContext";
import { colorForColumn } from "./columnColor";
import { formatBytes, formatRelativeTime } from "./format";
import { Alert, Badge, CopyButton, SeverityBadge, useConfirm } from "./components";
import tableStyles from "./components/Table.module.css";
import IndexCoverageAnalysisPanel from "./IndexCoverageAnalysisPanel";
import styles from "./FindingCard.module.css";

// Covering-index column verdicts: a tone plus a text label, so the verdict
// never rests on the chip colour alone.
const COVERING_VERDICT = {
  key: { label: "used to filter/sort", tone: "healthy" },
  include_candidate: { label: "INCLUDE candidate", tone: "attention" },
  unused_in_sample: { label: "not seen in sample", tone: "unknown" },
  no_data: { label: "no query data", tone: "unknown" },
};

// Column-name pill; its colour pair comes from columnColor.js (token values)
// through inline custom properties.
function ColumnChip({ column, className }) {
  const c = colorForColumn(column);
  return (
    <span
      className={[styles.columnChip, className].filter(Boolean).join(" ")}
      style={{ "--chip-bg": c.bg, "--chip-text": c.text }}
    >
      {column}
    </span>
  );
}

// `navigateTo` is a router path (e.g. from screenPath()) rendered as an
// "Open …" link labelled by `navigateLabel`.
export default function FindingCard({
  finding,
  onArchive,
  onRestore,
  onApply,
  onApplyAnalysis,
  navigateTo,
  navigateLabel,
  onVacuum,
  targetId,
  archivedAt,
  firstSeenAt,
}) {
  const mode = useMode();
  const confirm = useConfirm();
  const titleId = useId();
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState(null);
  const [vacuuming, setVacuuming] = useState(false);
  const [vacuumError, setVacuumError] = useState(null);
  const canApply = Boolean(onApply && finding.recommended_ddl);
  const canVacuum = Boolean(onVacuum && finding.schema_name && finding.table_name);

  // DDL runs straight against the target, so it is confirmed first with the
  // exact statement shown.
  const handleApply = async () => {
    const ok = await confirm({
      title: "Run this against your database?",
      message: (
        <div className={styles.confirmBody}>
          <p>This changes the target directly.</p>
          <pre className={styles.code}>{finding.recommended_ddl}</pre>
        </div>
      ),
      confirmLabel: "Confirm & run",
      tone: "danger",
    });
    if (!ok) return;
    setApplying(true);
    setApplyError(null);
    try {
      await onApply();
    } catch (err) {
      setApplyError(err.message);
    } finally {
      setApplying(false);
    }
  };

  const handleVacuum = async () => {
    const ok = await confirm({
      title: "Run VACUUM ANALYZE?",
      message: `Run VACUUM ANALYZE on ${finding.schema_name}.${finding.table_name}?`,
      confirmLabel: "Run Vacuum",
    });
    if (!ok) return;
    setVacuuming(true);
    setVacuumError(null);
    try {
      await onVacuum();
    } catch (err) {
      setVacuumError(err.message);
    } finally {
      setVacuuming(false);
    }
  };

  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <div className={styles.header}>
        <div className={styles.meta}>
          <SeverityBadge severity={finding.severity} />
          <span className={styles.category}>{finding.category}</span>
          {firstSeenAt && (
            <span className={styles.firstSeen} title="From the deep scan's persisted history">
              · open since {formatRelativeTime(firstSeenAt)}
            </span>
          )}
        </div>
        <div className={styles.actions}>
          {canVacuum && (
            <button type="button" className="button-secondary button-sm" onClick={handleVacuum} disabled={vacuuming}>
              {vacuuming ? "Running…" : "Run Vacuum"}
            </button>
          )}
          {navigateTo && (
            <Link to={navigateTo} className={`button-secondary button-sm ${styles.linkButton}`}>
              {navigateLabel || "Open"}
            </Link>
          )}
          {canApply && (
            <button type="button" className="button-secondary button-sm" onClick={handleApply} disabled={applying}>
              {applying ? "Running…" : "Apply"}
            </button>
          )}
          {onArchive && (
            <button type="button" className="button-secondary button-sm" onClick={onArchive}>
              Archive
            </button>
          )}
          {onRestore && (
            <button type="button" className="button-secondary button-sm" onClick={onRestore}>
              Restore
            </button>
          )}
        </div>
      </div>

      <div className={styles.body}>
        <h3 id={titleId} className={styles.title}>
          {finding.title}
        </h3>
        <p className={styles.summary}>{finding.summary}</p>
        {vacuumError && (
          <Alert tone="critical" compact>
            Vacuum failed: {vacuumError}
          </Alert>
        )}
        {applyError && (
          <Alert tone="critical" compact>
            Apply failed: {applyError}
          </Alert>
        )}
      </div>

      {finding.occurrences && finding.occurrences.length > 0 && (
        <details>
          <summary className={styles.summaryToggle}>
            {finding.occurrences.length} matching quer{finding.occurrences.length === 1 ? "y" : "ies"}
          </summary>
          <ul className={styles.occurrences}>
            {finding.occurrences.map((occ, i) => (
              <li key={i} className={styles.occurrence}>
                <div title={occ.query} className={styles.occurrenceQuery}>
                  {occ.query}
                </div>
                <div className={styles.occurrenceMeta}>
                  Called {occ.calls.toLocaleString()} times{occ.note ? ` · ${occ.note}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {finding.index_columns && finding.index_columns.length > 0 && (
        <details>
          <summary className={styles.summaryToggle}>
            {finding.index_columns.length} index{finding.index_columns.length === 1 ? "" : "es"} on this table
          </summary>
          <div className={`${tableStyles.wrap} ${styles.detailsBody}`}>
            <table className={tableStyles.table}>
              <thead>
                <tr>
                  <th scope="col" className={`${tableStyles.th} ${styles.th}`}>
                    Index
                  </th>
                  <th scope="col" className={`${tableStyles.th} ${styles.th}`}>
                    Columns
                  </th>
                  <th scope="col" className={`${tableStyles.th} ${styles.th}`}>
                    Method
                  </th>
                  <th scope="col" className={`${tableStyles.th} ${styles.th}`}>
                    Size
                  </th>
                </tr>
              </thead>
              <tbody>
                {finding.index_columns.map((idx) => (
                  <tr key={idx.index_name}>
                    <td className={`${tableStyles.td} ${tableStyles.mono} ${styles.td}`}>
                      {idx.index_name}
                      {idx.is_unique && (
                        <Badge tone="healthy" className={styles.uniqueBadge}>
                          UNIQUE
                        </Badge>
                      )}
                    </td>
                    <td className={`${tableStyles.td} ${styles.td}`}>
                      <div className={styles.chipRow}>
                        {idx.columns.map((col) => (
                          <ColumnChip key={col} column={col} />
                        ))}
                      </div>
                    </td>
                    <td className={`${tableStyles.td} ${styles.td} ${styles.secondaryCell}`}>{idx.method}</td>
                    <td className={`${tableStyles.td} ${styles.td} ${styles.secondaryCell}`}>
                      {idx.index_bytes != null ? formatBytes(idx.index_bytes) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {finding.covering_analysis && (
        <div className={styles.section}>
          <h4 className={styles.sectionLabel}>
            Column usage ({finding.covering_analysis.matched_query_count.toLocaleString()} matched quer
            {finding.covering_analysis.matched_query_count === 1 ? "y" : "ies"})
          </h4>
          <ul className={styles.verdicts}>
            {finding.covering_analysis.columns.map((v) => {
              const verdict = COVERING_VERDICT[v.verdict] ?? COVERING_VERDICT.no_data;
              return (
                <li key={v.column}>
                  <Badge tone={verdict.tone} className={styles.verdict}>
                    <ColumnChip column={v.column} className={styles.verdictChip} />
                    {verdict.label}
                  </Badge>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {finding.extensions && finding.extensions.length > 0 && (
        <ul className={styles.extensions}>
          {finding.extensions.map((ext) => (
            <li key={ext.name} className={styles.extension}>
              <span className={styles.extensionName}>{ext.name}</span>
              <span className={styles.extensionVersion}>{ext.version}</span>
            </li>
          ))}
        </ul>
      )}

      {finding.id.startsWith("covering-index-") && targetId && onApplyAnalysis && (
        <IndexCoverageAnalysisPanel targetId={targetId} findingId={finding.id} onApplyAnalysis={onApplyAnalysis} />
      )}

      {mode === "advanced" && (
        <details open>
          <summary className={styles.summaryToggle}>Technical detail</summary>
          <div className={`${styles.detailsBody} ${styles.technical}`}>
            <span className={styles.technicalText}>{finding.detail}</span>
          </div>
        </details>
      )}

      <p className={styles.suggested}>Suggested: {finding.suggested_action}</p>

      {finding.recommended_ddl && <DdlBlock ddl={finding.recommended_ddl} applyAvailable={canApply} />}

      {archivedAt && <p className={styles.archived}>Archived {new Date(archivedAt).toLocaleString()}</p>}
    </article>
  );
}

function DdlBlock({ ddl, applyAvailable }) {
  return (
    <div className={styles.section}>
      <div className={styles.ddlHeader}>
        <h4 className={styles.sectionLabel}>
          {applyAvailable ? "Suggested DDL — use Apply above, or copy to run yourself" : "Suggested DDL — not applied automatically"}
        </h4>
        <CopyButton text={ddl} />
      </div>
      <pre className={styles.code}>{ddl}</pre>
    </div>
  );
}
