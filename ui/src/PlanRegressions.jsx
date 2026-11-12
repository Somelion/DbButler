import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompare } from "lucide-react";
import styles from "./PlanRegressions.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Card, EmptyState, Skeleton } from "./components";
import FindingCard from "./FindingCard";
import PlanDiffDialog from "./PlanDiffDialog";

const POLL_INTERVAL_MS = 15000;
const FINDING_ID_PREFIX = "plan-regression-";

export default function PlanRegressions({ targetId }) {
  const queryClient = useQueryClient();
  const [archiveError, setArchiveError] = useState(null);
  // The queryid as the decimal text from the finding's id ("plan-regression-
  // <queryid>"), so a 64-bit id reaches the API unrounded.
  const [compareQueryId, setCompareQueryId] = useState(null);
  const regressionsKey = targetKey(targetId, "plan-regressions");

  const { data, error, isPending } = useQuery({
    queryKey: regressionsKey,
    queryFn: ({ signal }) => api.getPlanRegressions(targetId, { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });
  const findings = data?.findings ?? null;

  const handleArchive = (finding) => {
    setArchiveError(null);
    api
      .archiveFinding(targetId, finding)
      .then(() => {
        queryClient.setQueryData(regressionsKey, (prev) =>
          prev ? { ...prev, findings: prev.findings.filter((f) => f.id !== finding.id) } : prev
        );
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "archived-findings") });
      })
      .catch((err) => setArchiveError(err.message));
  };

  return (
    <Card
      title="Regressed queries"
      description="Tracked queries (Query History's top 50) whose EXPLAIN plan shape changed and got meaningfully slower since — not queries that are simply slow, only ones that got structurally worse."
      actions={
        findings && (
          <span className={styles.count}>
            {findings.length} finding{findings.length === 1 ? "" : "s"}
          </span>
        )
      }
    >
      {error && <Alert tone="critical">{error.message}</Alert>}
      {archiveError && <Alert tone="critical">{archiveError}</Alert>}

      {isPending && <Skeleton lines={4} label="Loading plan regressions…" />}

      {findings && findings.length === 0 && (
        <EmptyState icon={GitCompare} title="No plan regressions detected">
          This needs at least two plan captures and a few latency samples per query, so a freshly connected target
          may not have enough history yet.
        </EmptyState>
      )}

      {findings && findings.length > 0 && (
        <div className={styles.list}>
          {findings.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              onArchive={() => handleArchive(finding)}
              actions={
                <button
                  type="button"
                  className="button-secondary button-sm"
                  onClick={() => setCompareQueryId(finding.id.replace(FINDING_ID_PREFIX, ""))}
                >
                  Compare plans
                </button>
              }
            />
          ))}
        </div>
      )}

      <PlanDiffDialog
        key={compareQueryId ?? "closed"}
        targetId={targetId}
        queryidText={compareQueryId}
        onClose={() => setCompareQueryId(null)}
      />
    </Card>
  );
}
