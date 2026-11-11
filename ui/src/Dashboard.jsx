import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Card, Skeleton } from "./components";
import TrendChart from "./TrendChart";
import styles from "./Dashboard.module.css";

const POLL_INTERVAL_MS = 10000;
const NO_POINTS = [];

export default function Dashboard({ targetId }) {
  // Polled through the query cache; the interval pauses in hidden tabs.
  const { data, error, isPending } = useQuery({
    queryKey: targetKey(targetId, "metrics", "cache_hit_ratio"),
    queryFn: ({ signal }) => api.getMetric(targetId, "cache_hit_ratio", { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });
  const points = data?.points ?? NO_POINTS;

  return (
    <Card>
      {isPending ? (
        <Skeleton height={170} label="Loading cache hit rate…" />
      ) : (
        <TrendChart bare label="Cache Hit Rate" unit="%" points={points} />
      )}
      <span className={styles.meta}>
        {points.length} sample{points.length === 1 ? "" : "s"} collected &middot; refreshes every{" "}
        {POLL_INTERVAL_MS / 1000}s
      </span>
      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}
    </Card>
  );
}
