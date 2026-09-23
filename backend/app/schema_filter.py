"""Shared plumbing for the connection-level schema allowlist
(targets.allowed_schemas, PROBLEMS.md #9/#10). NULL/empty on a target means
"no filter" — every backend query that lists schemas/tables keeps behaving
exactly as it did before this existed. When a target has allowed_schemas
set, every one of those queries narrows to just the named schemas.
"""

import uuid

from app.db.store import store_conn


def get_allowed_schemas(target_id: uuid.UUID) -> list[str] | None:
    """None (or an empty list) means "no filter" — callers should treat both
    the same way (skip adding a schema clause at all)."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT allowed_schemas FROM targets WHERE id = %s", (target_id,))
        row = cur.fetchone()
    if row is None:
        return None
    allowed = row[0]
    return allowed or None


def schema_filter_sql(column_expr: str, allowed_schemas: list[str] | None) -> str:
    """An `AND {column_expr} = ANY(%s)` fragment for embedding into a query
    template via `query_template.format(schema_filter=...)`, or an empty
    string when no allowlist is set (so the query runs exactly as it did
    before this existed).

    The template must place the resulting `{schema_filter}` output
    immediately after its own WHERE clause's last condition — never after a
    trailing GROUP BY/ORDER BY, since "AND ..." can't legally follow one. A
    query with no WHERE clause of its own should add a harmless `WHERE 1=1`
    right before `{schema_filter}` so this always slots in as an AND."""
    return f"AND {column_expr} = ANY(%s)" if allowed_schemas else ""


def schema_filter_params(allowed_schemas: list[str] | None) -> list:
    """The params list to pass alongside a query built with schema_filter_sql
    — empty when there's no allowlist (matching the empty clause above), one
    array param when there is."""
    return [allowed_schemas] if allowed_schemas else []
