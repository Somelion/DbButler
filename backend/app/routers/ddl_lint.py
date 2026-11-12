"""Migration linter: POST a migration, get back what each statement locks,
for how long, what's risky about it (app/ddl_lint.py) — and, with a target
chosen, what it would do there right now: table sizes, sessions holding
conflicting locks, and recent traffic.

Nothing in the pasted SQL is ever executed. With a target, the reads are
catalog and pg_locks / pg_stat_activity SELECTs on one autocommit
connection (the usual statement and lock timeouts apply), plus the store's
own table_stat_snapshots for traffic.
"""

import uuid
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.ddl_lint import PG_LOCKS_MODE, SEVERITY_ORDER, LintResult, describe_impact, lint
from app.schemas import MigrationLintRequest, MigrationLintResponse
from app.sql_ident import quote_qualified
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api", tags=["migration-lint"])

# The relation a name resolves to (search_path applies to an unqualified
# one) and, for an index, the table it belongs to.
RELATION_SQL = """
    SELECT coalesce(ix.indrelid, c.oid)
    FROM pg_class c
    LEFT JOIN pg_index ix ON ix.indexrelid = c.oid
    WHERE c.oid = to_regclass(%s)
"""

# A partitioned table has no storage of its own: size and row estimate are
# summed over its partitions.
TABLE_SQL = """
    SELECT n.nspname, c.relname, c.relkind,
           CASE WHEN c.relkind = 'p'
                THEN (SELECT sum(pg_total_relation_size(t.relid)) FROM pg_partition_tree(c.oid) t)
                ELSE pg_total_relation_size(c.oid) END::bigint,
           CASE WHEN c.relkind = 'p'
                THEN (SELECT sum(greatest(p.reltuples, 0)) FROM pg_partition_tree(c.oid) t JOIN pg_class p ON p.oid = t.relid)
                ELSE greatest(c.reltuples, 0) END::bigint
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.oid = %s
"""

HOLDERS_SQL = """
    SELECT l.pid, l.mode, l.granted, a.application_name, a.state,
           extract(epoch FROM now() - a.xact_start)::bigint
    FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.locktype = 'relation' AND l.relation = %s
      AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
      AND l.pid <> pg_backend_pid()
"""

OLDEST_TRANSACTION_SQL = """
    SELECT extract(epoch FROM now() - min(xact_start))::bigint
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
      AND xact_start IS NOT NULL AND backend_type = 'client backend'
"""

TRAFFIC_SQL = """
    SELECT collected_at,
           coalesce(seq_scan, 0) + coalesce(idx_scan, 0),
           coalesce(n_tup_ins, 0) + coalesce(n_tup_upd, 0) + coalesce(n_tup_del, 0)
    FROM table_stat_snapshots
    WHERE target_id = %s AND schema_name = %s AND table_name = %s
      AND collected_at >= now() - interval '1 hour'
    ORDER BY collected_at
"""

RELKIND_LABELS = {"r": "table", "p": "partitioned table", "m": "materialized view", "i": "index", "I": "index"}


def _regclass_text(schema: str | None, name: str) -> str:
    return quote_qualified(schema, name) if schema else quote_qualified(name)


class TargetCatalog:
    """app/ddl_lint.py's Catalog against a live target. A failed read is
    "don't know" (None), not an error — the lint still stands without it."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self._version: int | None = None
        self._volatility: dict[str, str | None] = {}

    def fetch_one(self, sql: str, params) -> tuple | None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        except psycopg.Error:
            if self.conn.broken:
                raise
            return None

    def server_version(self) -> int | None:
        if self._version is None:
            row = self.fetch_one("SELECT current_setting('server_version_num')::int", None)
            self._version = row[0] if row else None
        return self._version

    def function_volatility(self, name: str) -> str | None:
        # The most volatile overload: 'i' < 's' < 'v'.
        if name not in self._volatility:
            row = self.fetch_one("SELECT max(provolatile::text) FROM pg_proc WHERE proname = %s", (name,))
            self._volatility[name] = row[0] if row else None
        return self._volatility[name]

    def column_type(self, relation: tuple[str | None, str], column: str) -> str | None:
        row = self.fetch_one(
            """
            SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
            WHERE a.attrelid = to_regclass(%s) AND a.attname = %s AND NOT a.attisdropped
            """,
            (_regclass_text(*relation), column),
        )
        return row[0] if row else None


def _traffic(target_id: uuid.UUID, schema: str, table: str) -> tuple[float | None, float | None]:
    """Scans and row changes per second over the last hour, from the
    table_stat_snapshots the table metrics job writes every 5 minutes."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(TRAFFIC_SQL, (target_id, schema, table))
        rows = cur.fetchall()
    if len(rows) < 2:
        return None, None
    (first_at, first_scans, first_writes), (last_at, last_scans, last_writes) = rows[0], rows[-1]
    seconds = (last_at - first_at).total_seconds()
    if seconds <= 0 or last_scans < first_scans or last_writes < first_writes:  # stats reset in between
        return None, None
    return (last_scans - first_scans) / seconds, (last_writes - first_writes) / seconds


def _relation_facts(catalog: TargetCatalog, target_id: uuid.UUID, schema: str | None, name: str) -> dict:
    resolved = catalog.fetch_one(RELATION_SQL, (_regclass_text(schema, name),))
    if resolved is None or resolved[0] is None:
        return {"exists": False}
    table_oid = resolved[0]
    row = catalog.fetch_one(TABLE_SQL, (table_oid,))
    if row is None:
        return {"exists": False}
    nspname, relname, relkind, total_bytes, rows_estimate = row
    holders = []
    try:
        with catalog.conn.cursor() as cur:
            cur.execute(HOLDERS_SQL, (table_oid,))
            for pid, mode, granted, app, state, xact_seconds in cur.fetchall():
                holders.append(
                    {
                        "pid": pid,
                        "mode": PG_LOCKS_MODE.get(mode, mode),
                        "granted": granted,
                        "application_name": app,
                        "state": state,
                        "xact_seconds": xact_seconds,
                    }
                )
    except psycopg.Error:
        if catalog.conn.broken:
            raise
    scans, writes = _traffic(target_id, nspname, relname)
    return {
        "exists": True,
        "table": f"{nspname}.{relname}",
        "kind": RELKIND_LABELS.get(relkind, relkind),
        "total_bytes": total_bytes,
        "rows_estimate": rows_estimate,
        "holders": holders,
        "scans_per_second": scans,
        "writes_per_second": writes,
    }


def _overall(result: LintResult, statements: list[dict]) -> str:
    severities = [f["severity"] for f in result.findings]
    severities += [impact["severity"] for s in statements for impact in s["impacts"]]
    if result.parse_error:
        severities.append("critical")
    return max(severities, key=lambda s: SEVERITY_ORDER.get(s, 0), default="healthy")


def run_migration_lint(sql: str, target_id: uuid.UUID | None) -> dict:
    """Shared by the endpoint below and the MCP tool (app/mcp_tools.py)."""
    if target_id is None:
        result = lint(sql)
        statements = [s.as_dict() for s in result.statements]
        live = None
    else:
        try:
            with connect_to_target(target_id, autocommit=True) as conn:
                catalog = TargetCatalog(conn)
                result = lint(sql, catalog)
                statements = [s.as_dict() for s in result.statements]
                oldest_row = catalog.fetch_one(OLDEST_TRANSACTION_SQL, None)
                oldest = oldest_row[0] if oldest_row else None
                facts_cache: dict[tuple, dict] = {}
                for stmt, out in zip(result.statements, statements, strict=True):
                    for lock in stmt.locks:
                        key = (lock.schema, lock.name)
                        if key not in facts_cache:
                            facts_cache[key] = _relation_facts(catalog, target_id, lock.schema, lock.name)
                        out["impacts"].append(describe_impact(lock, stmt.effect, facts_cache[key], oldest))
                live = {
                    "target_id": target_id,
                    "server_version": catalog.server_version(),
                    "oldest_transaction_seconds": oldest,
                    "checked_at": datetime.now(timezone.utc),
                }
        except psycopg.Error as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc
    return {
        "overall_severity": _overall(result, statements),
        "statements": statements,
        "findings": result.findings,
        "parse_error": result.parse_error,
        "live": live,
    }


@router.post("/migration-lint", response_model=MigrationLintResponse)
def lint_migration(payload: MigrationLintRequest):
    return run_migration_lint(payload.sql, payload.target_id)
