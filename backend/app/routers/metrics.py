import uuid

from fastapi import APIRouter, HTTPException, Query

from app.db.store import store_conn
from app.forecasting import days_to_reach, fit_linear_trend
from app.schemas import (
    TableTrendSeries,
    TableTrendsResponse,
    TableTrendSummaryResponse,
    TableTrendSummaryRow,
    TrendForecast,
    TrendPoint,
    TrendSeries,
    TrendsResponse,
)
from app.severity import WRAPAROUND_CRITICAL

router = APIRouter(prefix="/api/targets", tags=["metrics"])

# (metric_name, display label, unit) for every series Trends charts. Adding a
# metric here is the only step needed once its collector exists — see
# app/collectors/__init__.py.
#
# worst_table_xid_age keeps its historical metric name so older history
# still lines up, but is now the database's datfrozenxid age (which also
# covers catalogs and TOAST tables) — see app/collectors/__init__.py.
TREND_METRICS = [
    ("cache_hit_ratio", "Cache Hit Rate", "%"),
    ("connection_count", "Connections", ""),
    ("worst_table_dead_pct", "Worst Table Bloat", "%"),
    ("database_size_bytes", "Database Size", "bytes"),
    ("worst_table_xid_age", "Wraparound Age (transaction IDs)", ""),
    ("database_mxid_age", "Wraparound Age (multixact IDs)", ""),
]

# Both wraparound series forecast days until the same critical threshold —
# XIDs and multixact IDs are both 32-bit counters (app/severity.py).
_WRAPAROUND_METRICS = ("worst_table_xid_age", "database_mxid_age")

# Capacity forecasting (app/forecasting.py) only makes sense for metrics that
# genuinely head toward a ceiling — cache hit rate and connection count
# don't have a "growing forever" failure mode the way size and XID age do.
# Requires a longer span than Table Health's dead-tuple forecast
# (routers/table_health.py) before trusting a trend: a 30-day-out size
# projection based on only a few hours of data is a much bigger
# extrapolation than a 14-day dead-tuple one.
FORECAST_MIN_SPAN_HOURS = 24
SIZE_PROJECTION_DAYS = 30


def _build_forecast(metric_name: str, points: list[TrendPoint]) -> TrendForecast | None:
    if metric_name != "database_size_bytes" and metric_name not in _WRAPAROUND_METRICS:
        return None

    history = [(p.ts, p.value) for p in points]
    trend = fit_linear_trend(history, min_span_hours=FORECAST_MIN_SPAN_HOURS)
    if trend is None:
        return None
    slope, span_days = trend
    if slope <= 0:
        return None

    current_value = points[-1].value

    if metric_name in _WRAPAROUND_METRICS:
        days = days_to_reach(current_value, WRAPAROUND_CRITICAL, slope)
        if days is None:
            return None
        return TrendForecast(
            slope_per_day=slope,
            span_days=span_days,
            days_to_threshold=days,
            threshold_label="the wraparound critical threshold",
        )

    return TrendForecast(
        slope_per_day=slope,
        span_days=span_days,
        projected_value_30d=current_value + slope * SIZE_PROJECTION_DAYS,
    )


# metric -> (display label, unit, table_stat_snapshots column) for the
# per-table trend picker. Written by app/scheduler.py's
# run_table_metrics_cycle, one row per table per cycle. The metric names are
# the API's (and the UI's) and predate the typed table; the column is a
# fixed value from this dict, never from the request.
TABLE_TREND_METRICS = {
    "table_dead_pct": ("Dead Tuple %", "%", "dead_pct"),
    "table_size_bytes": ("Size", "bytes", "total_bytes"),
}

# How far back the per-table summary looks for "the latest value". Bounded
# so the picker never scans the whole 30-day history (a table not seen in
# the last day has most likely been dropped).
TABLE_TREND_SUMMARY_LOOKBACK_HOURS = 24


@router.get("/{target_id}/metrics/{metric_name}")
def get_metric_points(target_id: uuid.UUID, metric_name: str, limit: int = 200):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT collected_at, value FROM metric_points
            WHERE target_id = %s AND metric_name = %s
            ORDER BY collected_at DESC
            LIMIT %s
            """,
            (target_id, metric_name, limit),
        )
        rows = cur.fetchall()
    points = [{"ts": ts, "value": value} for ts, value in reversed(rows)]
    return {"metric_name": metric_name, "points": points}


@router.get("/{target_id}/trends", response_model=TrendsResponse)
def get_trends(target_id: uuid.UUID, hours: int = 24):
    hours = max(1, min(hours, 24 * 90))  # matches the 30-day retention window with headroom
    series = []
    with store_conn() as conn, conn.cursor() as cur:
        for metric_name, label, unit in TREND_METRICS:
            cur.execute(
                """
                SELECT collected_at, value FROM metric_points
                WHERE target_id = %s AND metric_name = %s
                  AND collected_at >= now() - (%s * interval '1 hour')
                ORDER BY collected_at
                """,
                (target_id, metric_name, hours),
            )
            points = [TrendPoint(ts=ts, value=value) for ts, value in cur.fetchall()]
            forecast = _build_forecast(metric_name, points)
            series.append(TrendSeries(metric_name=metric_name, label=label, unit=unit, points=points, forecast=forecast))
    return TrendsResponse(hours=hours, series=series)


@router.get("/{target_id}/table-trends/summary", response_model=TableTrendSummaryResponse)
def get_table_trends_summary(target_id: uuid.UUID, metric: str = "table_dead_pct"):
    """The latest value per table only — cheap regardless of table count or
    history depth, meant for populating a search/pick list. Full history for
    whichever tables get selected comes from get_table_trends below."""
    if metric not in TABLE_TREND_METRICS:
        raise HTTPException(status_code=400, detail=f"Unknown table trend metric '{metric}'")
    label, unit, column = TABLE_TREND_METRICS[metric]

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT DISTINCT ON (schema_name, table_name) schema_name, table_name, {column}
            FROM table_stat_snapshots
            WHERE target_id = %s AND {column} IS NOT NULL
              AND collected_at >= now() - (%s * interval '1 hour')
            ORDER BY schema_name, table_name, collected_at DESC
            """,
            (target_id, TABLE_TREND_SUMMARY_LOOKBACK_HOURS),
        )
        rows = cur.fetchall()

    rows.sort(key=lambda row: row[2], reverse=True)
    tables = [TableTrendSummaryRow(schema_name=s, table_name=t, latest_value=v) for s, t, v in rows]
    return TableTrendSummaryResponse(metric=metric, label=label, unit=unit, tables=tables)


@router.get("/{target_id}/table-trends", response_model=TableTrendsResponse)
def get_table_trends(
    target_id: uuid.UUID,
    metric: str = "table_dead_pct",
    hours: int = 24,
    tables: list[str] = Query(default=[]),
):
    """Full history, but only for explicitly requested tables — with
    potentially hundreds of tables on a target, returning everyone's history
    just to render a picker doesn't scale. The frontend gets the picker list
    from get_table_trends_summary instead and asks for history only once a
    table is actually selected."""
    if metric not in TABLE_TREND_METRICS:
        raise HTTPException(status_code=400, detail=f"Unknown table trend metric '{metric}'")
    label, unit, column = TABLE_TREND_METRICS[metric]
    hours = max(1, min(hours, 24 * 90))

    if not tables:
        return TableTrendsResponse(metric=metric, label=label, unit=unit, hours=hours, tables=[])

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT schema_name, table_name, collected_at, {column}
            FROM table_stat_snapshots
            WHERE target_id = %s AND {column} IS NOT NULL
              AND collected_at >= now() - (%s * interval '1 hour')
              AND (schema_name || '.' || table_name) = ANY(%s)
            ORDER BY schema_name, table_name, collected_at
            """,
            (target_id, hours, tables),
        )
        rows = cur.fetchall()

    result: dict[str, TableTrendSeries] = {}
    for schema_name, table_name, ts, value in rows:
        key = f"{schema_name}.{table_name}"
        if key not in result:
            result[key] = TableTrendSeries(schema_name=schema_name, table_name=table_name, points=[])
        result[key].points.append(TrendPoint(ts=ts, value=value))

    return TableTrendsResponse(metric=metric, label=label, unit=unit, hours=hours, tables=list(result.values()))
