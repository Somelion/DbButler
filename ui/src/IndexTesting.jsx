import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import styles from "./IndexTesting.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Badge, Card, EmptyState, Skeleton, useConfirm } from "./components";
import { formatBytes, formatRelativeTime } from "./format";

// Verdict pill: text label plus tone, so it never relies on colour alone.
const VERDICT = {
  being_used: { label: "Being used", tone: "healthy" },
  not_used_yet: { label: "Not used yet", tone: "attention" },
  too_early: { label: "Too early to tell", tone: "neutral" },
};

export default function IndexTesting({ targetId }) {
  const { data, error, isPending } = useQuery({
    queryKey: targetKey(targetId, "index-testing"),
    queryFn: ({ signal }) => api.getIndexTesting(targetId, { signal }),
  });
  const indexes = data?.indexes ?? null;

  return (
    <Card
      title="Tracked indexes"
      description="Indexes you've added from Index Advisor's Apply button, and whether they're actually earning their keep. Once you're happy with one, add it to your own migration tooling (Liquibase, etc.) — dropping it here only removes it from this database, not from anywhere else."
    >
      {error && <Alert tone="critical">{error.message}</Alert>}

      {isPending && <Skeleton lines={4} label="Loading tracked indexes…" />}

      {indexes && indexes.length === 0 && (
        <EmptyState icon={FlaskConical} title="Nothing here yet">
          Use Apply on an Index Advisor finding to create an index and start tracking it.
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
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [dropping, setDropping] = useState(false);
  const [dropError, setDropError] = useState(null);
  const headingId = useId();
  const verdict = VERDICT[idx.verdict] ?? VERDICT.too_early;

  const handleDrop = async () => {
    const confirmed = await confirm({
      title: `Drop ${idx.index_name}?`,
      message: `Drop ${idx.index_name} from ${idx.schema_name}.${idx.table_name}? This runs against the target immediately and can't be undone from here.`,
      confirmLabel: "Drop index",
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
            on {idx.schema_name}.{idx.table_name} · added {formatRelativeTime(idx.created_at)}
          </span>
        </div>
        <Badge tone={verdict.tone} className={styles.verdict}>
          {verdict.label}
        </Badge>
      </div>

      <dl className={styles.metrics}>
        <Metric label="Scans since added" value={idx.scans_since_added.toLocaleString()} />
        <Metric label="Index size" value={formatBytes(idx.index_bytes)} />
        <Metric
          label="Table's seq scans since added"
          value={idx.seq_scans_since_added.toLocaleString()}
          hint="Raw count, not a rate — for a real before/after trend, watch this table's Seq-Scan-Heavy finding over time too."
        />
      </dl>

      {dropError && (
        <Alert tone="critical" compact>
          {dropError}
        </Alert>
      )}

      <button type="button" className={`button-danger button-sm ${styles.drop}`} onClick={handleDrop} disabled={dropping}>
        {dropping ? "Dropping…" : "Drop this index"}
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
