import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import TrendChart from "./TrendChart";
import MultiTrendChart, { MULTI_TREND_PALETTE } from "./MultiTrendChart";
import QueryPicker from "./QueryPicker";

const RANGE_OPTIONS = [
  { hours: 24, label: "24 hours" },
  { hours: 24 * 7, label: "7 days" },
  { hours: 24 * 30, label: "30 days" },
];

const COMPARE_METRIC_OPTIONS = [
  { value: "mean_exec_ms", label: "Avg Latency" },
  { value: "calls", label: "Calls" },
];

// How many standard deviations above a query's own mean latency (across the
// history currently shown) counts as "ran hot" — shaded as the top of the
// normal-range band on the deep-dive latency chart.
const ANOMALY_STDDEV_MULTIPLIER = 1.5;

function meanAndStddev(values) {
  if (values.length === 0) return { mean: 0, stddev: 0 };
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length;
  return { mean, stddev: Math.sqrt(variance) };
}

export default function QueryHistoryScreen({ targetId }) {
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [selectedQueryIds, setSelectedQueryIds] = useState(new Set());
  const [focusedQueryId, setFocusedQueryId] = useState(null);
  const [compareMetric, setCompareMetric] = useState("mean_exec_ms");
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState(null);

  // queryids are target-specific pg_stat_statements ids — carrying a
  // selection/focus over from a different target would silently request
  // ids that don't exist here, so clear both (and the stale summary/
  // history payloads) on every target switch.
  useEffect(() => {
    setSelectedQueryIds(new Set());
    setFocusedQueryId(null);
    setSummary(null);
    setHistory(null);
  }, [targetId]);

  useEffect(() => {
    api
      .getQueryHistorySummary(targetId)
      .then((d) => {
        setSummary(d);
        setSummaryError(null);
      })
      .catch((err) => setSummaryError(err.message));
  }, [targetId]);

  const requestedIds = useMemo(() => {
    const ids = new Set(selectedQueryIds);
    if (focusedQueryId != null) ids.add(focusedQueryId);
    return [...ids];
  }, [selectedQueryIds, focusedQueryId]);

  useEffect(() => {
    if (requestedIds.length === 0) {
      setHistory(null);
      return;
    }
    api
      .getQueryHistory(targetId, hours, requestedIds)
      .then((d) => {
        setHistory(d);
        setHistoryError(null);
      })
      .catch((err) => setHistoryError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, hours, requestedIds.join(",")]);

  const toggleQuery = (queryid) => {
    setSelectedQueryIds((prev) => {
      const next = new Set(prev);
      if (next.has(queryid)) {
        next.delete(queryid);
      } else if (next.size < MULTI_TREND_PALETTE.length) {
        next.add(queryid);
      }
      return next;
    });
  };
  const clearSelection = () => setSelectedQueryIds(new Set());

  const seriesByQueryId = useMemo(() => {
    const map = new Map();
    if (history) for (const q of history.queries) map.set(q.queryid, q);
    return map;
  }, [history]);

  // Preserve selection order (a Set iterates in insertion order) so a
  // query's overlay color stays stable as long as it stays selected —
  // same technique TrendsScreen's Table Trends section uses.
  const compareSeries = [...selectedQueryIds]
    .map((id) => seriesByQueryId.get(id))
    .filter(Boolean)
    .map((q) => ({ name: q.query, points: q.points.map((p) => ({ ts: p.ts, value: p[compareMetric] })) }));

  const focusedSeries = focusedQueryId != null ? seriesByQueryId.get(focusedQueryId) : null;
  const focusedCallsPoints = focusedSeries ? focusedSeries.points.map((p) => ({ ts: p.ts, value: p.calls })) : [];
  const focusedLatencyPoints = focusedSeries
    ? focusedSeries.points.map((p) => ({ ts: p.ts, value: p.mean_exec_ms }))
    : [];
  const { mean: latencyMean, stddev: latencyStddev } = meanAndStddev(focusedLatencyPoints.map((p) => p.value));
  const anomalyThreshold = latencyMean + ANOMALY_STDDEV_MULTIPLIER * latencyStddev;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 18,
          padding: "18px 20px",
          background: "var(--card)",
          border: "1px solid var(--border)",
          borderRadius: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Query History</span>
          <div style={{ display: "flex", gap: 6 }}>
            {RANGE_OPTIONS.map((opt) => (
              <span
                key={opt.hours}
                onClick={() => setHours(opt.hours)}
                style={{
                  padding: "6px 12px",
                  fontSize: 12,
                  fontWeight: hours === opt.hours ? 650 : 600,
                  color: hours === opt.hours ? "white" : "var(--text-secondary)",
                  background: hours === opt.hours ? "var(--accent)" : "var(--row-bg)",
                  borderRadius: 100,
                  cursor: "pointer",
                }}
              >
                {opt.label}
              </span>
            ))}
          </div>
        </div>

        {summaryError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{summaryError}</span>}
        {historyError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{historyError}</span>}

        {summary && summary.queries.length === 0 && (
          <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
            No query history yet — this fills in once the query history collector has run a few
            cycles (every 5 minutes) against queries with recent activity in pg_stat_statements.
          </span>
        )}

        {summary && summary.queries.length > 0 && (
          <QueryPicker
            queries={summary.queries}
            selected={selectedQueryIds}
            onToggle={toggleQuery}
            onClear={clearSelection}
            maxSelected={MULTI_TREND_PALETTE.length}
            focusedQueryId={focusedQueryId}
            onFocus={(id) => setFocusedQueryId((prev) => (prev === id ? null : id))}
          />
        )}
      </div>

      {selectedQueryIds.size > 0 && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 12,
            padding: "18px 20px",
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 14,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Compare Queries</span>
            <div style={{ display: "flex", alignItems: "center", padding: 3, background: "var(--row-bg)", borderRadius: 9 }}>
              {COMPARE_METRIC_OPTIONS.map((opt) => (
                <span
                  key={opt.value}
                  onClick={() => setCompareMetric(opt.value)}
                  style={{
                    padding: "6px 14px",
                    fontSize: 12,
                    fontWeight: compareMetric === opt.value ? 650 : 600,
                    color: compareMetric === opt.value ? "white" : "var(--text-secondary)",
                    background: compareMetric === opt.value ? "var(--accent)" : "transparent",
                    borderRadius: 7,
                    cursor: "pointer",
                  }}
                >
                  {opt.label}
                </span>
              ))}
            </div>
          </div>
          <MultiTrendChart unit={compareMetric === "mean_exec_ms" ? "ms" : ""} series={compareSeries} />
        </div>
      )}

      {focusedQueryId != null && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 14,
            padding: "18px 20px",
            background: "var(--card)",
            border: "1px solid var(--border)",
            borderRadius: 14,
          }}
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Query Deep-Dive</span>
          <span
            title={focusedSeries?.query}
            style={{
              fontFamily: "IBM Plex Mono, monospace",
              fontSize: 12,
              color: "var(--text-muted)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {focusedSeries?.query ?? "Loading…"}
          </span>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 }}>
            <TrendChart label="Calls per interval" unit="" points={focusedCallsPoints} />
            <TrendChart
              label="Average latency per interval"
              unit="ms"
              points={focusedLatencyPoints}
              thresholdMin={0}
              thresholdMax={anomalyThreshold}
            />
          </div>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
            The shaded band on the latency chart is this query's own normal range (average ±{" "}
            {ANOMALY_STDDEV_MULTIPLIER}× standard deviation, computed from the history shown) — a
            point above it means that 5-minute collection interval ran unusually slow on average,
            not necessarily any single execution within it.
          </span>
        </div>
      )}
    </div>
  );
}
