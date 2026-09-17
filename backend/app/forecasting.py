"""Shared linear-trend forecasting over metric-history-shaped samples —
(collected_at, value) pairs, oldest first. Used by Table Health's dead-tuple
growth forecast (app/table_health_advisor.py) and Trends' capacity
forecasting — database size and XID wraparound age
(routers/metrics.py). Deliberately simple: one ordinary-least-squares fit
through elapsed-days-since-first-sample vs value, no seasonality or outlier
rejection — the goal is "is this obviously trending toward a threshold
soon," not a precise ETA.
"""

DEFAULT_MIN_POINTS = 12
DEFAULT_MIN_SPAN_HOURS = 6


def fit_linear_trend(history, min_points=DEFAULT_MIN_POINTS, min_span_hours=DEFAULT_MIN_SPAN_HOURS):
    """history: list of (collected_at: datetime, value: float), oldest
    first. Returns (slope_per_day, span_days), or None if there isn't
    enough history to trust a trend — too few points, or too short a span,
    since a handful of samples a few minutes apart can't tell a real trend
    from noise."""
    if len(history) < min_points:
        return None

    t0 = history[0][0]
    span_days = (history[-1][0] - t0).total_seconds() / 86400.0
    if span_days < min_span_hours / 24:
        return None

    xs = [(ts - t0).total_seconds() / 86400.0 for ts, _ in history]
    ys = [value for _, value in history]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return slope, span_days


def days_to_reach(current_value: float, target_value: float, slope_per_day: float) -> float | None:
    """Days until current_value + slope*days == target_value, assuming the
    trend holds. None if the trend isn't heading toward the target at all
    (flat, or moving the other way) or the target's already behind us —
    "already crossed" is what the caller's own current-value check should
    report, not a negative or infinite day count from here."""
    if slope_per_day == 0:
        return None
    days = (target_value - current_value) / slope_per_day
    if days <= 0:
        return None
    return days
