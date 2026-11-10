"""Shared read-only guard for any code path that runs EXPLAIN ANALYZE (which
actually executes the query) against a target — routers/explain.py and the AI
Query Analysis background task (routers/query_analysis.py).

Two layers, since neither alone is enough:

1. ensure_read_only() below rejects anything that isn't a single SELECT/WITH
   statement, plus calls to well-known side-effecting functions. A prefix
   check alone isn't enough — `WITH d AS (DELETE ... RETURNING *) SELECT ...`
   starts with WITH, and `SELECT 1; DROP TABLE t` starts with SELECT.
2. run_explain_analyze() runs the EXPLAIN inside a READ ONLY transaction that
   is always rolled back, so PostgreSQL itself refuses any write the text
   check missed (a data-modifying CTE, nextval(), a volatile function that
   writes) — "cannot execute DELETE in a read-only transaction."

Neither replaces running DbButler under a least-privilege monitoring role
(see README) — a read-only transaction still allows e.g. pg_terminate_backend
for a role that has the privilege, which is why layer 1 blocks those calls by
name."""

import re

READ_ONLY_QUERY_PREFIX = re.compile(r"^\s*(--[^\n]*\n\s*)*(select|with)\b", re.IGNORECASE)

# pg_stat_statements normalizes literal values into $1/$2/... — that's the
# query text every caller here starts from (Query Intelligence is the only
# source of an analyzable query). Left unfilled, Postgres treats "$1" as an
# extended-query-protocol bind parameter and fails with a cryptic "there is
# no parameter $1"; the UI fills these in before submitting (ExplainView.jsx/
# QueryAnalysisModal.jsx + queryParams.js), but this catches it clearly if
# one ever slips through — an API caller bypassing the UI, for instance.
UNFILLED_PLACEHOLDER = re.compile(r"\$\d+\b")

# Functions that act outside the current transaction (signal or kill other
# sessions, touch files, run SQL over another connection, change server or
# session state) — a READ ONLY transaction doesn't stop any of these.
SIDE_EFFECT_FUNCTIONS = re.compile(
    r"\b("
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_promote|"
    r"pg_switch_wal|pg_create_restore_point|pg_backup_start|pg_backup_stop|pg_start_backup|pg_stop_backup|"
    r"pg_create_physical_replication_slot|pg_create_logical_replication_slot|pg_drop_replication_slot|"
    r"pg_replication_origin_\w+|pg_stat_reset\w*|pg_stat_statements_reset|"
    r"pg_advisory_\w*lock\w*|pg_try_advisory_\w*lock\w*|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_file_write|pg_file_rename|pg_file_unlink|"
    r"lo_import|lo_export|lo_unlink|"
    r"dblink\w*|set_config|pg_sleep\w*"
    r")\s*\(",
    re.IGNORECASE,
)

_SINGLE_QUOTED_LITERAL = re.compile(r"'(?:[^']|'')*'")
_DOUBLE_QUOTED_IDENTIFIER = re.compile(r'"(?:[^"]|"")*"')
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_literals_and_comments(query: str) -> str:
    """The query text with string literals, quoted identifiers and comments
    blanked out — so a semicolon or function name that only appears inside
    one of those doesn't trip the checks below."""
    text = _BLOCK_COMMENT.sub(" ", query)
    text = _LINE_COMMENT.sub(" ", text)
    text = _SINGLE_QUOTED_LITERAL.sub("''", text)
    return _DOUBLE_QUOTED_IDENTIFIER.sub('""', text)


def ensure_read_only(query: str) -> str:
    """Strips a trailing semicolon and raises ValueError if the query isn't
    a single read-only SELECT/WITH statement, calls a side-effecting
    function, or still has an unfilled $N placeholder. Returns the cleaned
    query string on success."""
    cleaned = query.strip().rstrip(";").rstrip()
    if not cleaned:
        raise ValueError("Query is empty.")
    if not READ_ONLY_QUERY_PREFIX.match(cleaned):
        raise ValueError(
            "Only read-only SELECT (or WITH ... SELECT) queries can be run here. EXPLAIN "
            "ANALYZE actually executes the query, so anything that writes data is refused "
            "rather than run against your database."
        )

    code_only = _strip_literals_and_comments(cleaned)
    if ";" in code_only:
        raise ValueError(
            "Only a single statement can be run here — remove everything after the first semicolon."
        )
    side_effect = SIDE_EFFECT_FUNCTIONS.search(code_only)
    if side_effect:
        raise ValueError(
            f"This query calls {side_effect.group(1)}(), which acts outside the query itself "
            "(other sessions, files, or server state), so it isn't run here."
        )

    match = UNFILLED_PLACEHOLDER.search(code_only)
    if match:
        raise ValueError(
            f"This query still has an unfilled parameter placeholder ({match.group(0)}). "
            "Fill in a value for every placeholder before running it."
        )
    return cleaned


def run_explain_analyze(conn, query: str) -> str:
    """Runs EXPLAIN (ANALYZE, BUFFERS) for an already ensure_read_only()-
    checked query on a non-autocommit target connection and returns the
    plan text. The transaction is READ ONLY and is always rolled back —
    including on success, since psycopg's `with connect()` would otherwise
    commit on a clean exit."""
    conn.read_only = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {query}")
            return "\n".join(row[0] for row in cur.fetchall())
    finally:
        conn.rollback()
        conn.read_only = None
