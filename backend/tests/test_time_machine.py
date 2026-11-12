from datetime import datetime, timedelta, timezone

from app import scheduler
from app.routers.timeline import _bucket, _event_marker, _window
from app.schemas import LockTree
from app.time_machine import (
    SnapshotSummary,
    build_lock_snapshot,
    diff_settings,
    group_incidents,
    incident_facts,
    mask_query,
    root_blockers,
    strip_comments_and_open_literal,
)
from app.workload_extract import Table
from app.workload_graph import QueryInfo, RelationRow, build_workload_graph

T0 = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)


def lock_row(pid, blocked_by, query, state="active", state_seconds=5, app="api"):
    return (pid, blocked_by, "app_user", app, "client backend", state, "Lock", "transactionid", query, 6, 8, state_seconds)


def test_lock_snapshot_masks_literals_and_counts_blocked_sessions():
    rows = [
        lock_row(101, [], "UPDATE orders SET note = 'call alice@example.com' WHERE id = 42", state="idle in transaction"),
        lock_row(202, [101], "UPDATE orders SET total = 10 WHERE id = 42", state_seconds=30),
        lock_row(303, [202], "DELETE FROM orders WHERE id = 42", state_seconds=12),
    ]
    snapshot = build_lock_snapshot(
        rows,
        {202: ("ShareLock on transaction 900", 25), 303: ("ExclusiveLock on public.orders row", 12)},
    )
    assert snapshot["blocked_count"] == 2
    # The real lock wait (pg_locks.waitstart), not time since state change.
    assert snapshot["max_wait_seconds"] == 25
    texts = " ".join(s["query"] for s in snapshot["sessions"])
    assert "alice@example.com" not in texts and "42" not in texts
    by_pid = {s["pid"]: s for s in snapshot["sessions"]}
    assert by_pid[202]["waiting_on"] == "ShareLock on transaction 900"
    assert by_pid[101]["waiting_on"] is None and by_pid[101]["wait_seconds"] is None
    LockTree(collected_at=T0, blocked_count=2, max_wait_seconds=30, root_pids=[101], sessions=snapshot["sessions"])


def test_before_pg14_wait_falls_back_to_state_age():
    rows = [lock_row(101, [], "SELECT 1"), lock_row(202, [101], "SELECT 2", state_seconds=40)]
    snapshot = build_lock_snapshot(rows, {202: ("ShareLock on transaction 9", None)})
    assert snapshot["max_wait_seconds"] == 40


def test_truncated_literals_and_comments_are_never_stored():
    # pg_stat_activity cut the text inside a literal; a sqlcommenter tag
    # carries a user id.
    text = "/* user_id='u-123' */ INSERT INTO notes VALUES ('Alice', 'alice@x.com', 'long free text about her acc"
    masked = mask_query(text)
    for secret in ("u-123", "Alice", "alice@x.com", "free text"):
        assert secret not in masked
    assert strip_comments_and_open_literal("SELECT '--not a comment' -- real comment\nFROM t") == (
        "SELECT '--not a comment'  \nFROM t"
    )
    assert strip_comments_and_open_literal("SELECT 'it''s' /* x */ FROM t") == "SELECT 'it''s'   FROM t"


def test_no_snapshot_when_the_wait_cleared():
    assert build_lock_snapshot([lock_row(101, [], "SELECT 1")], {}) is None


def test_root_blockers_include_prepared_transactions_and_absent_pids():
    sessions = [
        {"pid": 202, "blocked_by": [101]},
        {"pid": 303, "blocked_by": [202]},
        {"pid": 404, "blocked_by": [0]},
        {"pid": 101, "blocked_by": []},
    ]
    assert root_blockers(sessions) == [0, 101]


def test_snapshots_group_into_incidents_by_gap():
    snaps = [
        SnapshotSummary(T0, 1, 5),
        SnapshotSummary(T0 + timedelta(seconds=15), 3, 20),
        SnapshotSummary(T0 + timedelta(seconds=30), 2, 35),
        SnapshotSummary(T0 + timedelta(minutes=10), 1, 3),
    ]
    first, second = group_incidents(snaps)
    assert (first["snapshots"], first["max_blocked"], first["max_wait_seconds"]) == (3, 3, 35)
    assert first["end"] - first["start"] == timedelta(seconds=30)
    assert second["snapshots"] == 1


def test_settings_diff_has_a_baseline_and_reports_changes():
    assert diff_settings(None, {"work_mem": "4096 kB"}) == []
    changes = diff_settings(
        {"work_mem": "4096 kB", "max_connections": "100"},
        {"work_mem": "65536 kB", "max_connections": "100", "jit": "off"},
    )
    assert changes == [
        {"name": "jit", "old": None, "new": "off"},
        {"name": "work_mem", "old": "4096 kB", "new": "65536 kB"},
    ]


def test_ai_facts_carry_no_query_text():
    incidents = group_incidents([SnapshotSummary(T0, 4, 90)])
    facts = incident_facts(
        T0,
        T0 + timedelta(hours=1),
        incidents,
        [{"waiting_on": ["ExclusiveLock on public.orders row"], "blocker_apps": ["billing"], "blocker_states": ["idle in transaction"]}],
        [{"at": T0, "summary": "Seq Scan on orders"}],
        [{"at": T0, "severity": "critical", "title": "Long-running transaction"}],
        [{"at": T0, "kind": "config_change", "detail": {"changes": [{"name": "work_mem", "old": "4MB", "new": "64MB"}]}}],
        {"Connections": {"min": 10, "max": 80, "avg": 30}},
    )
    assert "up to 4 sessions blocked, longest wait 90s" in facts
    assert "billing" in facts and "public.orders" in facts and "work_mem" in facts
    assert "SELECT" not in facts and "UPDATE" not in facts


def test_window_and_bucket_sizes():
    start, end = _window(24, T0)
    assert end == T0 and end - start == timedelta(hours=24)
    assert _bucket(start, end) == timedelta(minutes=4.8)
    small_start, small_end = _window(1, T0)
    assert _bucket(small_start, small_end) == timedelta(seconds=30)
    assert _window(10_000, T0)[1] - _window(10_000, T0)[0] == timedelta(hours=720)


def test_config_change_marker_lists_changes():
    marker = _event_marker(T0, "config_change", {"changes": [{"name": "work_mem", "old": "4MB", "new": "64MB"}]})
    assert marker["title"] == "Configuration changed (1)"
    assert marker["detail"] == "work_mem: 4MB → 64MB"


def test_past_window_never_borrows_lifetime_weights():
    query = QueryInfo(1, 11, "SELECT 1", 0.0, 0, 500.0, 50, "ok")
    graph = build_workload_graph(
        hours=24,
        queries=[query],
        relations=[RelationRow(1, Table("public", "orders"), ["seq"], [])],
        joins=[],
        foreign_keys=[],
        index_leading_columns=set(),
        table_stats={},
        stats_window_hours=None,
        severities={},
        include_table=lambda t: True,
        allow_lifetime_fallback=False,
        until=T0,
    )
    assert graph["meta"]["weight_basis"] == "window"
    assert graph["meta"]["until"] == T0
    assert graph["nodes"][0]["db_time_ms"] == 0.0


def test_new_jobs_are_registered_and_pruned():
    assert {"lock_snapshot_cycle", "events_cycle"} <= scheduler.KNOWN_JOB_NAMES
    pruned = {table for table, _sql, _days in scheduler.RETENTION_RULES}
    assert {"lock_snapshots", "target_events"} <= pruned
    assert "settings_snapshots" in scheduler.RETENTION_EXEMPT_TABLES
