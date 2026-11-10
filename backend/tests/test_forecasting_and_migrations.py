from datetime import datetime, timedelta, timezone

from app.db import migrate
from app.forecasting import days_to_reach, fit_linear_trend
from app.pg_probe import _format_uptime
from app.schema_filter import schema_filter_params, schema_filter_sql

START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _hourly(values):
    return [(START + timedelta(hours=i), value) for i, value in enumerate(values)]


def test_linear_trend_recovers_the_slope_per_day():
    history = _hourly([i * 0.5 for i in range(24)])  # +0.5 per hour
    slope, span_days = fit_linear_trend(history)
    assert abs(slope - 12.0) < 1e-9
    assert abs(span_days - 23 / 24) < 1e-9


def test_trend_needs_enough_points_and_span():
    assert fit_linear_trend(_hourly(range(5))) is None
    minutes_apart = [(START + timedelta(minutes=i), i) for i in range(30)]
    assert fit_linear_trend(minutes_apart) is None


def test_days_to_reach():
    assert days_to_reach(10, 20, 2) == 5
    assert days_to_reach(10, 20, 0) is None
    assert days_to_reach(10, 20, -1) is None
    assert days_to_reach(30, 20, 1) is None


def test_migration_files_are_numbered_uniquely_and_after_the_baseline():
    files = migrate._migration_files()
    versions = [version for version, _path in files]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))
    assert all(version > migrate.BASELINE_VERSION for version in versions)
    assert all(path.suffix == ".sql" for _version, path in files)


def test_schema_filter_fragment_and_params_agree():
    assert schema_filter_sql("n.nspname", None) == ""
    assert schema_filter_params(None) == []
    assert schema_filter_sql("n.nspname", ["app"]) == "AND n.nspname = ANY(%s)"
    assert schema_filter_params(["app"]) == [["app"]]


def test_uptime_formatting():
    assert _format_uptime(59) == "0h 0m"
    assert _format_uptime(90061) == "1d 1h 1m"
