import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "./api";
import { formatMs } from "./format";
import FindingCard from "./FindingCard";
import QueryParamsForm from "./QueryParamsForm";
import { applyPlaceholderValues, extractPlaceholders } from "./queryParams";
import { Workflow } from "lucide-react";
import { Alert, Card, Dialog, Field, PlanViewerFrame } from "./components";
import styles from "./ExplainView.module.css";

export default function ExplainView({ targetId, seedQuery, onSeedConsumed }) {
  const [queryText, setQueryText] = useState("");
  const [paramValues, setParamValues] = useState({});
  const [viewerOpen, setViewerOpen] = useState(false);

  // A query handed over from the Query Intelligence table replaces whatever
  // is in the editor. Taken into local state while rendering (React's
  // "adjust state on prop change" pattern), then the parent is told it was
  // consumed so it can clear seedQuery — which lets the same query be
  // loaded again later.
  const [prevSeed, setPrevSeed] = useState(null);
  if (seedQuery !== prevSeed) {
    setPrevSeed(seedQuery);
    if (seedQuery) {
      setQueryText(seedQuery);
      setParamValues({});
    }
  }

  useEffect(() => {
    if (seedQuery) onSeedConsumed?.();
  }, [seedQuery, onSeedConsumed]);

  const placeholders = useMemo(() => extractPlaceholders(queryText), [queryText]);
  const allParamsFilled = placeholders.every((n) => (paramValues[n] ?? "").trim() !== "");

  // EXPLAIN ANALYZE actually runs the query, so it's only ever user-triggered:
  // a mutation, not a query. Starting a new run clears the previous result.
  const explain = useMutation({
    mutationFn: (finalQuery) => api.explainQuery(targetId, finalQuery),
  });
  const result = explain.data;
  const canRun = !explain.isPending && queryText.trim() !== "" && allParamsFilled;

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!canRun) return;
    explain.mutate(placeholders.length > 0 ? applyPlaceholderValues(queryText, paramValues) : queryText);
  };

  return (
    <Card
      title="EXPLAIN ANALYZE"
      description="Paste a read-only SELECT query — this actually runs it, so only SELECT / WITH queries are accepted."
    >
      <form className={styles.form} onSubmit={handleSubmit}>
        <Field label="Query">
          <textarea
            className={styles.queryInput}
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            rows={4}
            placeholder="SELECT ..."
          />
        </Field>

        <QueryParamsForm
          placeholders={placeholders}
          values={paramValues}
          onChange={(n, value) => setParamValues((prev) => ({ ...prev, [n]: value }))}
        />

        <div className={styles.actions}>
          <button type="submit" className="button-primary" disabled={!canRun}>
            {explain.isPending ? "Running…" : "Run EXPLAIN ANALYZE"}
          </button>
        </div>
      </form>

      {explain.error && <Alert tone="critical">{explain.error.message}</Alert>}

      {result && (
        <div className={styles.result}>
          <p className={styles.meta}>
            Execution time: {result.execution_time_ms != null ? formatMs(result.execution_time_ms) : "—"}
          </p>

          {result.findings.length === 0 ? (
            <p className={styles.empty}>No red flags found in this plan.</p>
          ) : (
            <div className={styles.findings}>
              {result.findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </div>
          )}

          <div className={styles.actions}>
            <button type="button" className="button-secondary button-sm" onClick={() => setViewerOpen(true)}>
              <Workflow size={13} aria-hidden="true" />
              Open in plan viewer
            </button>
          </div>

          <details>
            <summary className={styles.rawSummary}>Raw plan</summary>
            <pre className={styles.rawPlan}>{result.plan_text}</pre>
          </details>
        </div>
      )}

      <Dialog
        open={viewerOpen && Boolean(result)}
        onClose={() => setViewerOpen(false)}
        size="xl"
        title="Plan viewer"
        description="pev2 by Dalibo — node timings, buffers and the critical path of this EXPLAIN ANALYZE run."
      >
        {result && <PlanViewerFrame plan={result.plan_text} query={explain.variables ?? ""} />}
      </Dialog>
    </Card>
  );
}
