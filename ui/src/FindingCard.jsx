import { useState } from "react";
import { useMode } from "./ModeContext";
import { colorForColumn } from "./columnColor";
import { formatBytes, formatRelativeTime } from "./format";
import IndexCoverageAnalysisPanel from "./IndexCoverageAnalysisPanel";

const SEVERITY = {
  critical: { label: "Critical", badgeBg: "var(--critical-bg)", badgeText: "var(--critical-text)", dot: "oklch(52% 0.20 25)" },
  attention: { label: "Attention", badgeBg: "var(--attention-bg)", badgeText: "var(--attention-text)", dot: "oklch(60% 0.16 75)" },
  healthy: { label: "Healthy", badgeBg: "var(--healthy-bg)", badgeText: "var(--healthy-text)", dot: "oklch(56% 0.15 150)" },
  unknown: { label: "Unknown", badgeBg: "oklch(94% 0.004 250)", badgeText: "var(--text-secondary)", dot: "oklch(45% 0.02 255)" },
};

const COVERING_VERDICT = {
  key: { label: "used to filter/sort", bg: "var(--healthy-bg)", text: "var(--healthy-text)" },
  include_candidate: { label: "INCLUDE candidate", bg: "var(--attention-bg)", text: "var(--attention-text)" },
  unused_in_sample: { label: "not seen in sample", bg: "oklch(94% 0.004 250)", text: "var(--text-secondary)" },
  no_data: { label: "no query data", bg: "oklch(94% 0.004 250)", text: "var(--text-secondary)" },
};

const TH_STYLE = {
  textAlign: "left",
  padding: "4px 8px",
  fontSize: 10.5,
  fontWeight: 700,
  letterSpacing: "0.02em",
  textTransform: "uppercase",
  color: "var(--text-muted)",
  borderBottom: "1px solid var(--border)",
};
const TD_STYLE = { padding: "6px 8px", verticalAlign: "top", borderBottom: "1px solid var(--border)" };

export default function FindingCard({
  finding,
  onArchive,
  onRestore,
  onApply,
  onApplyAnalysis,
  onNavigate,
  navigateLabel,
  onVacuum,
  targetId,
  archivedAt,
  firstSeenAt,
}) {
  const mode = useMode();
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState(null);
  const [vacuuming, setVacuuming] = useState(false);
  const [vacuumError, setVacuumError] = useState(null);
  const s = SEVERITY[finding.severity] ?? SEVERITY.unknown;
  const canApply = Boolean(onApply && finding.recommended_ddl);
  const canVacuum = Boolean(onVacuum && finding.schema_name && finding.table_name);

  const handleConfirmApply = () => {
    setApplying(true);
    setApplyError(null);
    Promise.resolve(onApply())
      .then(() => setConfirming(false))
      .catch((err) => setApplyError(err.message))
      .finally(() => setApplying(false));
  };

  const handleVacuum = () => {
    if (!window.confirm(`Run VACUUM ANALYZE on ${finding.schema_name}.${finding.table_name}?`)) return;
    setVacuuming(true);
    setVacuumError(null);
    Promise.resolve(onVacuum())
      .catch((err) => setVacuumError(err.message))
      .finally(() => setVacuuming(false));
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "14px 16px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 9 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: "3px 9px 3px 7px",
              borderRadius: 100,
              background: s.badgeBg,
            }}
          >
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.dot }} />
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: "0.03em",
                textTransform: "uppercase",
                color: s.badgeText,
              }}
            >
              {s.label}
            </span>
          </span>
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text-muted)" }}>
            {finding.category}
          </span>
          {firstSeenAt && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }} title="From the deep scan's persisted history">
              · open since {formatRelativeTime(firstSeenAt)}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {canVacuum && (
            <button className="button-secondary" onClick={handleVacuum} disabled={vacuuming} style={{ padding: "4px 10px", fontSize: 11 }}>
              {vacuuming ? "Running…" : "Run Vacuum"}
            </button>
          )}
          {onNavigate && (
            <button className="button-secondary" onClick={onNavigate} style={{ padding: "4px 10px", fontSize: 11 }}>
              {navigateLabel || "Open"}
            </button>
          )}
          {canApply && !confirming && (
            <button className="button-secondary" onClick={() => setConfirming(true)} style={{ padding: "4px 10px", fontSize: 11 }}>
              Apply
            </button>
          )}
          {onArchive && (
            <button className="button-secondary" onClick={onArchive} style={{ padding: "4px 10px", fontSize: 11 }}>
              Archive
            </button>
          )}
          {onRestore && (
            <button className="button-secondary" onClick={onRestore} style={{ padding: "4px 10px", fontSize: 11 }}>
              Restore
            </button>
          )}
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          {finding.title}
        </span>
        <span style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text-secondary)" }}>{finding.summary}</span>
        {vacuumError && <span style={{ fontSize: 12, color: "var(--critical-text)" }}>Vacuum failed: {vacuumError}</span>}
      </div>

      {finding.occurrences && finding.occurrences.length > 0 && (
        <details>
          <summary style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent)", cursor: "pointer" }}>
            {finding.occurrences.length} matching quer{finding.occurrences.length === 1 ? "y" : "ies"}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
            {finding.occurrences.map((occ, i) => (
              <div key={i} style={{ padding: "8px 10px", background: "var(--row-bg)", borderRadius: 8 }}>
                <div
                  title={occ.query}
                  style={{
                    fontFamily: "IBM Plex Mono, monospace",
                    fontSize: 11.5,
                    color: "var(--text)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    cursor: "default",
                  }}
                >
                  {occ.query}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                  Called {occ.calls.toLocaleString()} times{occ.note ? ` · ${occ.note}` : ""}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}

      {finding.index_columns && finding.index_columns.length > 0 && (
        <details>
          <summary style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent)", cursor: "pointer" }}>
            {finding.index_columns.length} index{finding.index_columns.length === 1 ? "" : "es"} on this table
          </summary>
          <div style={{ marginTop: 6, overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={TH_STYLE}>Index</th>
                  <th style={TH_STYLE}>Columns</th>
                  <th style={TH_STYLE}>Method</th>
                  <th style={TH_STYLE}>Size</th>
                </tr>
              </thead>
              <tbody>
                {finding.index_columns.map((idx) => (
                  <tr key={idx.index_name}>
                    <td style={{ ...TD_STYLE, fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text)" }}>
                      {idx.index_name}
                      {idx.is_unique && (
                        <span
                          style={{
                            marginLeft: 6,
                            padding: "1px 6px",
                            borderRadius: 100,
                            background: "var(--healthy-bg)",
                            color: "var(--healthy-text)",
                            fontSize: 9.5,
                            fontWeight: 700,
                          }}
                        >
                          UNIQUE
                        </span>
                      )}
                    </td>
                    <td style={TD_STYLE}>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                        {idx.columns.map((col) => {
                          const c = colorForColumn(col);
                          return (
                            <span
                              key={col}
                              style={{
                                padding: "1px 7px",
                                borderRadius: 100,
                                background: c.bg,
                                color: c.text,
                                fontSize: 10.5,
                                fontWeight: 600,
                              }}
                            >
                              {col}
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td style={{ ...TD_STYLE, fontSize: 11.5, color: "var(--text-secondary)" }}>{idx.method}</td>
                    <td style={{ ...TD_STYLE, fontSize: 11.5, color: "var(--text-secondary)" }}>
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
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.02em",
              textTransform: "uppercase",
              color: "var(--text-muted)",
            }}
          >
            Column usage ({finding.covering_analysis.matched_query_count.toLocaleString()} matched quer
            {finding.covering_analysis.matched_query_count === 1 ? "y" : "ies"})
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {finding.covering_analysis.columns.map((v) => {
              const badge = COVERING_VERDICT[v.verdict] ?? COVERING_VERDICT.no_data;
              const c = colorForColumn(v.column);
              return (
                <span
                  key={v.column}
                  style={{ display: "flex", alignItems: "center", gap: 5, padding: "3px 9px 3px 3px", borderRadius: 100, background: badge.bg }}
                >
                  <span
                    style={{
                      padding: "1px 6px",
                      borderRadius: 100,
                      background: c.bg,
                      color: c.text,
                      fontSize: 10,
                      fontWeight: 700,
                    }}
                  >
                    {v.column}
                  </span>
                  <span style={{ fontSize: 10.5, fontWeight: 600, color: badge.text }}>{badge.label}</span>
                </span>
              );
            })}
          </div>
        </div>
      )}

      {finding.extensions && finding.extensions.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {finding.extensions.map((ext) => (
            <span
              key={ext.name}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 5,
                padding: "3px 9px",
                borderRadius: 100,
                background: "var(--row-bg)",
                fontFamily: "IBM Plex Mono, monospace",
                fontSize: 11.5,
              }}
            >
              <span style={{ color: "var(--text)", fontWeight: 650 }}>{ext.name}</span>
              <span style={{ color: "var(--text-muted)" }}>{ext.version}</span>
            </span>
          ))}
        </div>
      )}

      {finding.id.startsWith("covering-index-") && targetId && onApplyAnalysis && (
        <IndexCoverageAnalysisPanel targetId={targetId} findingId={finding.id} onApplyAnalysis={onApplyAnalysis} />
      )}

      {mode === "advanced" && (
        <details open>
          <summary style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent)", cursor: "pointer" }}>
            Technical detail
          </summary>
          <div style={{ marginTop: 6, padding: "8px 10px", background: "var(--row-bg)", borderRadius: 8 }}>
            <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text-secondary)" }}>
              {finding.detail}
            </span>
          </div>
        </details>
      )}

      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Suggested: {finding.suggested_action}</span>

      {confirming && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            padding: "10px 12px",
            background: "var(--attention-bg)",
            borderRadius: 8,
          }}
        >
          <span style={{ fontSize: 12, fontWeight: 650, color: "var(--attention-text)" }}>
            Run this against your database? This changes the target directly.
          </span>
          <pre
            style={{
              margin: 0,
              padding: "8px 10px",
              background: "var(--row-bg)",
              borderRadius: 8,
              fontFamily: "IBM Plex Mono, monospace",
              fontSize: 11.5,
              color: "var(--text)",
              overflowX: "auto",
              whiteSpace: "pre",
            }}
          >
            {finding.recommended_ddl}
          </pre>
          {applyError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{applyError}</span>}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="button-danger" onClick={handleConfirmApply} disabled={applying}>
              {applying ? "Running…" : "Confirm & run"}
            </button>
            <button className="button-secondary" onClick={() => setConfirming(false)} disabled={applying}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {finding.recommended_ddl && !confirming && <DdlBlock ddl={finding.recommended_ddl} applyAvailable={canApply} />}

      {archivedAt && (
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          Archived {new Date(archivedAt).toLocaleString()}
        </span>
      )}
    </div>
  );
}

function DdlBlock({ ddl, applyAvailable }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard
      .writeText(ddl)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: "0.02em",
            textTransform: "uppercase",
            color: "var(--text-muted)",
          }}
        >
          {applyAvailable ? "Suggested DDL — use Apply above, or copy to run yourself" : "Suggested DDL — not applied automatically"}
        </span>
        <button className="button-secondary" onClick={handleCopy} style={{ padding: "4px 10px", fontSize: 11 }}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: "8px 10px",
          background: "var(--row-bg)",
          borderRadius: 8,
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 11.5,
          color: "var(--text)",
          overflowX: "auto",
          whiteSpace: "pre",
        }}
      >
        {ddl}
      </pre>
    </div>
  );
}
