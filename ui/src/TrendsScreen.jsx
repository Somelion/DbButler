import { useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import styles from "./TrendsScreen.module.css";
import { api } from "./api";
import { targetKey } from "./queryClient";
import { Alert, Card, EmptyState, SegmentedControl, Skeleton } from "./components";
import TrendChart from "./TrendChart";
import MultiTrendChart, { MULTI_TREND_PALETTE } from "./MultiTrendChart";
import TablePicker from "./TablePicker";
import { formatMetricValue } from "./format";

// `labelKey`s are translation keys (trends namespace), resolved at render time.
const RANGE_OPTIONS = [
  { value: 24, labelKey: "range.h24" },
  { value: 24 * 7, labelKey: "range.d7" },
  { value: 24 * 30, labelKey: "range.d30" },
];
const DEFAULT_HOURS = 24;

// Matches the severity thresholds used elsewhere (severity.py) — only shown
// where a "healthy zone" genuinely applies; connection count and database
// size don't get one.
const THRESHOLDS = {
  cache_hit_ratio: [95, 100],
  worst_table_dead_pct: [0, 10],
};

const TABLE_METRIC_OPTIONS = [
  { value: "table_dead_pct", labelKey: "tableMetric.deadPct" },
  { value: "table_size_bytes", labelKey: "tableMetric.size" },
];

const translateOptions = (options, t) => options.map(({ value, labelKey }) => ({ value, label: t(labelKey) }));
const DEFAULT_TABLE_METRIC = "table_dead_pct";

const DEFAULT_TABLE_SELECTION = 5;

// Renders app/forecasting.py's linear-trend projection (routers/metrics.py's
// TrendForecast, only ever set for database size and wraparound age) as a
// one-line caption below its chart — days-to-threshold when there's a real
// ceiling to head toward (wraparound), a plain 30-day projection otherwise
// (size). Null when a series has no forecast, so TrendChart's default
// (no footer) applies unchanged.
function forecastFooter(series, t) {
  const forecast = series.forecast;
  if (!forecast) return null;

  const text =
    forecast.days_to_threshold != null
      ? t("forecast.daysUntil", {
          count: Math.round(forecast.days_to_threshold),
          threshold: forecast.threshold_label,
        })
      : t("forecast.in30Days", { value: formatMetricValue(forecast.projected_value_30d, series.unit) });

  return <span className={styles.forecast}>{text}</span>;
}

// View state lives in the URL so a Trends view can be reloaded or shared:
//   ?hours=168            time range (one of RANGE_OPTIONS, else 24)
//   ?metric=table_size_bytes  table metric (one of TABLE_METRIC_OPTIONS)
//   ?tables=a.b,c.d       the tables being compared. Absent = the default
//                         (worst DEFAULT_TABLE_SELECTION for the metric);
//                         present but empty = the user cleared the list.
function readView(searchParams) {
  const hours = Number(searchParams.get("hours"));
  const metric = searchParams.get("metric");
  const tables = searchParams.get("tables");
  return {
    hours: RANGE_OPTIONS.some((o) => o.value === hours) ? hours : DEFAULT_HOURS,
    tableMetric: TABLE_METRIC_OPTIONS.some((o) => o.value === metric) ? metric : DEFAULT_TABLE_METRIC,
    pickedTables:
      tables == null
        ? null
        : [...new Set(tables.split(",").filter(Boolean))].slice(0, MULTI_TREND_PALETTE.length),
  };
}

const tableKey = (t) => `${t.schema_name}.${t.table_name}`;

export default function TrendsScreen({ targetId }) {
  const { t } = useTranslation("trends");
  const [searchParams, setSearchParams] = useSearchParams();
  const { hours, tableMetric, pickedTables } = readView(searchParams);

  const updateParams = (changes) =>
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [name, value] of Object.entries(changes)) {
          if (value == null) next.delete(name);
          else next.set(name, String(value));
        }
        return next;
      },
      { replace: true }
    );

  const trendsQuery = useQuery({
    queryKey: targetKey(targetId, "trends", hours),
    queryFn: ({ signal }) => api.getTrends(targetId, hours, { signal }),
    placeholderData: keepPreviousData,
  });

  const waitEventsQuery = useQuery({
    queryKey: targetKey(targetId, "wait-event-trend", hours),
    queryFn: ({ signal }) => api.getWaitEventTrend(targetId, hours, { signal }),
    placeholderData: keepPreviousData,
  });

  // Cheap: latest value per table only, regardless of table count or history
  // depth. Drives the search/pick list. Doesn't depend on `hours` — "latest"
  // means the same thing no matter which range is showing.
  const summaryQuery = useQuery({
    queryKey: targetKey(targetId, "table-trends-summary", tableMetric),
    queryFn: ({ signal }) => api.getTableTrendsSummary(targetId, tableMetric, { signal }),
    placeholderData: keepPreviousData,
  });
  const summary = summaryQuery.data;
  // While a new metric's summary is loading, the previous one is still on
  // screen (keepPreviousData) — but its tables belong to the old metric, so
  // nothing downstream should act on them yet.
  const summaryIsCurrent = Boolean(summary) && !summaryQuery.isPlaceholderData;

  // The user's picks (from the URL) survive a range change; a metric change
  // drops them (see changeMetric), falling back to the default — the summary
  // is already sorted worst-first server-side. A target switch remounts this
  // screen on a fresh URL, so a selection never carries across targets.
  // Picks that the current summary doesn't know are dropped.
  const knownTables = summaryIsCurrent ? new Set(summary.tables.map(tableKey)) : null;
  const selectedList = !summaryIsCurrent
    ? []
    : pickedTables == null
      ? summary.tables.slice(0, DEFAULT_TABLE_SELECTION).map(tableKey)
      : pickedTables.filter((key) => knownTables.has(key));
  // Preserve selection order (a Set iterates in insertion order) so a
  // table's color stays stable as long as it stays selected.
  const selectedTables = new Set(selectedList);

  // Full history — only for whichever tables are actually selected, so the
  // payload stays bounded no matter how many tables the target has.
  const tableTrendsQuery = useQuery({
    queryKey: targetKey(targetId, "table-trends", tableMetric, hours, selectedList),
    queryFn: ({ signal }) => api.getTableTrends(targetId, tableMetric, hours, selectedList, { signal }),
    enabled: selectedList.length > 0,
    placeholderData: keepPreviousData,
  });
  const tableTrends = tableTrendsQuery.data;

  const setTables = (list) => updateParams({ tables: list.join(",") });

  const toggleTable = (key) => {
    if (selectedTables.has(key)) {
      setTables(selectedList.filter((k) => k !== key));
    } else if (selectedList.length < MULTI_TREND_PALETTE.length) {
      setTables([...selectedList, key]);
    }
  };

  const clearSelection = () => setTables([]);

  const changeMetric = (metric) => updateParams({ metric, tables: null });

  const chartSeries = tableTrends
    ? selectedList
        .map((key) => tableTrends.tables.find((table) => tableKey(table) === key))
        .filter(Boolean)
        .map((table) => ({ name: tableKey(table), points: table.points }))
    : [];

  return (
    <div className={styles.screen}>
      <div className={styles.toolbar}>
        <SegmentedControl
          label={t("range.label")}
          options={translateOptions(RANGE_OPTIONS, t)}
          value={hours}
          onChange={(value) => updateParams({ hours: value === DEFAULT_HOURS ? null : value })}
        />
      </div>

      <Card title={t("database.title")}>
        {trendsQuery.error && <Alert tone="critical">{trendsQuery.error.message}</Alert>}
        {trendsQuery.isPending && <Skeleton lines={4} label={t("database.loading")} />}
        {trendsQuery.data && (
          <div className={styles.chartGrid}>
            {trendsQuery.data.series.map((s) => {
              const [thresholdMin, thresholdMax] = THRESHOLDS[s.metric_name] ?? [null, null];
              return (
                <TrendChart
                  key={s.metric_name}
                  label={s.label}
                  unit={s.unit}
                  points={s.points}
                  thresholdMin={thresholdMin}
                  thresholdMax={thresholdMax}
                  footer={forecastFooter(s, t)}
                />
              );
            })}
          </div>
        )}
      </Card>

      <Card
        title={t("waitEvents.title")}
        description={t("waitEvents.description")}
      >
        {waitEventsQuery.error && <Alert tone="critical">{waitEventsQuery.error.message}</Alert>}
        {waitEventsQuery.isPending && <Skeleton height={220} label={t("waitEvents.loading")} />}
        {waitEventsQuery.data && waitEventsQuery.data.series.length === 0 && (
          <EmptyState title={t("waitEvents.empty")} />
        )}
        {waitEventsQuery.data && waitEventsQuery.data.series.length > 0 && (
          <MultiTrendChart
            unit=""
            series={waitEventsQuery.data.series.map((s) => ({ name: s.category, points: s.points }))}
          />
        )}
      </Card>

      <Card
        title={t("tables.title")}
        actions={
          <SegmentedControl
            label={t("tableMetric.label")}
            size="sm"
            options={translateOptions(TABLE_METRIC_OPTIONS, t)}
            value={tableMetric}
            onChange={changeMetric}
          />
        }
      >
        {summaryQuery.error && <Alert tone="critical">{summaryQuery.error.message}</Alert>}
        {summaryQuery.isPending && <Skeleton lines={5} label={t("tables.loading")} />}

        {summary && summary.tables.length === 0 && <EmptyState title={t("tables.empty")} />}

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

            {tableTrendsQuery.error && <Alert tone="critical">{tableTrendsQuery.error.message}</Alert>}

            {summaryIsCurrent && selectedList.length === 0 && (
              <p className={styles.note}>{t("tables.selectHint")}</p>
            )}

            {selectedList.length > 0 && tableTrendsQuery.isPending && (
              <Skeleton height={220} label={t("tables.loadingTrends")} />
            )}

            {selectedList.length > 0 && tableTrends && (
              <MultiTrendChart unit={tableTrends.unit} series={chartSeries} />
            )}
          </>
        )}
      </Card>
    </div>
  );
}
