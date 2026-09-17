import { useEffect, useState } from "react";
import { api } from "./api";
import FindingCard from "./FindingCard";

export default function Archive({ targetId }) {
  const [archived, setArchived] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setError(null);
    api
      .getArchivedFindings(targetId)
      .then((data) => setArchived(data.findings))
      .catch((err) => setError(err.message));
  }, [targetId]);

  const handleRestore = (entry) => {
    api
      .restoreFinding(targetId, entry.finding_id)
      .then(() => setArchived((prev) => prev.filter((a) => a.finding_id !== entry.finding_id)))
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
          Archived findings
        </span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
          {archived ? `${archived.length} archived` : ""}
        </span>
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {archived && archived.length === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
          Nothing archived yet — archive a finding from the Advisor screen to see it here.
        </span>
      )}

      {archived && archived.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {archived.map((entry) => (
            <FindingCard
              key={entry.id}
              finding={entry.finding}
              archivedAt={entry.archived_at}
              onRestore={() => handleRestore(entry)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
