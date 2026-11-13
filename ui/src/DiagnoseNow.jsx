import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Stethoscope } from "lucide-react";
import { api } from "./api";
import { Alert, Card, SeverityIcon, Skeleton } from "./components";
import FindingCard from "./FindingCard";
import { useFirstSeenMap } from "./useFirstSeenMap";
import styles from "./DiagnoseNow.module.css";

export default function DiagnoseNow({ targetId }) {
  const { t } = useTranslation("dashboard");
  // The last successful run stays on screen while a re-run is in flight or
  // after a re-run fails. A stale diagnosis for a different target is worse
  // than showing nothing — DashboardScreen is keyed by target, so switching
  // database remounts this with no result rather than leaving the last-run
  // result under a new target's header.
  const [result, setResult] = useState(null);
  const [firstSeenByFindingId] = useFirstSeenMap(targetId);

  // User-triggered, so a mutation rather than a query: nothing runs until
  // "Diagnose Now" is clicked, and nothing is cached across visits.
  const diagnosis = useMutation({
    mutationFn: () => api.diagnose(targetId),
    onSuccess: setResult,
  });
  const running = diagnosis.isPending;

  return (
    <Card
      title={t("diagnose.title")}
      description={t("diagnose.description")}
      actions={
        <button type="button" className="button-primary" onClick={() => diagnosis.mutate()} disabled={running}>
          <Stethoscope size={14} aria-hidden="true" />
          {running ? t("diagnose.running") : t("diagnose.button")}
        </button>
      }
    >
      {diagnosis.error && (
        <Alert tone="critical" compact>
          {diagnosis.error.message}
        </Alert>
      )}

      {running && !result && <Skeleton lines={4} label={t("diagnose.loading")} />}

      {result && (
        <div className={styles.result}>
          {result.top_finding ? (
            <FindingCard finding={result.top_finding} firstSeenAt={firstSeenByFindingId[result.top_finding.id]} />
          ) : (
            <p className={styles.muted}>{t("diagnose.nothingStandsOut")}</p>
          )}

          <ol className={styles.steps} aria-label={t("diagnose.steps")}>
            {result.steps.map((step) => (
              <li key={step.step_number} className={styles.step}>
                {/* Icon shape + its accessible label carry the status, not just its colour. */}
                <SeverityIcon severity={step.severity} size={14} />
                <span className={styles.stepLabel}>
                  {step.step_number}. {step.label}
                </span>
                <span className={styles.stepValue}>{step.value}</span>
                <span className={styles.stepDetail}>{step.detail}</span>
              </li>
            ))}
          </ol>

          {result.top_queries.length > 0 && (
            <div className={styles.queries}>
              <h3 className={styles.queriesTitle}>{t("diagnose.slowestQueries")}</h3>
              <ul className={styles.queryList}>
                {result.top_queries.slice(0, 3).map((q, i) => (
                  <li key={i} title={q.query} className={styles.query}>
                    {q.query}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
