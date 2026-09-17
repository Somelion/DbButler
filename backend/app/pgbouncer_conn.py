import uuid

import psycopg

from app.crypto import decrypt
from app.db.store import store_conn


def get_pgbouncer_credentials(target_id: uuid.UUID):
    """Returns (host, port, username, encrypted_password, sslmode), or None
    if this target has no PgBouncer connection configured — a normal,
    common state (most targets don't run PgBouncer), unlike a missing
    target row itself (app/target_conn.py), which is always an error."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host, port, username, encrypted_password, sslmode FROM pgbouncer_connections WHERE target_id = %s",
            (target_id,),
        )
        return cur.fetchone()


def connect_to_pgbouncer(target_id: uuid.UUID) -> psycopg.Connection | None:
    """None means "not configured," not an error — callers (routers/
    dashboard.py) treat that as this target simply not running PgBouncer.
    dbname is always literally "pgbouncer", PgBouncer's magic admin
    database name. autocommit=True since PgBouncer's admin console doesn't
    support real transactions the way a normal Postgres backend does."""
    creds = get_pgbouncer_credentials(target_id)
    if creds is None:
        return None
    host, port, username, enc_password, sslmode = creds
    return psycopg.connect(
        host=host,
        port=port,
        dbname="pgbouncer",
        user=username,
        password=decrypt(enc_password),
        sslmode=sslmode,
        connect_timeout=5,
        autocommit=True,
    )
