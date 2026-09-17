import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import TrendChart from "./TrendChart";
import MultiTrendChart, { MULTI_TREND_PALETTE } from "./MultiTrendChart";
import TablePicker from "./TablePicker";
import { formatMetricValue } from "./format";

const RANGE_OPTIONS = [
  { hours: 24, label: "24 hours" },
  { hours: 24 * 7, label: "7 days" },
  { hours: 24 * 30, label: "30 days" },
];

// Matches the severity thresholds used elsewhere (severity.py) — only shown
// where a "healthy zone" genuinely applies; connection count and database
// size don't get one.
const THRESHOLDS = {
  cache_hit_ratio: [95, 100],
  worst_table_dead_pct: [0, 10],
};

const TABLE_METRIC_OPTIONS = [
  { value: "table_dead_pct", label: "Dead Tuple %" },
  { value: "table_size_bytes", label: "Size" },
];

const DEFAULT_TABLE_SELECTION = 5;

// Renders app/forecasting.py's linear-trend projection (routers/metrics.py's
// TrendForecast, only ever set for database size and wraparound age) as a
// one-line caption below its chart — days-to-threshold when there's a real
// ceiling to head toward (wraparound), a plain 30-day projection otherwise
// (size). Null when a series has no forecast, so TrendChart's default
// (no footer) applies unchanged.
function forecastFooter(series) {
  const forecast = series.forecast;
  if (!forecast) return null;

  const text =
    forecast.days_to_threshold != null
      ? `At this rate, ~${Math.round(forecast.days_to_threshold)} day${
          Math.round(forecast.days_to_threshold) === 1 ? "" : "s"
        } until ${forecast.threshold_label}.`
      : `At this rate, ~${formatMetricValue(forecast.projected_value_30d, series.unit)} in 30 days.`;

  return (
    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
      {text}
    </span>
  );
}

export default function TrendsScreen({ targetId }) {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const [waitEvents, setWaitEvents] = useState(null);
  const [waitEventsError, setWaitEventsError] = useState(null);

  const [tableMetric, setTableMetric] = useState("table_dead_pct");
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [tableTrends, setTableTrends] = useState(null);
  const [tableTrendsError, setTableTrendsError] = useState(null);
  const [selectedTables, setSelectedTables] = useState(new Set());
  // Tracks (targetId, tableMetric) together — either one changing means the
  // previous selection may no longer even exist on the new target/metric,
  // not just "the user picked a different metric". Was keyed on tableMetric
  // alone, which left a stale selection (schema.table keys from the old
  // target) in place across a target switch, silently requesting
  // now-nonexistent tables from getTableTrends below.
  const prevKeyRef = useRef(`${targetId}:${tableMetric}`);

  useEffect(() => {
    setData(null);
    api
      .getTrends(targetId, hours)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [targetId, hours]);

  useEffect(() => {
    setWaitEvents(null);
    api
      .getWaitEventTrend(targetId, hours)
      .then((d) => {
        setWaitEvents(d);
        setWaitEventsError(null);
      })
      .catch((err) => setWaitEventsError(err.message));
  }, [targetId, hours]);

  // Cheap: latest value per table only, regardless of table count or history
  // depth. Drives the search/pick list. Doesn't depend on `hours` — "latest"
  // means the same thing no matter which range is showing.
  useEffect(() => {
    const key = `${targetId}:${tableMetric}`;
    const changed = prevKeyRef.current !== key;
    prevKeyRef.current = key;

    api
      .getTableTrendsSummary(targetId, tableMetric)
      .then((d) => {
        setSummary(d);
        setSummaryError(null);
        setSelectedTables((prev) => {
          // Keep the user's picks across a range change; only re-default
          // when the target or metric changed (different tables matter),
          // since the summary is already sorted worst-first server-side.
          if (!changed && prev.size > 0) return prev;
          return new Set(d.tables.slice(0, DEFAULT_TABLE_SELECTION).map((t) => `${t.schema_name}.${t.table_name}`));
        });
      })
      .catch((err) => setSummaryError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, tableMetric]);

  // Full history — only for whichever tables are actually selected, so the
  // payload stays bounded no matter how many tables the target has.
  useEffect(() => {
    if (selectedTables.size === 0) {
      setTableTrends(null);
      return;
    }
    api
      .getTableTrends(targetId, tableMetric, hours, [...selectedTables])
      .then((d) => {
        setTableTrends(d);
        setTableTrendsError(null);
      })
      .catch((err) => setTableTrendsError(err.message));
  }, [targetId, tableMetric, hours, selectedTables]);

  const toggleTable = (key) => {
    setSelectedTables((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else if (next.size < MULTI_TREND_PALETTE.length) {
        next.add(key);
      }
      return next;
    });
  };

  const clearSelection = () => setSelectedTables(new Set());

  // Preserve selection order (a Set iterates in insertion order) so a
  // table's color stays stable as long as it stays selected.
  const chartSeries = tableTrends
    ? [...selectedTables]
        .map((key) => tableTrends.tables.find((t) => `${t.schema_name}.${t.table_name}` === key))
        .filter(Boolean)
        .map((t) => ({ name: `${t.schema_name}.${t.table_name}`, points: t.points }))
    : [];

  return (
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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Trends</span>
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

      {error && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{error}</span>}

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 }}>
          {data.series.map((s) => {
            const [thresholdMin, thresholdMax] = THRESHOLDS[s.metric_name] ?? [null, null];
            return (
              <TrendChart
                key={s.metric_name}
                label={s.label}
                unit={s.unit}
                points={s.points}
                thresholdMin={thresholdMin}
                thresholdMax={thresholdMax}
                footer={forecastFooter(s)}
              />
            );
          })}
        </div>
      )}

      <div style={{ height: 1, background: "var(--border)" }} />

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Wait Events</span>
        <span style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: -8 }}>
          Active sessions grouped by what they were waiting on, sampled every few minutes — useful for spotting when a
          lock or I/O storm happened, after the fact.
        </span>

        {waitEventsError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{waitEventsError}</span>}

        {waitEvents && waitEvents.series.length === 0 && (
          <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No wait event history yet.</span>
        )}

        {waitEvents && waitEvents.series.length > 0 && (
          <MultiTrendChart
            unit=""
            series={waitEvents.series.map((s) => ({ name: s.category, points: s.points }))}
          />
        )}
      </div>

      <div style={{ height: 1, background: "var(--border)" }} />

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>Table Trends</span>
          <div style={{ display: "flex", alignItems: "center", padding: 3, background: "var(--row-bg)", borderRadius: 9 }}>
            {TABLE_METRIC_OPTIONS.map((opt) => (
              <span
                key={opt.value}
                onClick={() => setTableMetric(opt.value)}
                style={{
                  padding: "6px 14px",
                  fontSize: 12,
                  fontWeight: tableMetric === opt.value ? 650 : 600,
                  color: tableMetric === opt.value ? "white" : "var(--text-secondary)",
                  background: tableMetric === opt.value ? "var(--accent)" : "transparent",
                  borderRadius: 7,
                  cursor: "pointer",
                }}
              >
                {opt.label}
              </span>
            ))}
          </div>
        </div>

        {summaryError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{summaryError}</span>}

        {summary && summary.tables.length === 0 && (
          <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>No table history yet.</span>
        )}

        {summary && summary.tables.length > 0 && (
          <>
            <TablePicker
              tables={summary.tables}
              unit={summary.unit}
              selected={selectedTables}
              onToggle={toggleTable}
              onClear={clearSelection}
              maxSelected={MULTI_TREND_PALETTE.length}
            />

            {tableTrendsError && <span style={{ fontSize: 11.5, color: "var(--critical-text)" }}>{tableTrendsError}</span>}

            {selectedTables.size === 0 && (
              <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>
                Select at least one table above to see its trend.
              </span>
            )}

            {selectedTables.size > 0 && tableTrends && <MultiTrendChart unit={tableTrends.unit} series={chartSeries} />}
          </>
        )}
      </div>
    </div>
  );
}
