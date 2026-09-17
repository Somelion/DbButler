"""Shared pg_stat_statements plumbing — used by the query-stats endpoint and
by the dashboard's findings check, so the enable-it message stays one string.
"""

ENABLED_CHECK_QUERY = "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"

# total_time/mean_time were renamed to total_exec_time/mean_exec_time in
# PostgreSQL 13, when pg_stat_statements started tracking planning time
# separately from execution time — this app's stated minimum server
# version. A server older than this can still have the extension
# *installed*, just under the old column names TOP_QUERIES_QUERY doesn't
# use, so "installed" alone doesn't guarantee these queries will run —
# is_enabled below checks both, not just the extension.
MIN_SERVER_VERSION_NUM = 130000

NOT_INSTALLED_MESSAGE = (
    "The pg_stat_statements extension isn't enabled on this database. Add "
    "'pg_stat_statements' to shared_preload_libraries in postgresql.conf, restart "
    "PostgreSQL, then run: CREATE EXTENSION pg_stat_statements;"
)

TOO_OLD_MESSAGE = (
    "This database is running a PostgreSQL version older than 13, which PostgreDba's query "
    "statistics features don't support — pg_stat_statements renamed its timing columns in "
    "PostgreSQL 13. Upgrade the target to PostgreSQL 13 or newer to use this feature."
)

# Kept for existing importers — NOT_INSTALLED_MESSAGE is the more precise
# name now that there are two distinct reasons, but this alias avoids
# touching every call site that only ever showed the "not installed" case.
NOT_ENABLED_MESSAGE = NOT_INSTALLED_MESSAGE

TOP_QUERIES_QUERY = """
    SELECT pss.query, pss.calls, pss.total_exec_time, pss.mean_exec_time, pss.rows
    FROM pg_stat_statements pss
    JOIN pg_database d ON d.oid = pss.dbid
    WHERE d.datname = current_database()
    ORDER BY pss.total_exec_time DESC
    LIMIT %s
"""


def _extension_installed(cur) -> bool:
    cur.execute(ENABLED_CHECK_QUERY)
    return cur.fetchone() is not None


def is_enabled(cur) -> bool:
    """True only when pg_stat_statements is installed AND the server is new
    enough (PostgreSQL 13+) for TOP_QUERIES_QUERY's column names to exist —
    without the version check, a pre-13 server with the extension installed
    still reads as "enabled" here, and the very next query fails with a raw
    "column pss.total_exec_time does not exist" instead of degrading
    gracefully. Callers that need to explain *why* this returned False
    should call installed_but_too_old(cur) or unavailable_message(cur) —
    the two reasons need completely different fixes (create the extension
    vs. upgrade Postgres)."""
    if not _extension_installed(cur):
        return False
    cur.execute("SHOW server_version_num")
    return int(cur.fetchone()[0]) >= MIN_SERVER_VERSION_NUM


def installed_but_too_old(cur) -> bool:
    """Only meaningful right after is_enabled(cur) has returned False —
    distinguishes "extension not installed at all" from "installed, but the
    server is older than PostgreSQL 13"."""
    return _extension_installed(cur)


def unavailable_message(cur) -> str:
    """Convenience for callers that just want one user-facing string,
    already picking the right one for whichever reason applies. Only
    meaningful right after is_enabled(cur) has returned False."""
    return TOO_OLD_MESSAGE if installed_but_too_old(cur) else NOT_INSTALLED_MESSAGE
