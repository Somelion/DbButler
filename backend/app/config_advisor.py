"""Configuration Advisor analyzers — pure functions over pg_settings /
pg_class.reloptions values, per databank/02 - Concepts/PostgreSQL Bad
Practices.md's "Configuration Anti-Patterns" section. The one Advisor
category that reads server/table settings rather than catalog or
pg_stat_* rows about tables and indexes. Also home to two
cluster-identity checks (server version support status, data checksums)
that aren't settings exactly, but are the same kind of "know this about
your server" nudge and don't warrant a whole extra Advisor tab of their
own.
"""

from datetime import date

RANDOM_PAGE_COST_HIGH_THRESHOLD = 2.0


def find_autovacuum_disabled_findings(global_enabled: bool, per_table_rows: list[tuple]) -> list[dict]:
    """global_enabled: whether the server-wide `autovacuum` setting is on.
    per_table_rows: (schema, table) for tables carrying an explicit
    autovacuum_enabled=false storage parameter override. Disabling
    autovacuum is called out in the databank as "almost never the right
    solution" — it leads to uncontrolled bloat and, eventually, transaction
    ID wraparound."""
    findings = []
    if not global_enabled:
        findings.append(
            {
                "id": "autovacuum-disabled-global",
                "category": "configuration advisor",
                "severity": "critical",
                "title": "Autovacuum is disabled server-wide",
                "summary": (
                    "With autovacuum off, dead rows and transaction ID age accumulate on every table "
                    "with no automatic cleanup. This leads to uncontrolled bloat and, eventually, "
                    "transaction ID wraparound."
                ),
                "detail": "autovacuum=off",
                "suggested_action": (
                    "Turn autovacuum back on; tune its cost/scale-factor settings instead of "
                    "disabling it."
                ),
                "recommended_ddl": None,
            }
        )
    for schema, table in per_table_rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"autovacuum-disabled-{schema}-{table}",
                "category": "configuration advisor",
                "severity": "critical",
                "title": f"Autovacuum is disabled on {full_name}",
                "summary": (
                    "This table has autovacuum_enabled=false set directly on it, overriding the "
                    "server-wide setting. Dead rows and transaction ID age will accumulate here with "
                    "no automatic cleanup."
                ),
                "detail": "autovacuum_enabled=false (table storage parameter)",
                "suggested_action": (
                    "Re-enable autovacuum for this table unless there's a specific, documented reason "
                    "it's off."
                ),
                "recommended_ddl": f"ALTER TABLE {full_name} SET (autovacuum_enabled = true);",
            }
        )
    return findings


def find_fsync_off_findings(fsync_enabled: bool) -> list[dict]:
    """fsync_enabled: whether the server-wide `fsync` setting is on. Turning
    fsync off means PostgreSQL no longer forces WAL writes to durable
    storage before acknowledging a commit — a crash can silently lose or
    corrupt committed data. There's no safe production reason to run with
    this off."""
    if fsync_enabled:
        return []
    return [
        {
            "id": "fsync-disabled",
            "category": "configuration advisor",
            "severity": "critical",
            "title": "fsync is disabled",
            "summary": (
                "With fsync off, PostgreSQL doesn't force WAL writes to durable storage before "
                "acknowledging a commit. A crash or power loss can silently lose or corrupt "
                "committed data."
            ),
            "detail": "fsync=off",
            "suggested_action": "Turn fsync back on — there's no safe production reason to run with it off.",
            "recommended_ddl": None,
        }
    ]


def find_pg_stat_statements_eviction_findings(dealloc: int, stats_reset, pg_stat_statements_max: int) -> list[dict]:
    """dealloc: pg_stat_statements_info.dealloc, how many times an entry was
    evicted because pg_stat_statements.max was reached since the last reset.
    Above 0 means steady, real queries have been competing with one-off
    queries for a fixed number of tracking slots — and some have lost. Every
    screen built on pg_stat_statements (Query Intelligence, Query History,
    Index/Schema Lint's query-pattern checks, the Plan Regression Detector)
    only ever sees whatever's currently tracked, so a query silently
    dropping out of all of them at once is a real, easy-to-miss gap.
    Version-gated by the caller (routers/config_advisor.py) since
    pg_stat_statements_info doesn't exist before PostgreSQL 14."""
    if dealloc <= 0:
        return []
    suggested_max = pg_stat_statements_max * 2
    return [
        {
            "id": "pg-stat-statements-eviction",
            "category": "configuration advisor",
            "severity": "attention",
            "title": f"pg_stat_statements has evicted {dealloc:,} quer{'y' if dealloc == 1 else 'ies'} since the last reset",
            "summary": (
                f"pg_stat_statements.max ({pg_stat_statements_max:,}) has been hit at least once — once "
                "full, tracking a new distinct query evicts an existing one. A steady query can silently "
                "drop out of Query Intelligence, Query History, and every advisor that reads "
                "pg_stat_statements, in favor of one-off queries."
            ),
            "detail": (
                f"dealloc={dealloc} pg_stat_statements.max={pg_stat_statements_max} "
                f"stats_reset={stats_reset.isoformat() if stats_reset else 'never'}"
            ),
            "suggested_action": (
                f"If broader query coverage matters here, raise pg_stat_statements.max (e.g. to "
                f"{suggested_max:,}) — takes a full restart, not just a reload, since it's a "
                "shared_preload_libraries parameter. Resetting stats (Query Intelligence's \"Reset "
                "query stats\") won't fix this on its own if the same query volume keeps cycling "
                "through the cap."
            ),
            "recommended_ddl": f"ALTER SYSTEM SET pg_stat_statements.max = {suggested_max};",
        }
    ]


def find_random_page_cost_findings(random_page_cost: float) -> list[dict]:
    """random_page_cost: the server's current `random_page_cost` setting.
    The default (4.0) assumes spinning-disk seek costs; on SSD-backed
    storage (the common case today) it systematically biases the planner
    away from indexes even when they'd be faster. A soft heuristic — this
    app has no way to confirm the actual storage medium, so it stays
    informational rather than a hard recommendation."""
    if random_page_cost < RANDOM_PAGE_COST_HIGH_THRESHOLD:
        return []
    return [
        {
            "id": "random-page-cost-high",
            "category": "configuration advisor",
            "severity": "unknown",
            "title": f"random_page_cost is {random_page_cost:g}",
            "summary": (
                "This is PostgreSQL's spinning-disk-era default. On SSD-backed storage (the common "
                "case today), it overstates random I/O cost and can push the planner toward "
                "sequential scans even when an index would be faster."
            ),
            "detail": f"random_page_cost={random_page_cost}",
            "suggested_action": "If this database runs on SSD, consider lowering random_page_cost to around 1.1.",
            "recommended_ddl": None,
        }
    ]


def find_synchronous_commit_off_findings(synchronous_commit: str) -> list[dict]:
    """synchronous_commit: the server's current setting ('on'/'off'/
    'local'/'remote_write'/'remote_apply'). 'off' means a COMMIT can return
    to the client before its WAL record is even flushed to local disk — a
    crash (not just a failover) can lose the most recent transactions that
    were reported as committed. Often a deliberate, documented trade-off for
    write-heavy workloads that can tolerate losing a few seconds of commits,
    so this stays informational rather than a hard warning."""
    if synchronous_commit != "off":
        return []
    return [
        {
            "id": "synchronous-commit-off",
            "category": "configuration advisor",
            "severity": "unknown",
            "title": "synchronous_commit is off",
            "summary": (
                "A COMMIT can return to the client before its WAL record is flushed to disk — a "
                "crash can silently lose the most recently \"committed\" transactions. Sometimes a "
                "deliberate trade-off for write-heavy workloads that can tolerate losing the last "
                "second or two of commits."
            ),
            "detail": "synchronous_commit=off",
            "suggested_action": "Confirm this is an intentional durability trade-off, not an overlooked default.",
            "recommended_ddl": None,
        }
    ]


def find_full_page_writes_off_findings(full_page_writes: bool) -> list[dict]:
    """full_page_writes: the server's current setting. Off means a crash
    mid-write can leave a "torn page" (partially written) that WAL replay
    can't repair, unless the underlying filesystem itself guarantees atomic
    block writes (e.g. ZFS, Btrfs with COW) — this app has no way to know
    the storage stack, so it stays attention rather than critical."""
    if full_page_writes:
        return []
    return [
        {
            "id": "full-page-writes-off",
            "category": "configuration advisor",
            "severity": "attention",
            "title": "full_page_writes is off",
            "summary": (
                "Without full_page_writes, a crash mid-write can leave a torn (partially written) "
                "page that WAL replay can't repair — safe only on a filesystem that itself guarantees "
                "atomic block writes (e.g. ZFS, copy-on-write Btrfs)."
            ),
            "detail": "full_page_writes=off",
            "suggested_action": (
                "Turn it back on unless the underlying filesystem is confirmed to guarantee atomic "
                "page writes on its own."
            ),
            "recommended_ddl": None,
        }
    ]


# Each major version's official end-of-life date (postgresql.org/support/versioning) —
# five years of support from initial release, always the second Thursday of
# November. Versions this app can't identify (older than 13, its own
# documented minimum, or newer than this table's most recent entry) simply
# get no finding rather than a guess.
PG_MAJOR_VERSION_EOL = {
    13: date(2025, 11, 13),
    14: date(2026, 11, 12),
    15: date(2027, 11, 11),
    16: date(2028, 11, 9),
    17: date(2029, 11, 8),
    18: date(2030, 11, 14),
}
VERSION_EOL_WARNING_WINDOW_DAYS = 180


def find_version_eol_findings(major_version: int, today: date) -> list[dict]:
    """major_version: SHOW server_version_num // 10000 (the PG10+ numbering
    scheme this app's own PG13+ minimum already assumes). today: the
    server's current date, passed in rather than computed here so this stays
    a pure function."""
    eol_date = PG_MAJOR_VERSION_EOL.get(major_version)
    if eol_date is None:
        return []

    days_remaining = (eol_date - today).days
    if days_remaining <= 0:
        severity = "critical"
        phrase = f"reached end of life on {eol_date.isoformat()} — no more security patches"
    elif days_remaining <= VERSION_EOL_WARNING_WINDOW_DAYS:
        severity = "attention"
        phrase = f"reaches end of life on {eol_date.isoformat()}, {days_remaining} days from now"
    else:
        return []

    return [
        {
            "id": f"version-eol-{major_version}",
            "category": "configuration advisor",
            "severity": severity,
            "title": f"PostgreSQL {major_version} {phrase}",
            "summary": (
                f"PostgreSQL {major_version} {phrase}. Once a major version is out of support, no "
                "further security patches are released for it."
            ),
            "detail": f"major_version={major_version} eol_date={eol_date.isoformat()}",
            "suggested_action": "Plan an upgrade to a still-supported major version before this deadline.",
            "recommended_ddl": None,
        }
    ]


def find_checksums_disabled_findings(data_page_checksum_version: int | None) -> list[dict]:
    """data_page_checksum_version: pg_control_init()'s own column, 0 when
    data checksums were never enabled at initdb time (or since, via
    pg_checksums --enable). None means the connected role isn't granted
    EXECUTE on pg_control_init() (superuser-only by default; this app's
    recommended pg_monitor role isn't granted it) — the caller checks
    has_function_privilege() first rather than attempting the call and
    catching a permission error, since a failed query would abort the rest
    of this function's shared transaction. Without checksums, silent
    storage corruption (a failing disk, a bad RAID controller) has no way
    to be detected other than the query that happens to touch the
    corrupted page returning wrong results or crashing."""
    if data_page_checksum_version is None or data_page_checksum_version != 0:
        return []
    return [
        {
            "id": "data-checksums-disabled",
            "category": "configuration advisor",
            "severity": "unknown",
            "title": "Data checksums are disabled",
            "summary": (
                "With data checksums off, silent storage corruption (a failing disk, a bad RAID "
                "controller) has no way to be detected other than a query happening to touch the "
                "corrupted page and returning wrong results or crashing."
            ),
            "detail": "data_page_checksum_version=0",
            "suggested_action": (
                "Enable checksums with pg_checksums --enable — the server must be shut down cleanly "
                "first; there's no online-enable option before PostgreSQL 19. New PostgreSQL 18+ "
                "clusters have checksums on by default, so this is most likely an older cluster or "
                "one explicitly initialized with --no-data-checksums."
            ),
            "recommended_ddl": None,
        }
    ]
