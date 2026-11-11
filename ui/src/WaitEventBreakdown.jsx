import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Card, Skeleton } from "./components";
import styles from "./WaitEventBreakdown.module.css";

const POLL_INTERVAL_MS = 15000;

// Fixed, semantic colors for Postgres's own wait_event_type categories —
// not cycled from MULTI_TREND_PALETTE, since "Lock" should always read as
// the same color a DBA associates with a blocking problem, not whatever
// slot it happened to land in. Values are --chart-* tokens (light/dark
// variants live in tokens.css); the legend always names each category, so
// colour is never the only cue.
const CATEGORY_COLORS = {
  CPU: "var(--chart-1)",
  Lock: "var(--chart-6)",
  LWLock: "var(--chart-7)",
  IO: "var(--chart-5)",
  IPC: "var(--chart-4)",
  BufferPin: "var(--chart-3)",
};
const FALLBACK_COLOR = "var(--unknown-solid)"; // Timeout, Client, Extension, anything unlisted

export default function WaitEventBreakdown({ targetId }) {
  // Polled through the query cache; the interval pauses in hidden tabs.
  const { data, error, isPending } = useQuery({
    queryKey: targetKey(targetId, "wait-events"),
    queryFn: ({ signal }) => api.getWaitEvents(targetId, { signal }),
    refetchInterval: POLL_INTERVAL_MS,
  });

  return (
    <Card
      title="Active Sessions by Wait State"
      description="What's actually running right now is waiting on, for fast triage."
      actions={
        data && (
          <span className={styles.total}>
            {data.total_active}
            <span className="sr-only"> active sessions</span>
          </span>
        )
      }
    >
      {isPending && <Skeleton lines={2} label="Loading wait states…" />}

      {error && (
        <Alert tone="critical" compact>
          {error.message}
        </Alert>
      )}

      {data && data.total_active === 0 && (
        <p className={styles.empty}>No active sessions right now — everything's idle.</p>
      )}

      {data && data.total_active > 0 && (
        <>
          {/* Visual summary only — the legend below carries the same numbers as text. */}
          <div className={styles.bar} aria-hidden="true">
            {data.rows.map((r) => (
              <span
                key={r.category}
                title={`${r.category}: ${r.count}`}
                className={styles.segment}
                style={{
                  "--segment-width": `${(100 * r.count) / data.total_active}%`,
                  "--segment-color": CATEGORY_COLORS[r.category] ?? FALLBACK_COLOR,
                }}
              />
            ))}
          </div>
          <ul className={styles.legend}>
            {data.rows.map((r) => (
              <li
                key={r.category}
                className={styles.legendItem}
                style={{ "--segment-color": CATEGORY_COLORS[r.category] ?? FALLBACK_COLOR }}
              >
                <span className={styles.swatch} aria-hidden="true" />
                <span className={styles.legendLabel}>{r.category}</span>
                <span className={styles.legendCount}>{r.count}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
