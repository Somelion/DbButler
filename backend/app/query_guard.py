"""Shared read-only guard for any code path that runs EXPLAIN ANALYZE (which
actually executes the query) against a target — routers/explain.py and the AI
Query Analysis background task (routers/query_analysis.py). Refusing anything
that isn't clearly read-only is safer than trying to auto-wrap a write in a
transaction: a SERIAL/IDENTITY sequence advance, for one, survives a
ROLLBACK, so "safe because we rolled back" isn't actually true in general."""

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


def ensure_read_only(query: str) -> str:
    """Strips a trailing semicolon and raises ValueError if the query isn't
    read-only or still has an unfilled $N placeholder. Returns the cleaned
    query string on success."""
    cleaned = query.strip().rstrip(";")
    if not cleaned:
        raise ValueError("Query is empty.")
    if not READ_ONLY_QUERY_PREFIX.match(cleaned):
        raise ValueError(
            "Only read-only SELECT (or WITH ... SELECT) queries can be run here. EXPLAIN "
            "ANALYZE actually executes the query, so anything that writes data is refused "
            "rather than run against your database."
        )
    match = UNFILLED_PLACEHOLDER.search(cleaned)
    if match:
        raise ValueError(
            f"This query still has an unfilled parameter placeholder ({match.group(0)}). "
            "Fill in a value for every placeholder before running it."
        )
    return cleaned
