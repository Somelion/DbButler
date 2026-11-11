"""Turns Query History's cumulative pg_stat_statements counters into
per-interval deltas (app/scheduler.py::_collect_query_history).

Kept free of any database access so the rules (first sighting, a reset, a
baseline recorded by an older version) are testable on their own.
"""

from typing import NamedTuple


class QueryCounters(NamedTuple):
    """Cumulative counters for one queryid, summed over every
    pg_stat_statements row that shares it (one per user and top-level flag)."""

    calls: int
    total_exec_ms: float
    rows: int


class Baseline(NamedTuple):
    """The counters recorded last cycle (tracked_queries). rows is None for a
    baseline recorded before the last_rows column existed."""

    calls: int
    total_exec_ms: float
    rows: int | None


class IntervalStats(NamedTuple):
    calls: int
    mean_exec_ms: float
    rows: int


def compute_interval(baseline: Baseline | None, current: QueryCounters) -> IntervalStats | None:
    """This interval's stats, or None when there's nothing honest to record:

    - first sighting (no baseline): its lifetime total isn't "this interval";
    - a baseline without rows (written by an older version from a single
      pg_stat_statements row, not the per-queryid sum): re-baseline instead
      of recording one bogus jump;
    - any counter went backwards: pg_stat_statements_reset() ran, so this
      interval is a gap rather than a negative delta;
    - no new calls: no row, so the chart shows a gap, not a misleading zero.

    The caller always stores `current` as the next baseline."""
    if baseline is None or baseline.rows is None:
        return None
    if (
        current.calls < baseline.calls
        or current.total_exec_ms < baseline.total_exec_ms
        or current.rows < baseline.rows
    ):
        return None
    calls = current.calls - baseline.calls
    if calls <= 0:
        return None
    return IntervalStats(
        calls=calls,
        mean_exec_ms=(current.total_exec_ms - baseline.total_exec_ms) / calls,
        rows=current.rows - baseline.rows,
    )
