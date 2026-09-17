import { useEffect, useState } from "react";
import { api } from "./api";
import FindingCard from "./FindingCard";

const POLL_INTERVAL_MS = 15000;

export default function PlanRegressions({ targetId }) {
  const [findings, setFindings] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setError(null);
    setFindings(null);

    const load = () => {
      api
        .getPlanRegressions(targetId)
        .then((data) => setFindings(data.findings))
        .catch((err) => setError(err.message));
    };

    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [targetId]);

  const handleArchive = (finding) => {
    api
      .archiveFinding(targetId, finding)
      .then(() => setFindings((prev) => prev.filter((f) => f.id !== finding.id)))
      .catch((err) => setError(err.message));
  };

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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 14, color: "var(--text)" }}>
          Plan regressions
        </span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
          {findings ? `${findings.length} finding${findings.length === 1 ? "" : "s"}` : ""}
        </span>
      </div>
      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
        Tracked queries (Query History's top 50) whose EXPLAIN plan shape changed and got meaningfully
        slower since — not queries that are simply slow, only ones that got structurally worse.
      </span>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {findings && findings.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          No plan regressions detected. This needs at least two plan captures and a few latency samples
          per query, so a freshly connected target may not have enough history yet.
        </span>
      )}

      {findings && findings.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {findings.map((finding) => (
            <FindingCard key={finding.id} finding={finding} onArchive={() => handleArchive(finding)} />
          ))}
        </div>
      )}
    </div>
  );
}
