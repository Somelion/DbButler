"""Cache hit rate over recent activity rather than since the last stats reset.

pg_stat_database's blks_hit/blks_read are lifetime counters, so a ratio of
the raw values is an average over weeks or months and barely moves when the
cache actually starts missing. The scheduler (app/scheduler.py) samples the
counters every collection cycle and stores the ratio of the *difference*
between samples instead.
"""

from datetime import datetime
from typing import NamedTuple

# Below this many block reads in an interval, the ratio is noise (a quiet
# database where a handful of reads swing it between 0% and 100%). Fewer
# blocks than this keep the old baseline, so the interval just grows until
# there's enough activity to say something.
MIN_INTERVAL_BLOCKS = 500

BLOCK_COUNTERS_QUERY = """
    SELECT blks_hit, blks_read, stats_reset
    FROM pg_stat_database
    WHERE datname = current_database()
"""

# The fallback when no recent interval sample exists yet (right after
# startup, or for a target the scheduler hasn't reached).
LIFETIME_CACHE_HIT_QUERY = """
    SELECT 100.0 * blks_hit / nullif(blks_hit + blks_read, 0)
    FROM pg_stat_database
    WHERE datname = current_database()
"""


class BlockCounters(NamedTuple):
    hit: int
    read: int
    stats_reset: datetime | None


def interval_hit_ratio(
    previous: BlockCounters | None, current: BlockCounters
) -> tuple[float | None, BlockCounters]:
    """Returns (ratio for the interval since `previous`, the baseline to keep).

    The ratio is None when there's no usable interval: no previous sample,
    the stats were reset in between (or a counter went backwards, e.g. a
    server restart without stats), or too few blocks were read to mean
    anything — in that last case the previous baseline is kept so the next
    sample measures a longer interval."""
    if (
        previous is None
        or previous.stats_reset != current.stats_reset
        or current.hit < previous.hit
        or current.read < previous.read
    ):
        return None, current
    hit_delta = current.hit - previous.hit
    total_delta = hit_delta + (current.read - previous.read)
    if total_delta < MIN_INTERVAL_BLOCKS:
        return None, previous
    return 100.0 * hit_delta / total_delta, current
