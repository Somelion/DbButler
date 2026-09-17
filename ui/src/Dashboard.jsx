import { useEffect, useState } from "react";
import { api } from "./api";
import TrendChart from "./TrendChart";

const POLL_INTERVAL_MS = 10000;

export default function Dashboard({ targetId }) {
  const [points, setPoints] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const data = await api.getMetric(targetId, "cache_hit_ratio");
        if (!cancelled) {
          setPoints(data.points);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [targetId]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "18px 20px",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 14,
      }}
    >
      <TrendChart bare label="Cache Hit Rate" unit="%" points={points} />
      <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
        {points.length} sample{points.length === 1 ? "" : "s"} collected &middot; refreshes every{" "}
        {POLL_INTERVAL_MS / 1000}s
      </span>
      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}
    </div>
  );
}
