import { useMemo } from "react";
import { useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { History } from "lucide-react";
import { api } from "./api";
import { targetKey } from "./queryClient";
import TrendChart from "./TrendChart";
import MultiTrendChart, { MULTI_TREND_PALETTE } from "./MultiTrendChart";
import QueryPicker from "./QueryPicker";
import { Alert, Card, EmptyState, SegmentedControl, Skeleton } from "./components";
import styles from "./QueryHistoryScreen.module.css";

// Labels are translation keys, translated at render.
const RANGE_OPTIONS = [
  { value: 24, label: "history.range.24h" },
  { value: 24 * 7, label: "history.range.7d" },
  { value: 24 * 30, label: "history.range.30d" },
];
const DEFAULT_HOURS = 24;

const COMPARE_METRIC_OPTIONS = [
  { value: "mean_exec_ms", label: "history.metric.meanExecMs" },
  { value: "calls", label: "history.metric.calls" },
  { value: "rows_per_call", label: "history.metric.rowsPerCall" },
];
const DEFAULT_METRIC = "mean_exec_ms";

const MAX_SELECTED = MULTI_TREND_PALETTE.length;

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

// queryids are pg_stat_statements bigints. The API sends them as JSON
// numbers, so a URL token is parsed back with Number() — the same rounding
// JSON.parse applied — and only all-digit tokens are accepted.
function parseQueryId(token) {
  return /^-?\d+$/.test(token) ? Number(token) : null;
}

function parseQueryIdList(param) {
  const ids = [];
  for (const token of (param ?? "").split(",")) {
    const id = parseQueryId(token.trim());
    if (id != null && !ids.includes(id)) ids.push(id);
  }
  return ids.slice(0, MAX_SELECTED);
}

// View state lives in the URL so a comparison can be shared or reloaded:
//   ?hours=168        time range (24 / 168 / 720; 24 is the default)
//   ?q=<id>,<id>      queries overlaid in Compare, in selection order
//   ?focus=<id>       the query in Deep-Dive
//   ?metric=calls     Compare metric (mean_exec_ms is the default)
// Unknown values fall back to the default. The screen is remounted per
// target and switching target drops the query string, so a selection never
// carries over to a database where those queryids mean nothing; ids that
// aren't in this target's tracked list are dropped once it has loaded.
export default function QueryHistoryScreen({ targetId }) {
  const { t } = useTranslation("queries");
  const [searchParams, setSearchParams] = useSearchParams();
  const rangeOptions = RANGE_OPTIONS.map((o) => ({ ...o, label: t(o.label) }));
  const compareMetricOptions = COMPARE_METRIC_OPTIONS.map((o) => ({ ...o, label: t(o.label) }));

  const hoursParam = Number(searchParams.get("hours"));
  const hours = RANGE_OPTIONS.some((o) => o.value === hoursParam) ? hoursParam : DEFAULT_HOURS;
  const metricParam = searchParams.get("metric");
  const compareMetric = COMPARE_METRIC_OPTIONS.some((o) => o.value === metricParam) ? metricParam : DEFAULT_METRIC;

  const summaryQuery = useQuery({
    queryKey: targetKey(targetId, "query-history-summary"),
    queryFn: ({ signal }) => api.getQueryHistorySummary(targetId, { signal }),
  });
  const summary = summaryQuery.data;

  const summaryById = useMemo(() => {
    const map = new Map();
    if (summary) for (const q of summary.queries) map.set(q.queryid, q);
    return map;
  }, [summary]);
  const isKnown = (id) => !summary || summaryById.has(id);

  const qParam = searchParams.get("q");
  const selectedIds = useMemo(
    () => parseQueryIdList(qParam).filter((id) => !summary || summaryById.has(id)),
    [qParam, summary, summaryById]
  );
  const selectedQueryIds = useMemo(() => new Set(selectedIds), [selectedIds]);
  const focusParam = parseQueryId(searchParams.get("focus") ?? "");
  const focusedQueryId = focusParam != null && isKnown(focusParam) ? focusParam : null;

  const updateParams = (changes) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(changes)) {
          if (value == null || value === "") next.delete(key);
          else next.set(key, String(value));
        }
        return next;
      },
      { replace: true }
    );

  const requestedIds = useMemo(() => {
    const ids = new Set(selectedIds);
    if (focusedQueryId != null) ids.add(focusedQueryId);
    return [...ids].sort((a, b) => a - b);
  }, [selectedIds, focusedQueryId]);

  const historyQuery = useQuery({
    queryKey: targetKey(targetId, "query-history", hours, requestedIds.join(",")),
    queryFn: ({ signal }) => api.getQueryHistory(targetId, hours, requestedIds, { signal }),
    enabled: requestedIds.length > 0,
    // Keep the charts on the previous range/selection while the next loads.
    placeholderData: keepPreviousData,
  });
  const history = requestedIds.length > 0 ? historyQuery.data : null;
  const historyLoading = requestedIds.length > 0 && historyQuery.isPending;

  const toggleQuery = (queryid) => {
    let next;
    if (selectedQueryIds.has(queryid)) next = selectedIds.filter((id) => id !== queryid);
    else if (selectedIds.length < MAX_SELECTED) next = [...selectedIds, queryid];
    else return;
    updateParams({ q: next.map(String).join(",") });
  };
  const clearSelection = () => updateParams({ q: null });
  const toggleFocus = (queryid) => updateParams({ focus: focusedQueryId === queryid ? null : queryid });

  const seriesByQueryId = useMemo(() => {
    const map = new Map();
    if (history) for (const q of history.queries) map.set(q.queryid, q);
    return map;
  }, [history]);

  // Preserve selection order (the ?q= list is kept in the order queries
  // were picked) so a query's overlay color stays stable as long as it
  // stays selected — same technique TrendsScreen's Table Trends section
  // uses. Intervals recorded before rows were stored per interval have
  // rows_per_call null — left out rather than drawn as zero.
  const compareSeries = selectedIds
    .map((id) => seriesByQueryId.get(id))
    .filter(Boolean)
    .map((q) => ({
      name: q.query,
      points: q.points.filter((p) => p[compareMetric] != null).map((p) => ({ ts: p.ts, value: p[compareMetric] })),
    }));

  const focusedSeries = focusedQueryId != null ? seriesByQueryId.get(focusedQueryId) : null;
  const focusedText = focusedSeries?.query ?? summaryById.get(focusedQueryId)?.query ?? t("common:actions.loading");
  const focusedCallsPoints = focusedSeries ? focusedSeries.points.map((p) => ({ ts: p.ts, value: p.calls })) : [];
  const focusedLatencyPoints = focusedSeries
    ? focusedSeries.points.map((p) => ({ ts: p.ts, value: p.mean_exec_ms }))
    : [];
  const { mean: latencyMean, stddev: latencyStddev } = meanAndStddev(focusedLatencyPoints.map((p) => p.value));
  const anomalyThreshold = latencyMean + ANOMALY_STDDEV_MULTIPLIER * latencyStddev;

  return (
    <div className={styles.screen}>
      <Card
        title={t("history.trackedTitle")}
        actions={
          <SegmentedControl
            label={t("history.timeRange")}
            options={rangeOptions}
            value={hours}
            onChange={(value) => updateParams({ hours: value === DEFAULT_HOURS ? null : value })}
          />
        }
      >
        {summaryQuery.error && <Alert tone="critical">{summaryQuery.error.message}</Alert>}
        {historyQuery.error && <Alert tone="critical">{historyQuery.error.message}</Alert>}

        {summaryQuery.isPending && <Skeleton lines={5} label={t("history.loadingTracked")} />}

        {summary && summary.queries.length === 0 && (
          <EmptyState icon={History} title={t("history.emptyTitle")}>
            {t("history.emptyBody")}
          </EmptyState>
        )}

        {summary && summary.queries.length > 0 && (
          <QueryPicker
            queries={summary.queries}
            selected={selectedQueryIds}
            onToggle={toggleQuery}
            onClear={clearSelection}
            maxSelected={MAX_SELECTED}
            focusedQueryId={focusedQueryId}
            onFocus={toggleFocus}
          />
        )}
      </Card>

      {selectedIds.length > 0 && (
        <Card
          title={t("history.compareTitle")}
          actions={
            <SegmentedControl
              label={t("history.compareMetric")}
              options={compareMetricOptions}
              value={compareMetric}
              onChange={(value) => updateParams({ metric: value === DEFAULT_METRIC ? null : value })}
            />
          }
        >
          {historyLoading ? (
            <Skeleton height={180} label={t("history.loadingHistory")} />
          ) : (
            <MultiTrendChart unit={compareMetric === "mean_exec_ms" ? "ms" : ""} series={compareSeries} />
          )}
        </Card>
      )}

      {focusedQueryId != null && (
        <Card title={t("history.deepDiveTitle")}>
          <p title={focusedText} className={styles.focusedQuery}>
            {focusedText}
          </p>
          {historyLoading ? (
            <Skeleton height={180} label={t("history.loadingHistory")} />
          ) : (
            <div className={styles.charts}>
              <TrendChart label={t("history.callsChart")} unit="" points={focusedCallsPoints} />
              <TrendChart
                label={t("history.latencyChart")}
                unit="ms"
                points={focusedLatencyPoints}
                thresholdMin={0}
                thresholdMax={anomalyThreshold}
              />
            </div>
          )}
          <p className={styles.footnote}>{t("history.footnote", { multiplier: ANOMALY_STDDEV_MULTIPLIER })}</p>
        </Card>
      )}
    </div>
  );
}
