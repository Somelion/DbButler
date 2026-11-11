"""Data quality v2: Query History deltas, interval cache hit rate, target
backoff, xmin horizon and wraparound checks, retention coverage, and the
store migration staying additive."""

import re
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from app import scheduler
from app.cache_hit import MIN_INTERVAL_BLOCKS, BlockCounters, interval_hit_ratio
from app.db import migrate
from app.query_history_delta import Baseline, IntervalStats, QueryCounters, compute_interval
from app.severity import (
    WRAPAROUND_ATTENTION,
    WRAPAROUND_CRITICAL,
    XMIN_HORIZON_ATTENTION_SECONDS,
    XMIN_HORIZON_CRITICAL_AGE,
    xmin_horizon_severity,
)
from app.sql_ident import quote_literal
from app.target_backoff import BACKOFF_BASE_SECONDS, BACKOFF_MAX_SECONDS, TargetBackoff
from app.wraparound import build_wraparound_status
from app.xmin_horizon import MAX_XMIN_FINDINGS, build_xmin_horizon_status, format_duration

# --- Query History deltas ---------------------------------------------------


def test_first_sighting_records_no_interval():
    assert compute_interval(None, QueryCounters(10, 50.0, 100)) is None


def test_interval_is_the_difference_of_the_summed_counters():
    baseline = Baseline(calls=10, total_exec_ms=50.0, rows=100)
    current = QueryCounters(calls=14, total_exec_ms=70.0, rows=140)
    assert compute_interval(baseline, current) == IntervalStats(calls=4, mean_exec_ms=5.0, rows=40)


def test_baseline_from_an_older_version_is_rebaselined_not_diffed():
    # Recorded before last_rows existed, from one pg_stat_statements row
    # rather than the per-queryid sum: diffing it would record one bogus jump.
    assert compute_interval(Baseline(10, 50.0, None), QueryCounters(30, 150.0, 300)) is None


@pytest.mark.parametrize(
    "current",
    [
        QueryCounters(5, 70.0, 140),  # calls went backwards
        QueryCounters(14, 40.0, 140),  # time went backwards
        QueryCounters(14, 70.0, 90),  # rows went backwards
    ],
)
def test_a_reset_is_a_gap_not_a_negative_delta(current):
    assert compute_interval(Baseline(10, 50.0, 100), current) is None


def test_no_new_calls_records_nothing():
    assert compute_interval(Baseline(10, 50.0, 100), QueryCounters(10, 50.0, 100)) is None


def test_query_history_query_sums_rows_per_queryid():
    sql = " ".join(scheduler.QUERY_HISTORY_QUERY.split())
    assert "GROUP BY pss.queryid" in sql
    for column in ("calls", "total_exec_time", "rows"):
        assert f"sum(pss.{column})" in sql


# --- Interval cache hit rate ------------------------------------------------

RESET = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_cache_hit_first_sample_only_sets_the_baseline():
    current = BlockCounters(1000, 10, RESET)
    assert interval_hit_ratio(None, current) == (None, current)


def test_cache_hit_ratio_uses_the_interval_not_the_lifetime_totals():
    # Lifetime ratio here is ~99.9%; the last interval missed half its reads.
    previous = BlockCounters(1_000_000, 1_000, RESET)
    current = BlockCounters(1_000_500, 1_500, RESET)
    ratio, baseline = interval_hit_ratio(previous, current)
    assert ratio == pytest.approx(50.0)
    assert baseline == current


def test_cache_hit_too_few_blocks_keeps_the_old_baseline():
    previous = BlockCounters(1000, 10, RESET)
    current = BlockCounters(1000 + MIN_INTERVAL_BLOCKS - 1, 10, RESET)
    assert interval_hit_ratio(previous, current) == (None, previous)


@pytest.mark.parametrize(
    "current",
    [
        BlockCounters(5000, 100, datetime(2026, 2, 1, tzinfo=timezone.utc)),  # stats reset
        BlockCounters(10, 1, RESET),  # counters went backwards
    ],
)
def test_cache_hit_reset_starts_a_new_interval(current):
    previous = BlockCounters(1000, 10, RESET)
    assert interval_hit_ratio(previous, current) == (None, current)


# --- Target backoff ----------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_backoff_doubles_and_caps_then_clears_on_success():
    clock = FakeClock()
    backoff = TargetBackoff(clock)
    assert backoff.should_attempt("t1")

    assert backoff.record_failure("t1") == BACKOFF_BASE_SECONDS
    assert not backoff.should_attempt("t1")
    assert backoff.should_attempt("t2")
    clock.now += BACKOFF_BASE_SECONDS
    assert backoff.should_attempt("t1")

    assert backoff.record_failure("t1") == 2 * BACKOFF_BASE_SECONDS
    for _ in range(10):
        delay = backoff.record_failure("t1")
    assert delay == BACKOFF_MAX_SECONDS

    backoff.record_success("t1")
    assert backoff.should_attempt("t1")
    assert backoff.record_failure("t1") == BACKOFF_BASE_SECONDS


def _fake_store(monkeypatch, targets):
    @contextmanager
    def fake_store_conn():
        yield object()

    monkeypatch.setattr(scheduler, "store_conn", fake_store_conn)
    monkeypatch.setattr(scheduler, "_active_targets", lambda _conn: targets)


def test_for_each_target_skips_backed_off_targets_and_isolates_failures(monkeypatch):
    targets = [(f"t{i}", "h", 5432, "db", "u", "pw", "prefer") for i in range(5)]
    _fake_store(monkeypatch, targets)
    backoff = TargetBackoff(FakeClock())
    backoff.record_failure("t3")
    monkeypatch.setattr(scheduler, "_backoff", backoff)

    seen = []

    def job(target_row):
        seen.append(target_row[0])
        if target_row[0] == "t1":
            raise RuntimeError("store write failed")

    scheduler._for_each_target(job)
    assert sorted(seen) == ["t0", "t1", "t2", "t4"]

    seen.clear()
    scheduler._for_each_target(job, respect_backoff=False)
    assert sorted(seen) == ["t0", "t1", "t2", "t3", "t4"]


# --- Snapshot inserts stay in step with their queries -----------------------


def _select_column_count(query: str) -> int:
    select_list = re.search(r"SELECT(.*?)\bFROM\b", query, re.S).group(1)
    return len([part for part in re.split(r",(?![^()]*\))", select_list) if part.strip()])


@pytest.mark.parametrize(
    ("select_query", "insert_sql"),
    [
        (scheduler.TABLE_METRICS_QUERY, scheduler.TABLE_SNAPSHOT_INSERT),
        (scheduler.INDEX_METRICS_QUERY, scheduler.INDEX_SNAPSHOT_INSERT),
    ],
)
def test_snapshot_insert_matches_its_query(select_query, insert_sql):
    columns = re.search(r"\((.*?)\)\s*VALUES", insert_sql, re.S).group(1).split(",")
    placeholders = insert_sql.split("VALUES", 1)[1].count("%s")
    assert len(columns) == placeholders
    # target_id plus every selected column
    assert placeholders == 1 + _select_column_count(select_query)


# --- xmin horizon --------------------------------------------------------------


def test_xmin_horizon_severity_uses_age_or_duration():
    assert xmin_horizon_severity(1000, 60) == "healthy"
    assert xmin_horizon_severity(1000, XMIN_HORIZON_ATTENTION_SECONDS) == "attention"
    assert xmin_horizon_severity(XMIN_HORIZON_CRITICAL_AGE, None) == "critical"
    assert xmin_horizon_severity(None, None) == "healthy"


def test_xmin_horizon_clear_when_nothing_holds_it():
    category, findings = build_xmin_horizon_status([])
    assert category.severity == "healthy" and category.value == "clear"
    assert findings == []


def test_xmin_horizon_young_snapshot_is_shown_but_not_a_finding():
    category, findings = build_xmin_horizon_status([("backend", "42", "app", "web", "active", "client backend", 3, 2)])
    assert category.severity == "healthy"
    assert "session 42" in category.detail
    assert findings == []


def test_forgotten_prepared_transaction_is_critical_with_a_quoted_fix():
    rows = [
        ("backend", "42", "app", "web", "active", "client backend", 3, 2),
        ("prepared", "batch'7", "app", "billing", None, None, 1200, 3 * 86400),
    ]
    category, findings = build_xmin_horizon_status(rows)
    assert category.severity == "critical"
    assert category.detail == "oldest: prepared transaction 'batch''7'"
    (finding,) = findings
    assert finding["id"] == "xmin-holder-prepared-batch'7"
    assert finding["category"] == "xmin horizon"
    assert "ROLLBACK PREPARED 'batch''7';" in finding["suggested_action"]


def test_slot_and_walsender_holders_get_their_own_advice():
    rows = [
        ("slot", "old_sub", None, None, "inactive", "logical", XMIN_HORIZON_CRITICAL_AGE, None),
        ("backend", "77", None, "replicator", None, "walsender", 20_000_000, None),
    ]
    _category, findings = build_xmin_horizon_status(rows)
    assert [f["id"] for f in findings] == ["xmin-holder-slot-old_sub", "xmin-holder-walsender-77"]
    assert "pg_drop_replication_slot('old_sub')" in findings[0]["suggested_action"]
    assert "hot_standby_feedback" in findings[1]["summary"]


def test_xmin_horizon_findings_are_capped_worst_first():
    rows = [
        ("backend", str(pid), "app", "web", "idle in transaction", "client backend", pid, 3600 + pid)
        for pid in range(1, 10)
    ]
    _category, findings = build_xmin_horizon_status(rows)
    assert len(findings) == MAX_XMIN_FINDINGS
    assert findings[0]["id"] == "xmin-holder-backend-9"


def test_format_duration():
    assert format_duration(45) == "45s"
    assert format_duration(600) == "10 min"
    assert format_duration(3 * 3600) == "3.0 h"
    assert format_duration(3 * 86400) == "3.0 days"
    assert format_duration(None) == "an unknown time"


def test_quote_literal_doubles_quotes():
    assert quote_literal("it's") == "'it''s'"


# --- Wraparound ----------------------------------------------------------------


def test_wraparound_all_healthy():
    category, findings = build_wraparound_status(("public", "t", 1000), 2000, ("public", "t", 10), 20)
    assert category.severity == "healthy"
    assert category.value == "2,000"
    assert findings == []


def test_table_finding_suppresses_the_matching_database_finding():
    category, findings = build_wraparound_status(
        ("public", "orders", WRAPAROUND_ATTENTION), WRAPAROUND_ATTENTION + 5, None, 0
    )
    assert category.severity == "attention"
    assert [f["id"] for f in findings] == ["wraparound-public-orders"]
    assert findings[0]["schema_name"] == "public"


def test_database_finding_when_no_visible_table_explains_the_age():
    # e.g. a system catalog, TOAST table or orphaned temp table holds it back
    category, findings = build_wraparound_status(("public", "orders", 1000), WRAPAROUND_CRITICAL, None, None)
    assert category.severity == "critical"
    assert [f["id"] for f in findings] == ["wraparound-database"]
    assert "VACUUM (FREEZE)" in findings[0]["suggested_action"]


def test_multixact_wraparound_is_checked_too():
    category, findings = build_wraparound_status(
        ("public", "orders", 1000), 2000, ("public", "order_items", WRAPAROUND_CRITICAL), WRAPAROUND_CRITICAL
    )
    assert category.severity == "critical"
    assert "multixact" in category.detail
    assert [f["id"] for f in findings] == ["mxid-wraparound-public-order_items"]


def test_wraparound_unknown_without_any_data():
    category, findings = build_wraparound_status(None, None, None, None)
    assert category.severity == "unknown"
    assert findings == []


# --- Store schema: retention coverage and additive migrations ---------------


def _store_sql_files():
    return [migrate.MIGRATIONS_DIR.parent / "schema.sql"] + [path for _v, path in migrate._migration_files()]


def test_every_store_table_is_pruned_or_explicitly_kept():
    created = set()
    for path in _store_sql_files():
        created |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", path.read_text(encoding="utf-8")))
    pruned = {table for table, _sql, _days in scheduler.RETENTION_RULES}
    assert not pruned & scheduler.RETENTION_EXEMPT_TABLES
    assert created == pruned | scheduler.RETENTION_EXEMPT_TABLES


def test_data_quality_migration_is_additive():
    """An existing store must upgrade in place and keep working with the
    previous backend version, so 004 and later never drop or rename."""
    for version, path in migrate._migration_files():
        if version < 4:
            continue
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8")).upper()
        for forbidden in ("DROP TABLE", "DROP COLUMN", "RENAME", "TRUNCATE", "SET NOT NULL"):
            assert forbidden not in sql, f"{path.name} contains {forbidden}"
