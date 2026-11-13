import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FlaskConical } from "lucide-react";
import styles from "./IndexTesting.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Badge, Card, EmptyState, Skeleton, useConfirm } from "./components";
import { formatBytes, formatRelativeTime } from "./format";

// Verdict pill: text label plus tone, so it never relies on colour alone.
// label is a translation key, translated at render.
const VERDICT = {
  being_used: { label: "verdicts.being_used", tone: "healthy" },
  not_used_yet: { label: "verdicts.not_used_yet", tone: "attention" },
  too_early: { label: "verdicts.too_early", tone: "neutral" },
};

export default function IndexTesting({ targetId }) {
  const { t } = useTranslation("indexTesting");
  const { data, error, isPending } = useQuery({
    queryKey: targetKey(targetId, "index-testing"),
    queryFn: ({ signal }) => api.getIndexTesting(targetId, { signal }),
  });
  const indexes = data?.indexes ?? null;

  return (
    <Card title={t("title")} description={t("description")}>
      {error && <Alert tone="critical">{error.message}</Alert>}

      {isPending && <Skeleton lines={4} label={t("loading")} />}

      {indexes && indexes.length === 0 && (
        <EmptyState icon={FlaskConical} title={t("empty.title")}>
          {t("empty.body")}
        </EmptyState>
      )}

      {indexes && indexes.length > 0 && (
        <div className={styles.list}>
          {indexes.map((idx) => (
            <TrackedIndexCard key={idx.id} idx={idx} targetId={targetId} />
          ))}
        </div>
      )}
    </Card>
  );
}

function TrackedIndexCard({ idx, targetId }) {
  const { t } = useTranslation("indexTesting");
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [dropping, setDropping] = useState(false);
  const [dropError, setDropError] = useState(null);
  const headingId = useId();
  const verdict = VERDICT[idx.verdict] ?? VERDICT.too_early;

  const handleDrop = async () => {
    const confirmed = await confirm({
      title: t("drop.confirmTitle", { name: idx.index_name }),
      message: t("drop.confirmMessage", {
        name: idx.index_name,
        table: `${idx.schema_name}.${idx.table_name}`,
      }),
      confirmLabel: t("drop.confirmLabel"),
      tone: "danger",
    });
    if (!confirmed) return;
    setDropping(true);
    setDropError(null);
    try {
      await api.dropTrackedIndex(targetId, idx.id);
      await queryClient.invalidateQueries({ queryKey: targetKey(targetId, "index-testing") });
    } catch (err) {
      setDropError(err.message);
    } finally {
      setDropping(false);
    }
  };

  return (
    <article className={styles.item} aria-labelledby={headingId}>
      <div className={styles.itemHeader}>
        <div className={styles.itemTitle}>
          <h3 id={headingId} className={styles.indexName}>
            {idx.index_name}
          </h3>
          <span className={styles.meta}>
            {t("meta", {
              table: `${idx.schema_name}.${idx.table_name}`,
              time: formatRelativeTime(idx.created_at),
            })}
          </span>
        </div>
        <Badge tone={verdict.tone} className={styles.verdict}>
          {t(verdict.label)}
        </Badge>
      </div>

      <dl className={styles.metrics}>
        <Metric label={t("metrics.scans")} value={idx.scans_since_added.toLocaleString()} />
        <Metric label={t("metrics.size")} value={formatBytes(idx.index_bytes)} />
        <Metric
          label={t("metrics.seqScans")}
          value={idx.seq_scans_since_added.toLocaleString()}
          hint={t("metrics.seqScansHint")}
        />
      </dl>

      {dropError && (
        <Alert tone="critical" compact>
          {dropError}
        </Alert>
      )}

      <button type="button" className={`button-danger button-sm ${styles.drop}`} onClick={handleDrop} disabled={dropping}>
        {dropping ? t("drop.dropping") : t("drop.label")}
      </button>
    </article>
  );
}

function Metric({ label, value, hint }) {
  return (
    <div className={styles.metric} title={hint}>
      <dt className={styles.metricLabel}>{label}</dt>
      <dd className={styles.metricValue}>{value}</dd>
    </div>
  );
}
