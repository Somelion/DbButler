import { useEffect, useRef, useState } from "react";
import { api } from "./api";

const POLL_INTERVAL_MS = 3000;

// Inline "Analyze with AI" flow for a Covering Index Candidate finding —
// unlike QueryAnalysisModal (which sends the user to a separate AI Analysis
// tab to check back later), this polls right here and shows the result
// under the finding it's about, since a covering-index verdict only makes
// sense in the context of that one index.
export default function IndexCoverageAnalysisPanel({ targetId, findingId, onApplyAnalysis }) {
  const [analysis, setAnalysis] = useState(null);
  const [starting, setStarting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const pollUntilDone = (analysisId) => {
    pollRef.current = setInterval(() => {
      api
        .getIndexCoverageAnalyses(targetId, findingId)
        .then((data) => {
          const latest = data.analyses.find((a) => a.id === analysisId) ?? data.analyses[0];
          if (!latest) return;
          setAnalysis(latest);
          if (latest.status !== "pending") clearInterval(pollRef.current);
        })
        .catch(() => {});
    }, POLL_INTERVAL_MS);
  };

  const handleStart = () => {
    setStarting(true);
    setError(null);
    api
      .createIndexCoverageAnalysis(targetId, findingId)
      .then((created) => {
        setAnalysis(created);
        pollUntilDone(created.id);
      })
      .catch((err) => setError(err.message))
      .finally(() => setStarting(false));
  };

  const handleApply = () => {
    if (!analysis?.recommended_ddl) return;
    setApplying(true);
    setError(null);
    Promise.resolve(onApplyAnalysis(analysis.id))
      .catch((err) => setError(err.message))
      .finally(() => setApplying(false));
  };

  if (!analysis) {
    return (
      <button
        className="button-secondary"
        onClick={handleStart}
        disabled={starting}
        style={{ padding: "4px 10px", fontSize: 11, alignSelf: "flex-start" }}
      >
        {starting ? "Starting…" : "Analyze with AI"}
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "10px 12px", background: "var(--row-bg)", borderRadius: 8 }}>
      <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.02em", textTransform: "uppercase", color: "var(--text-muted)" }}>
        AI Analysis
      </span>

      {analysis.status === "pending" && <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Running…</span>}

      {analysis.status === "error" && <span style={{ fontSize: 12, color: "var(--critical-text)" }}>{analysis.error}</span>}

      {analysis.status === "done" && (
        <>
          <span style={{ fontSize: 12.5, color: "var(--text)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
            {analysis.ai_response}
          </span>
          {analysis.recommended_ddl ? (
            <>
              <pre
                style={{
                  margin: 0,
                  padding: "8px 10px",
                  background: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  fontFamily: "IBM Plex Mono, monospace",
                  fontSize: 11.5,
                  color: "var(--text)",
                  overflowX: "auto",
                  whiteSpace: "pre",
                }}
              >
                {analysis.recommended_ddl}
              </pre>
              <button
                className="button-primary"
                onClick={handleApply}
                disabled={applying}
                style={{ alignSelf: "flex-start", padding: "4px 10px", fontSize: 11 }}
              >
                {applying ? "Applying…" : "Apply"}
              </button>
            </>
          ) : (
            <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
              The AI didn't recommend a change — see its reasoning above.
            </span>
          )}
        </>
      )}

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
    </div>
  );
}
