import { useEffect, useState } from "react";
import { api } from "./api";
import FindingCard from "./FindingCard";
import { useFirstSeenMap } from "./useFirstSeenMap";

const SEVERITY_DOT = {
  critical: "oklch(52% 0.20 25)",
  attention: "oklch(60% 0.16 75)",
  healthy: "oklch(56% 0.15 150)",
  unknown: "oklch(65% 0.008 250)",
};

export default function DiagnoseNow({ targetId }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const [firstSeenByFindingId] = useFirstSeenMap(targetId);

  // A stale diagnosis for a different target is worse than showing nothing
  // — clear it on switch rather than leaving the last-run result under a
  // new target's header until the user re-clicks "Diagnose Now".
  useEffect(() => {
    setResult(null);
    setError(null);
  }, [targetId]);

  const handleRun = async () => {
    setRunning(true);
    setError(null);
    try {
      const data = await api.diagnose(targetId);
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
            Why is my database slow?
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            Runs the same checklist a DBA would, in order — locks, cache, bloat, then top queries.
          </span>
        </div>
        <button className="button-primary" onClick={handleRun} disabled={running}>
          {running ? "Diagnosing…" : "Diagnose Now"}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: "10px 12px",
            background: "var(--critical-bg)",
            border: "1px solid var(--critical-border)",
            borderRadius: 9,
            color: "var(--critical-text)",
            fontSize: 12.5,
          }}
        >
          {error}
        </div>
      )}

      {result && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {result.top_finding ? (
            <FindingCard finding={result.top_finding} firstSeenAt={firstSeenByFindingId[result.top_finding.id]} />
          ) : (
            <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>Nothing stands out right now.</span>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {result.steps.map((step) => (
              <div
                key={step.step_number}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 10px",
                  borderRadius: 8,
                  background: "var(--row-bg)",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: SEVERITY_DOT[step.severity] ?? SEVERITY_DOT.unknown,
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontSize: 11.5, fontWeight: 650, color: "var(--text)", minWidth: 170 }}>
                  {step.step_number}. {step.label}
                </span>
                <span
                  style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text-secondary)", minWidth: 50 }}
                >
                  {step.value}
                </span>
                <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>{step.detail}</span>
              </div>
            ))}
          </div>

          {result.top_queries.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.02em",
                  textTransform: "uppercase",
                  color: "var(--text-muted)",
                }}
              >
                Slowest queries right now
              </span>
              {result.top_queries.slice(0, 3).map((q, i) => (
                <span
                  key={i}
                  title={q.query}
                  style={{
                    fontFamily: "IBM Plex Mono, monospace",
                    fontSize: 11.5,
                    color: "var(--text-secondary)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {q.query}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
