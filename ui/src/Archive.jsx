import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Archive as ArchiveIcon } from "lucide-react";
import styles from "./Archive.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Card, EmptyState, Skeleton } from "./components";
import FindingCard from "./FindingCard";

export default function Archive({ targetId }) {
  const { t } = useTranslation("archive");
  const queryClient = useQueryClient();
  const [restoreError, setRestoreError] = useState(null);
  const archivedKey = targetKey(targetId, "archived-findings");

  const { data, error, isPending } = useQuery({
    queryKey: archivedKey,
    queryFn: ({ signal }) => api.getArchivedFindings(targetId, { signal }),
  });
  const archived = data?.findings ?? null;

  // A restored finding drops off this list right away, and the screens it
  // came from (Advisor, Plan Regressions, Table Health) are marked stale so
  // it shows up there again on next visit.
  const handleRestore = (entry) => {
    setRestoreError(null);
    api
      .restoreFinding(targetId, entry.finding_id)
      .then(() => {
        queryClient.setQueryData(archivedKey, (prev) =>
          prev ? { ...prev, findings: prev.findings.filter((a) => a.finding_id !== entry.finding_id) } : prev
        );
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "advisor") });
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "plan-regressions") });
        queryClient.invalidateQueries({ queryKey: targetKey(targetId, "table-health") });
      })
      .catch((err) => setRestoreError(err.message));
  };

  return (
    <Card
      title={t("title")}
      actions={archived && <span className={styles.count}>{t("count", { count: archived.length })}</span>}
    >
      {error && <Alert tone="critical">{error.message}</Alert>}
      {restoreError && <Alert tone="critical">{restoreError}</Alert>}

      {isPending && <Skeleton lines={4} label={t("loading")} />}

      {archived && archived.length === 0 && (
        <EmptyState icon={ArchiveIcon} title={t("emptyTitle")}>
          {t("emptyBody")}
        </EmptyState>
      )}

      {archived && archived.length > 0 && (
        <div className={styles.list}>
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
    </Card>
  );
}
