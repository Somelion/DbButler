import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import QueryParamsForm from "./QueryParamsForm";
import { applyPlaceholderValues, extractPlaceholders, splitOnPlaceholders } from "./queryParams";

export default function QueryAnalysisModal({ targetId, query, onClose, onCreated }) {
  const [anonymize, setAnonymize] = useState(true);
  const [paramValues, setParamValues] = useState({});
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(null);

  const placeholders = useMemo(() => extractPlaceholders(query), [query]);
  const allParamsFilled = placeholders.every((n) => (paramValues[n] ?? "").trim() !== "");
  const finalQuery = useMemo(
    () => (placeholders.length > 0 ? applyPlaceholderValues(query, paramValues) : query),
    [query, paramValues, placeholders.length]
  );

  useEffect(() => {
    if (placeholders.length > 0 && !allParamsFilled) {
      setPreview(null);
      return;
    }
    setPreviewing(true);
    api
      .anonymizeQueryPreview(targetId, finalQuery)
      .then((data) => setPreview(data.anonymized_query))
      .catch((err) => setError(err.message))
      .finally(() => setPreviewing(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, finalQuery, allParamsFilled, placeholders.length]);

  const handleRunAnalysis = () => {
    setSubmitting(true);
    setError(null);
    api
      .createQueryAnalysis(targetId, { query: finalQuery, anonymize })
      .then((analysis) => {
        onCreated?.(analysis);
        setSubmitted(true);
      })
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  };

  const displayedQuery = anonymize && preview !== null ? preview : finalQuery;

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "oklch(20% 0.01 250 / 55%)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 14,
          width: "100%",
          maxWidth: 680,
          maxHeight: "85vh",
          overflow: "auto",
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 14,
          padding: "20px 22px",
          boxSizing: "border-box",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontFamily: "Manrope, sans-serif", fontWeight: 650, fontSize: 15, color: "var(--text)" }}>
            Analyze with AI
          </span>
          <button className="button-secondary" onClick={onClose} style={{ padding: "3px 10px", fontSize: 11 }}>
            Close
          </button>
        </div>

        {!submitted && (
          <>
            <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
              {placeholders.length > 0 && !allParamsFilled
                ? "Query template — fill in a value for each $N placeholder below to see the query that will actually run."
                : anonymize
                  ? "Table and column names below are anonymized before anything is sent to the AI provider."
                  : "This is the exact query text that will be sent to the AI provider, unmodified."}
            </span>

            <pre
              style={{
                margin: 0,
                padding: "12px 14px",
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: 10,
                fontFamily: "IBM Plex Mono, monospace",
                fontSize: 12,
                color: "var(--text)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 220,
                overflow: "auto",
              }}
            >
              {placeholders.length > 0 && !allParamsFilled
                ? splitOnPlaceholders(query).map((part, i) =>
                    part.type === "placeholder" ? (
                      <span
                        key={i}
                        style={{
                          background: "var(--accent-tint)",
                          color: "var(--accent)",
                          fontWeight: 700,
                          borderRadius: 4,
                          padding: "0 2px",
                        }}
                      >
                        {part.value}
                      </span>
                    ) : (
                      <span key={i}>{part.value}</span>
                    )
                  )
                : previewing
                  ? "Anonymizing…"
                  : displayedQuery}
            </pre>

            <QueryParamsForm
              placeholders={placeholders}
              values={paramValues}
              onChange={(n, value) => setParamValues((prev) => ({ ...prev, [n]: value }))}
            />

            {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

            <div style={{ display: "flex", gap: 8 }}>
              <button
                className={anonymize ? "button-primary" : "button-secondary"}
                onClick={() => setAnonymize((a) => !a)}
              >
                {anonymize ? "✓ Anonymize table/column names" : "Anonymize table/column names"}
              </button>
              <button
                className="button-primary"
                onClick={handleRunAnalysis}
                disabled={submitting || previewing || !allParamsFilled}
              >
                {submitting ? "Starting…" : "Run Analysis"}
              </button>
            </div>

            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              Runs EXPLAIN (ANALYZE, BUFFERS) against your database, then sends the query and plan to the AI
              provider configured in Settings. Only literal values are already redacted (pg_stat_statements
              normalizes those to $1/$2/... before this app ever sees the query) — anonymizing here additionally
              hides real table/column names.
            </span>
          </>
        )}

        {submitted && (
          <div
            style={{
              padding: "12px 14px",
              background: "var(--healthy-bg)",
              border: "1px solid var(--healthy-border)",
              borderRadius: 10,
            }}
          >
            <span style={{ fontSize: 12.5, color: "var(--healthy-text)" }}>
              Analysis started. It's running in the background — you'll see a notification on the AI Analysis
              tab when it's ready, or check there any time.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
