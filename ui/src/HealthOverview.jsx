import { useEffect, useState } from "react";
import { api } from "./api";
import FindingCard from "./FindingCard";
import { useFirstSeenMap } from "./useFirstSeenMap";

const POLL_INTERVAL_MS = 15000;

const OVERALL_LABEL = {
  healthy: "All good",
  attention: "Needs attention",
  critical: "Needs attention now",
  unknown: "Collecting data…",
};

const SEVERITY_TOKENS = {
  critical: { bg: "var(--critical-bg)", border: "var(--critical-border)", text: "var(--critical-text)", dot: "oklch(52% 0.20 25)" },
  attention: { bg: "var(--attention-bg)", border: "var(--attention-border)", text: "var(--attention-text)", dot: "oklch(60% 0.16 75)" },
  healthy: { bg: "var(--healthy-bg)", border: "var(--healthy-border)", text: "var(--healthy-text)", dot: "oklch(56% 0.15 150)" },
  unknown: { bg: "var(--row-bg)", border: "var(--border)", text: "var(--text-secondary)", dot: "oklch(65% 0.008 250)" },
};

const EMPTY_HIDDEN = new Set();

// Where a tile's key or a live finding's category should jump to — covers
// both, since tile keys (routers/dashboard_settings.py::DASHBOARD_CATEGORIES)
// and finding categories mostly overlap but not always (Replication/Backup's
// findings are reused verbatim from their Advisor modules, so their
// `category` carries the " advisor" suffix the tile's own key doesn't).
const CATEGORY_DESTINATIONS = {
  connections: { view: "activity", label: "Open Activity" },
  locks: { view: "activity", label: "Open Activity" },
  bloat: { view: "table-health", label: "Open Table Health" },
  cache: { view: "trends", label: "Open Trends" },
  checkpoints: { view: "config-tuning", label: "Open Config Tuning" },
  wraparound: { view: "table-health", label: "Open Table Health" },
  autovacuum: { view: "table-health", label: "Open Table Health" },
  replication: { view: "advisor", advisorTab: "replication", label: "Open Advisor" },
  "replication advisor": { view: "advisor", advisorTab: "replication", label: "Open Advisor" },
  backup: { view: "advisor", advisorTab: "backup", label: "Open Advisor" },
  "backup advisor": { view: "advisor", advisorTab: "backup", label: "Open Advisor" },
  pooler: { view: "connections", label: "Open Connections" },
  "query intelligence": { view: "query-intelligence", label: "Open Query Intelligence" },
};

export default function HealthOverview({ targetId, hiddenCategories = EMPTY_HIDDEN, onNavigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [firstSeenByFindingId] = useFirstSeenMap(targetId);

  useEffect(() => {
    const refresh = () =>
      api
        .getDashboard(targetId)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((err) => setError(err.message));

    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [targetId]);

  const handleVacuum = (finding) =>
    api.vacuumTable(targetId, finding.schema_name, finding.table_name, true).then(() =>
      api
        .getDashboard(targetId)
        .then(setData)
        .catch(() => {})
    );

  if (error) {
    return (
      <div
        style={{
          padding: "12px 16px",
          background: "var(--critical-bg)",
          border: "1px solid var(--critical-border)",
          borderRadius: 12,
          color: "var(--critical-text)",
          fontSize: 13,
        }}
      >
        {error}
      </div>
    );
  }
  if (!data) return null;

  const overall = SEVERITY_TOKENS[data.overall_severity] ?? SEVERITY_TOKENS.unknown;
  const visibleCategories = data.categories.filter((category) => !hiddenCategories.has(category.key));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "16px 20px",
          background: overall.bg,
          border: `1px solid ${overall.border}`,
          borderRadius: 14,
        }}
      >
        <span style={{ width: 11, height: 11, borderRadius: "50%", background: overall.dot, flexShrink: 0 }} />
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14.5, color: overall.text }}>
          {OVERALL_LABEL[data.overall_severity] ?? "Status unknown"}
        </span>
      </div>

      {visibleCategories.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
          {visibleCategories.map((category) => (
            <CategoryTile key={category.key} category={category} onNavigate={onNavigate} />
          ))}
        </div>
      )}

      {data.findings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Findings</span>
          {data.findings.map((finding) => {
            const destination = CATEGORY_DESTINATIONS[finding.category];
            return (
              <FindingCard
                key={finding.id}
                finding={finding}
                firstSeenAt={firstSeenByFindingId[finding.id]}
                onNavigate={
                  destination && onNavigate ? () => onNavigate(destination.view, { advisorTab: destination.advisorTab }) : undefined
                }
                navigateLabel={destination?.label}
                onVacuum={finding.schema_name && finding.table_name ? () => handleVacuum(finding) : undefined}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function CategoryTile({ category, onNavigate }) {
  const dot = (SEVERITY_TOKENS[category.severity] ?? SEVERITY_TOKENS.unknown).dot;
  const destination = CATEGORY_DESTINATIONS[category.key];
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "14px 16px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>{category.label}</span>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: dot, flexShrink: 0 }} />
      </div>
      <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 20, fontWeight: 600, color: "var(--text)" }}>
        {category.value}
      </span>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{category.detail}</span>
        {destination && onNavigate && (
          <span
            onClick={() => onNavigate(destination.view, { advisorTab: destination.advisorTab })}
            style={{ fontSize: 11, fontWeight: 650, color: "var(--accent)", cursor: "pointer", flexShrink: 0, whiteSpace: "nowrap" }}
          >
            View &rarr;
          </span>
        )}
      </div>
    </div>
  );
}
