import { useEffect, useState } from "react";
import { api } from "./api";

const POLL_INTERVAL_MS = 15000;

// Fixed, semantic colors for Postgres's own wait_event_type categories —
// not cycled from MULTI_TREND_PALETTE, since "Lock" should always read as
// the same color a DBA associates with a blocking problem, not whatever
// slot it happened to land in.
const CATEGORY_COLORS = {
  CPU: "oklch(55% 0.16 255)",
  Lock: "oklch(55% 0.18 25)",
  LWLock: "oklch(60% 0.16 75)",
  IO: "oklch(55% 0.13 200)",
  IPC: "oklch(55% 0.18 330)",
  BufferPin: "oklch(56% 0.14 150)",
};
const FALLBACK_COLOR = "oklch(65% 0.008 250)"; // Timeout, Client, Extension, anything unlisted

export default function WaitEventBreakdown({ targetId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const poll = () =>
      api
        .getWaitEvents(targetId)
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setError(null);
          }
        })
        .catch((err) => {
          if (!cancelled) setError(err.message);
        });

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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
            Active Sessions by Wait State
          </span>
          <span style={{ fontSize: 11.5, color: "var(--text-muted)" }}>
            What's actually running right now is waiting on, for fast triage.
          </span>
        </div>
        {data && (
          <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
            {data.total_active}
          </span>
        )}
      </div>

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {data && data.total_active === 0 && (
        <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No active sessions right now — everything's idle.</span>
      )}

      {data && data.total_active > 0 && (
        <>
          <div style={{ display: "flex", width: "100%", height: 10, borderRadius: 100, overflow: "hidden" }}>
            {data.rows.map((r) => (
              <span
                key={r.category}
                title={`${r.category}: ${r.count}`}
                style={{
                  width: `${(100 * r.count) / data.total_active}%`,
                  background: CATEGORY_COLORS[r.category] ?? FALLBACK_COLOR,
                }}
              />
            ))}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {data.rows.map((r) => (
              <div key={r.category} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: CATEGORY_COLORS[r.category] ?? FALLBACK_COLOR,
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontSize: 11.5, color: "var(--text-secondary)" }}>{r.category}</span>
                <span style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 11.5, color: "var(--text-muted)" }}>
                  {r.count}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
