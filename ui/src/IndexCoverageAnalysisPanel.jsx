import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert } from "./components";
import styles from "./IndexCoverageAnalysisPanel.module.css";

const POLL_INTERVAL_MS = 3000;

// Inline "Analyze with AI" flow for a Covering Index Candidate finding —
// unlike QueryAnalysisModal (which sends the user to a separate AI Analysis
// tab to check back later), this polls right here and shows the result
// under the finding it's about, since a covering-index verdict only makes
// sense in the context of that one index.
export default function IndexCoverageAnalysisPanel({ targetId, findingId, onApplyAnalysis }) {
  // The analysis this panel started (as returned by the create call). Until
  // then nothing is fetched or polled.
  const [created, setCreated] = useState(null);
  const [starting, setStarting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState(null);

  // Polls the finding's analyses until the one we started leaves "pending".
  // Poll failures are ignored, as before — the next tick tries again.
  const { data } = useQuery({
    queryKey: targetKey(targetId, "index-coverage-analyses", findingId),
    queryFn: ({ signal }) => api.getIndexCoverageAnalyses(targetId, findingId, { signal }),
    enabled: Boolean(created),
    staleTime: 0,
    refetchInterval: (query) => {
      const latest = query.state.data?.analyses.find((a) => a.id === created?.id);
      return latest && latest.status !== "pending" ? false : POLL_INTERVAL_MS;
    },
  });

  const analysis = (created && data?.analyses.find((a) => a.id === created.id)) ?? created;

  const handleStart = () => {
    setStarting(true);
    setError(null);
    api
      .createIndexCoverageAnalysis(targetId, findingId)
      .then(setCreated)
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
      <>
        <button
          type="button"
          className={`button-secondary button-sm ${styles.selfStart}`}
          onClick={handleStart}
          disabled={starting}
        >
          {starting ? "Starting…" : "Analyze with AI"}
        </button>
        {error && (
          <Alert tone="critical" compact>
            {error}
          </Alert>
        )}
      </>
    );
  }

  return (
    <div className={styles.panel}>
      <h4 className={styles.heading}>AI Analysis</h4>

      {analysis.status === "pending" && (
        <p role="status" className={styles.muted}>
          Running…
        </p>
      )}

      {analysis.status === "error" && (
        <Alert tone="critical" compact>
          {analysis.error}
        </Alert>
      )}

      {analysis.status === "done" && (
        <>
          <p className={styles.response}>{analysis.ai_response}</p>
          {analysis.recommended_ddl ? (
            <>
              <pre className={styles.code}>{analysis.recommended_ddl}</pre>
              <button
                type="button"
                className={`button-primary button-sm ${styles.selfStart}`}
                onClick={handleApply}
                disabled={applying}
              >
                {applying ? "Applying…" : "Apply"}
              </button>
            </>
          ) : (
            <p className={styles.muted}>The AI didn't recommend a change — see its reasoning above.</p>
          )}
        </>
      )}

      {error && (
        <Alert tone="critical" compact>
          {error}
        </Alert>
      )}
    </div>
  );
}
