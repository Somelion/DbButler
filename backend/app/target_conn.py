import logging
import uuid

import psycopg
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from app.crypto import decrypt
from app.db.store import store_conn

logger = logging.getLogger(__name__)

# Every target session identifies itself, so DbButler's own queries are
# recognisable in pg_stat_activity (and excluded from its own connection
# counts — see routers/dashboard.py and routers/activity.py).
APPLICATION_NAME = "dbbutler"

# Session limits applied to every target connection at startup, so a check
# query can never hang behind a lock or run unbounded on someone's
# production server. lock_timeout matters most: a read of a table that a
# migration holds ACCESS EXCLUSIVE on would otherwise queue — and everything
# that arrives after it queues behind *it*.
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
DEFAULT_LOCK_TIMEOUT_MS = 5_000
IDLE_IN_TRANSACTION_TIMEOUT_MS = 60_000
# A user-requested EXPLAIN ANALYZE really executes the query — capped below
# nginx's 60s proxy_read_timeout (ui/nginx.conf) so the user sees
# PostgreSQL's own "canceling statement due to statement timeout" rather
# than a bare gateway timeout while the query keeps running.
EXPLAIN_ANALYZE_STATEMENT_TIMEOUT_MS = 50_000
# Explicit, user-triggered maintenance (VACUUM, ANALYZE, CREATE/DROP INDEX
# CONCURRENTLY) legitimately runs for minutes on a big table.
MAINTENANCE_STATEMENT_TIMEOUT_MS = 30 * 60_000


def _session_options(statement_timeout_ms: int, lock_timeout_ms: int) -> str:
    return (
        f"-c statement_timeout={statement_timeout_ms} "
        f"-c lock_timeout={lock_timeout_ms} "
        f"-c idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_TIMEOUT_MS}"
    )


def open_target_connection(
    host: str,
    port: int,
    dbname: str,
    username: str,
    password: str,
    sslmode: str,
    *,
    autocommit: bool = False,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> psycopg.Connection:
    """The one place a target connection is opened — shared by request
    handlers (connect_to_target below), the scheduler, and the Connections
    screen's test probe, so all of them get the same session limits.

    The limits travel as the `options` startup parameter rather than SET
    statements, so they hold for the whole session without an extra round
    trip. PgBouncer can refuse `options` ("unsupported startup parameter"),
    so a connection refused for exactly that reason is retried without it —
    a target behind such a pooler still works, just without these
    server-side limits (logged once per connect).
    0 for either timeout means "no limit"."""
    base_kwargs = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": username,
        "password": password,
        "sslmode": sslmode,
        "connect_timeout": 5,
        "application_name": APPLICATION_NAME,
        "autocommit": autocommit,
    }
    try:
        return psycopg.connect(**base_kwargs, options=_session_options(statement_timeout_ms, lock_timeout_ms))
    except psycopg.OperationalError as exc:
        if "unsupported startup parameter" not in str(exc):
            raise
        logger.warning(
            "target %s:%s rejected the 'options' startup parameter (likely a PgBouncer in front "
            "of it); connecting without statement/lock timeouts",
            host,
            port,
        )
        return psycopg.connect(**base_kwargs)


def get_target_credentials(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host, port, dbname, username, encrypted_password, sslmode FROM targets WHERE id = %s",
            (target_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return row


def connect_to_target(
    target_id: uuid.UUID,
    autocommit: bool = False,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
) -> psycopg.Connection:
    # VACUUM refuses to run inside a transaction block, so maintenance
    # actions need autocommit=True; every other caller keeps the default.
    host, port, dbname, username, enc_password, sslmode = get_target_credentials(target_id)
    try:
        password = decrypt(enc_password)
    except InvalidToken as exc:
        # Most common cause: SECRET_KEY changed since this password was
        # encrypted — Fernet keys can't be rotated after the fact, so the
        # fix is re-entering the password (Connections screen), not
        # anything this app can do automatically. Not a psycopg.Error, so
        # every router's existing `except psycopg.Error` leaves this
        # HTTPException to propagate as-is rather than a raw 500.
        raise HTTPException(
            status_code=409,
            detail="This connection's stored password can no longer be decrypted (the server's "
            "encryption key changed since it was saved). Update the password for this connection "
            "on the Connections screen to fix it.",
        ) from exc
    return open_target_connection(
        host,
        port,
        dbname,
        username,
        password,
        sslmode,
        autocommit=autocommit,
        statement_timeout_ms=statement_timeout_ms,
        lock_timeout_ms=lock_timeout_ms,
    )
