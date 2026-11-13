"""Shared pg_stat_statements plumbing — used by the query-stats endpoint and
by the dashboard's findings check, so the enable-it message stays one string.
"""

# Where the extension lives, whether that schema is on this session's
# search_path, and whether the role may use it. Managed PostgreSQL services
# (and careful DBAs) often install extensions into their own schema
# (CREATE EXTENSION pg_stat_statements SCHEMA extensions) — then the
# unqualified name every query here uses doesn't resolve.
EXTENSION_LOCATION_QUERY = """
    SELECT n.nspname,
           n.nspname = ANY(current_schemas(false)),
           has_schema_privilege(n.oid, 'USAGE')
    FROM pg_extension e
    JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'pg_stat_statements'
"""

# Appends a schema to this session's search_path (after whatever it has).
APPEND_SEARCH_PATH_QUERY = """
    SELECT set_config(
        'search_path',
        CASE WHEN current_setting('search_path') = '' THEN quote_ident(%(schema)s)
             ELSE current_setting('search_path') || ', ' || quote_ident(%(schema)s) END,
        false
    )
"""

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

NO_SCHEMA_ACCESS_MESSAGE = (
    "pg_stat_statements is installed in schema {schema}, but the role DbButler connects as may not "
    "use that schema. Run: GRANT USAGE ON SCHEMA {schema} TO <monitoring role>;"
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


def _extension_location(cur) -> tuple[str, bool, bool] | None:
    """(schema, on search_path, usable) or None when not installed."""
    cur.execute(EXTENSION_LOCATION_QUERY)
    return cur.fetchone()


def _extension_installed(cur) -> bool:
    return _extension_location(cur) is not None


def is_enabled(cur) -> bool:
    """True only when pg_stat_statements is installed AND the server is new
    enough (PostgreSQL 13+) for TOP_QUERIES_QUERY's column names to exist —
    without the version check, a pre-13 server with the extension installed
    still reads as "enabled" here, and the very next query fails with a raw
    "column pss.total_exec_time does not exist" instead of degrading
    gracefully. Callers that need to explain *why* this returned False
    should call installed_but_too_old(cur) or unavailable_message(cur) —
    the two reasons need completely different fixes (create the extension
    vs. upgrade Postgres).

    When the extension sits in a schema that isn't on the session's
    search_path, that schema is appended to it (for this session only), so
    the unqualified pg_stat_statements in every caller's SQL resolves. Every
    caller runs this check before its first pg_stat_statements query on the
    same connection. A schema the role may not use reads as "not enabled"
    (unavailable_message says how to grant it)."""
    location = _extension_location(cur)
    if location is None:
        return False
    schema, on_search_path, usable = location
    if not usable:
        return False
    if not on_search_path:
        cur.execute(APPEND_SEARCH_PATH_QUERY, {"schema": schema})
    cur.execute("SHOW server_version_num")
    return int(cur.fetchone()[0]) >= MIN_SERVER_VERSION_NUM


def installed_but_too_old(cur) -> bool:
    """Only meaningful right after is_enabled(cur) has returned False —
    distinguishes "extension not installed at all" from "installed, but the
    server is older than PostgreSQL 13"."""
    location = _extension_location(cur)
    return location is not None and location[2]


def installed_without_access(cur) -> str | None:
    """The extension's schema when it's installed but the role may not use
    that schema; None otherwise."""
    location = _extension_location(cur)
    return location[0] if location is not None and not location[2] else None


def unavailable_message(cur) -> str:
    """Convenience for callers that just want one user-facing string,
    already picking the right one for whichever reason applies. Only
    meaningful right after is_enabled(cur) has returned False."""
    location = _extension_location(cur)
    if location is None:
        return NOT_INSTALLED_MESSAGE
    schema, _on_search_path, usable = location
    if not usable:
        return NO_SCHEMA_ACCESS_MESSAGE.format(schema=schema)
    return TOO_OLD_MESSAGE
