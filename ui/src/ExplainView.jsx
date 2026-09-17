import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { formatMs } from "./format";
import FindingCard from "./FindingCard";
import QueryParamsForm from "./QueryParamsForm";
import { applyPlaceholderValues, extractPlaceholders } from "./queryParams";

export default function ExplainView({ targetId, seedQuery, onSeedConsumed }) {
  const [queryText, setQueryText] = useState("");
  const [paramValues, setParamValues] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (seedQuery) {
      setQueryText(seedQuery);
      setParamValues({});
      onSeedConsumed?.();
    }
  }, [seedQuery, onSeedConsumed]);

  const placeholders = useMemo(() => extractPlaceholders(queryText), [queryText]);
  const allParamsFilled = placeholders.every((n) => (paramValues[n] ?? "").trim() !== "");

  const handleRun = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const finalQuery = placeholders.length > 0 ? applyPlaceholderValues(queryText, paramValues) : queryText;
      const data = await api.explainQuery(targetId, finalQuery);
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
        gap: 12,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>EXPLAIN ANALYZE</span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
          Paste a read-only SELECT query — this actually runs it, so only SELECT / WITH queries are accepted.
        </span>
      </div>

      <textarea
        value={queryText}
        onChange={(e) => setQueryText(e.target.value)}
        rows={4}
        placeholder="SELECT ..."
        style={{
          fontFamily: "IBM Plex Mono, monospace",
          fontSize: 12.5,
          color: "var(--text)",
          padding: 10,
          border: "1px solid var(--border)",
          borderRadius: 8,
          resize: "vertical",
        }}
      />

      <QueryParamsForm
        placeholders={placeholders}
        values={paramValues}
        onChange={(n, value) => setParamValues((prev) => ({ ...prev, [n]: value }))}
      />

      <div>
        <button
          className="button-primary"
          onClick={handleRun}
          disabled={running || !queryText.trim() || !allParamsFilled}
        >
          {running ? "Running…" : "Run EXPLAIN ANALYZE"}
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
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Execution time: {result.execution_time_ms != null ? formatMs(result.execution_time_ms) : "—"}
          </span>

          {result.findings.length === 0 ? (
            <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No red flags found in this plan.</span>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {result.findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </div>
          )}

          <details>
            <summary style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent)", cursor: "pointer" }}>
              Raw plan
            </summary>
            <pre
              style={{
                marginTop: 6,
                padding: "10px 12px",
                background: "var(--row-bg)",
                borderRadius: 8,
                fontFamily: "IBM Plex Mono, monospace",
                fontSize: 11.5,
                color: "var(--text-secondary)",
                overflowX: "auto",
                whiteSpace: "pre",
              }}
            >
              {result.plan_text}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}
