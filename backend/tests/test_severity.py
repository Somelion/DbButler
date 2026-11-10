"""Thresholds from docs/FUNCTIONAL_SPEC.md, checked at their boundaries."""

from datetime import datetime, timedelta, timezone

import pytest

from app import severity as s


def test_worse_picks_the_higher_rank():
    assert s.worse("healthy", "critical") == "critical"
    assert s.worse("attention", "unknown") == "attention"


@pytest.mark.parametrize(
    ("func", "value", "expected"),
    [
        (s.dead_pct_severity, 9.99, "healthy"),
        (s.dead_pct_severity, 10, "attention"),
        (s.dead_pct_severity, 50, "critical"),
        (s.wraparound_severity, 999_999_999, "healthy"),
        (s.wraparound_severity, 1_000_000_000, "attention"),
        (s.wraparound_severity, 1_500_000_000, "critical"),
        (s.cache_hit_severity, None, "unknown"),
        (s.cache_hit_severity, 95, "healthy"),
        (s.cache_hit_severity, 85, "attention"),
        (s.cache_hit_severity, 84.9, "critical"),
        (s.idle_in_tx_severity, None, "healthy"),
        (s.idle_in_tx_severity, 60, "attention"),
        (s.idle_in_tx_severity, 300, "critical"),
        (s.long_running_query_severity, 299, "healthy"),
        (s.long_running_query_severity, 300, "attention"),
        (s.long_running_query_severity, 1800, "critical"),
        (s.connection_pool_severity, 79.9, "healthy"),
        (s.connection_pool_severity, 80, "attention"),
        (s.connection_pool_severity, 95, "critical"),
        (s.replication_lag_severity, 60, "attention"),
        (s.replication_lag_severity, 300, "critical"),
        (s.autovacuum_staleness_severity, None, "critical"),
        (s.autovacuum_staleness_severity, 6.9, "healthy"),
        (s.autovacuum_staleness_severity, 7, "attention"),
        (s.autovacuum_staleness_severity, 30, "critical"),
    ],
)
def test_single_value_thresholds(func, value, expected):
    assert func(value) == expected


def test_wal_archiving_compares_last_failure_with_last_success():
    now = datetime.now(timezone.utc)
    hour = timedelta(hours=1)
    assert s.wal_archiving_severity(0, None, None) == "healthy"
    assert s.wal_archiving_severity(3, now, now - hour) == "attention"  # recovered since
    assert s.wal_archiving_severity(3, now - hour, now) == "critical"  # still failing
    assert s.wal_archiving_severity(3, None, now) == "critical"  # never succeeded


def test_wal_dir_size_ratio():
    gib = 1024**3
    assert s.wal_dir_size_severity(gib, 0) == "unknown"
    assert s.wal_dir_size_severity(gib, gib) == "healthy"
    assert s.wal_dir_size_severity(2 * gib, gib) == "attention"
    assert s.wal_dir_size_severity(5 * gib, gib) == "critical"


def test_pooler_waiting_clients():
    assert s.pooler_severity(0, 100) == "healthy"
    assert s.pooler_severity(1, 1) == "attention"
    assert s.pooler_severity(1, 5) == "critical"


def test_replication_slot_states():
    assert s.replication_slot_severity(True, "lost", None) == "healthy"
    assert s.replication_slot_severity(False, "unreserved", 0) == "attention"
    assert s.replication_slot_severity(False, "reserved", 100 * 1024 * 1024) == "attention"
    assert s.replication_slot_severity(False, "reserved", 1024) == "healthy"
