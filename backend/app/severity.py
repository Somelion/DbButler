"""Severity thresholds shared across the live health-check endpoints.

Kept in one place so a threshold only needs tuning once, and so new checks
(Epic 3/4) can reuse the same scale rather than inventing their own.
"""

SEVERITY_RANK = {"unknown": 0, "healthy": 1, "attention": 2, "critical": 3}


def worse(a: str, b: str) -> str:
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b


# Databank guidance: >5-10% dead tuples on a large table signals bloat; the
# mockup's own worked example treats 42% as "attention," not "critical," so
# the critical threshold sits well above that rather than at 10-20%.
DEAD_PCT_ATTENTION = 10
DEAD_PCT_CRITICAL = 50


def dead_pct_severity(pct: float) -> str:
    if pct >= DEAD_PCT_CRITICAL:
        return "critical"
    if pct >= DEAD_PCT_ATTENTION:
        return "attention"
    return "healthy"


# XID wraparound: alert past 1 billion (of the 2.147 billion hard ceiling).
WRAPAROUND_ATTENTION = 1_000_000_000
WRAPAROUND_CRITICAL = 1_500_000_000


def wraparound_severity(xid_age: int) -> str:
    if xid_age >= WRAPAROUND_CRITICAL:
        return "critical"
    if xid_age >= WRAPAROUND_ATTENTION:
        return "attention"
    return "healthy"


def cache_hit_severity(pct: float | None) -> str:
    """Databank target: cache hit ratio should stay above 95%."""
    if pct is None:
        return "unknown"
    if pct >= 95:
        return "healthy"
    if pct >= 85:
        return "attention"
    return "critical"


def checkpoint_severity(timed: int, requested: int) -> str:
    """A low timed/total ratio means checkpoints are mostly forced by write
    pressure rather than happening on schedule — a WAL-tuning signal."""
    total = timed + requested
    if total == 0:
        return "unknown"
    ratio = timed / total
    if ratio >= 0.8:
        return "healthy"
    if ratio >= 0.5:
        return "attention"
    return "critical"


# A session idle in a transaction holds locks and blocks vacuum on whatever
# it has touched, even though it isn't running a query right now.
IDLE_IN_TX_ATTENTION_SECONDS = 60
IDLE_IN_TX_CRITICAL_SECONDS = 300


def idle_in_tx_severity(seconds: int | None) -> str:
    if seconds is None:
        return "healthy"
    if seconds >= IDLE_IN_TX_CRITICAL_SECONDS:
        return "critical"
    if seconds >= IDLE_IN_TX_ATTENTION_SECONDS:
        return "attention"
    return "healthy"


# An actively-running query (not idle, not idle-in-transaction) held open
# this long is either a genuinely long-running batch job or a query stuck
# behind something else — distinct from idle-in-tx (a dangling open
# transaction with no query running at all), so it gets its own, more
# generous thresholds: a five-minute query isn't unusual the way a
# five-minute idle transaction is.
LONG_RUNNING_QUERY_ATTENTION_SECONDS = 300
LONG_RUNNING_QUERY_CRITICAL_SECONDS = 1800


def long_running_query_severity(seconds: int | None) -> str:
    if seconds is None:
        return "healthy"
    if seconds >= LONG_RUNNING_QUERY_CRITICAL_SECONDS:
        return "critical"
    if seconds >= LONG_RUNNING_QUERY_ATTENTION_SECONDS:
        return "attention"
    return "healthy"


# Databank guidance (Connection Pooling.md): approaching max_connections risks
# outright connection refusals for new sessions, so the warning band starts
# well before the hard ceiling.
CONNECTION_POOL_ATTENTION_PCT = 80
CONNECTION_POOL_CRITICAL_PCT = 95


def connection_pool_severity(used_pct: float) -> str:
    if used_pct >= CONNECTION_POOL_CRITICAL_PCT:
        return "critical"
    if used_pct >= CONNECTION_POOL_ATTENTION_PCT:
        return "attention"
    return "healthy"


# A tracked query's EXPLAIN plan changed shape (app/plan_fingerprint.py)
# between two captures — thresholds are on the ratio of post-change to
# pre-change mean latency (routers/plan_regressions.py), not an absolute ms
# value, since "slow" only means something relative to what this specific
# query normally costs.
PLAN_REGRESSION_ATTENTION_RATIO = 1.5
PLAN_REGRESSION_CRITICAL_RATIO = 3.0


def plan_regression_severity(ratio: float) -> str:
    if ratio >= PLAN_REGRESSION_CRITICAL_RATIO:
        return "critical"
    if ratio >= PLAN_REGRESSION_ATTENTION_RATIO:
        return "attention"
    return "healthy"


# A connected replica (physical standby or logical subscriber — Postgres
# exposes both identically in pg_stat_replication) falling behind means
# stale reads and a replica that's further from being a usable failover
# target the longer it persists. Same order of magnitude as idle-in-tx,
# since both are "how long has something been stuck" signals.
REPLICATION_LAG_ATTENTION_SECONDS = 60
REPLICATION_LAG_CRITICAL_SECONDS = 300


def replication_lag_severity(lag_seconds: float | None) -> str:
    """None means pg_stat_replication.replay_lag hasn't been computed yet
    (right after connecting, or the replica isn't reporting feedback) —
    reported as unknown rather than guessed at."""
    if lag_seconds is None:
        return "unknown"
    if lag_seconds >= REPLICATION_LAG_CRITICAL_SECONDS:
        return "critical"
    if lag_seconds >= REPLICATION_LAG_ATTENTION_SECONDS:
        return "attention"
    return "healthy"


# An inactive replication slot retains WAL for whatever consumer used to
# read it regardless of size — but a slot inactive for a few seconds with
# negligible retained WAL (e.g. right after creation, before its consumer's
# first connection) isn't worth an alert yet. wal_status ('lost'/
# 'unreserved'/'extended'/'reserved', PostgreSQL 13+) is Postgres's own,
# more authoritative signal when available and always overrides the size
# floor below.
INACTIVE_SLOT_MIN_RETAINED_BYTES = 100 * 1024 * 1024


def replication_slot_severity(active: bool, wal_status: str | None, retained_bytes: int) -> str:
    if active:
        return "healthy"
    if wal_status == "lost":
        return "critical"
    if wal_status == "unreserved":
        return "attention"
    if retained_bytes >= INACTIVE_SLOT_MIN_RETAINED_BYTES:
        return "attention"
    return "healthy"


def wal_archiving_severity(failed_count: int, last_archived_time, last_failed_time) -> str:
    """pg_stat_archiver.failed_count > 0 means WAL archiving has failed at
    least once since the last stats reset. Whether that's an active,
    ongoing problem or an old blip already recovered from depends on
    whether the most recent failure is more recent than the most recent
    success — comparing the two timestamps avoids needing a "how stale is
    too stale" heuristic that a quiet/idle database would false-positive on."""
    if failed_count <= 0:
        return "healthy"
    if last_archived_time is None or (last_failed_time is not None and last_failed_time > last_archived_time):
        return "critical"
    return "attention"


# max_wal_size is a soft target Postgres can and does exceed between
# checkpoints under normal load — only a WAL directory well past it signals
# something is actually preventing cleanup (failed archiving, a stalled or
# orphaned replication slot, a stuck replication connection).
WAL_DIR_SIZE_ATTENTION_RATIO = 2.0
WAL_DIR_SIZE_CRITICAL_RATIO = 5.0


def wal_dir_size_severity(wal_dir_bytes: int, max_wal_size_bytes: int) -> str:
    if max_wal_size_bytes <= 0:
        return "unknown"
    ratio = wal_dir_bytes / max_wal_size_bytes
    if ratio >= WAL_DIR_SIZE_CRITICAL_RATIO:
        return "critical"
    if ratio >= WAL_DIR_SIZE_ATTENTION_RATIO:
        return "attention"
    return "healthy"


# A client waiting at all for a pooled server connection (PgBouncer's own
# cl_waiting) means the pool is momentarily undersized for demand; a wait
# of any real duration compounds into the request-latency-cascade research
# calls out as the actual failure mode behind "connection exhaustion," not
# just a raw connection count.
POOLER_MAXWAIT_CRITICAL_SECONDS = 5


def pooler_severity(cl_waiting: int, maxwait_seconds: float) -> str:
    if cl_waiting <= 0:
        return "healthy"
    if maxwait_seconds >= POOLER_MAXWAIT_CRITICAL_SECONDS:
        return "critical"
    return "attention"


# How long since the least-recently-vacuumed table with live rows was last
# touched by autovacuum or a manual VACUUM, either counts. A table that's
# never been vacuumed at all (days_since_last_vacuum=None) is worse than any
# finite staleness — its own Table Health finding
# (table_health_advisor.py::find_never_vacuumed) already flags this per
# table; this is the same signal rolled up to a single Dashboard tile.
AUTOVACUUM_STALE_ATTENTION_DAYS = 7
AUTOVACUUM_STALE_CRITICAL_DAYS = 30


def autovacuum_staleness_severity(days_since_last_vacuum: float | None) -> str:
    if days_since_last_vacuum is None:
        return "critical"
    if days_since_last_vacuum >= AUTOVACUUM_STALE_CRITICAL_DAYS:
        return "critical"
    if days_since_last_vacuum >= AUTOVACUUM_STALE_ATTENTION_DAYS:
        return "attention"
    return "healthy"
