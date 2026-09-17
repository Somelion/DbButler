"""Connectivity test for PgBouncer's admin console — a distinct check from
pg_probe.py's test_connection, since PgBouncer's admin database only
understands its own SHOW/SET commands, not arbitrary SQL (SELECT version()
fails there the way it wouldn't against a real Postgres database).
"""

import psycopg

from app.pg_probe import LOOPBACK_HOSTS


def test_pgbouncer_connection(host: str, port: int, username: str, password: str, sslmode: str) -> dict:
    """Connects to PgBouncer's admin console (dbname is always literally
    "pgbouncer") and reports its version. Never raises — callers get
    {"ok": False, "message": ...} on any failure. The connecting role only
    needs PgBouncer's stats_users membership, not admin_users — this app
    only ever runs SHOW POOLS, never PAUSE/KILL/RELOAD/RECONNECT."""
    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname="pgbouncer",
            user=username,
            password=password,
            sslmode=sslmode,
            connect_timeout=5,
            autocommit=True,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW VERSION")
                row = cur.fetchone()
                version_string = row[0] if row else "unknown"
    except Exception as exc:
        message = str(exc)
        if host.strip().lower() in LOOPBACK_HOSTS:
            message += (
                ' PostgreDba runs inside its own Docker container, so "localhost"/"127.0.0.1" '
                "points at that container, not your machine — try host.docker.internal instead "
                "if PgBouncer is on this machine."
            )
        return {"ok": False, "message": message}

    return {"ok": True, "version": version_string, "message": None}
