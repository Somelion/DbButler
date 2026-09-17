import re

import psycopg

# The backend runs inside its own container — pointing it at "localhost" hits
# that container, not the host machine. A very common first-connection trip-up.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def test_connection(host: str, port: int, dbname: str, username: str, password: str, sslmode: str) -> dict:
    """Connects to a target Postgres and reports its version and uptime.

    Never raises — callers get {"ok": False, "message": ...} on any failure
    (bad credentials, unreachable host, timeout).
    """
    try:
        with psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=username,
            password=password,
            sslmode=sslmode,
            connect_timeout=5,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version(), "
                    "extract(epoch from (now() - pg_postmaster_start_time()))::bigint"
                )
                version_string, uptime_seconds = cur.fetchone()
    except Exception as exc:
        message = str(exc)
        if host.strip().lower() in LOOPBACK_HOSTS:
            message += (
                ' PostgreDba runs inside its own Docker container, so "localhost"/"127.0.0.1" '
                "points at that container, not your machine — try host.docker.internal instead "
                "if the database is on this machine."
            )
        return {"ok": False, "message": message}

    match = re.search(r"PostgreSQL (\S+)", version_string)
    pg_version = match.group(1) if match else version_string
    uptime_seconds = int(uptime_seconds)
    return {
        "ok": True,
        "pg_version": pg_version,
        "uptime_seconds": uptime_seconds,
        "uptime_human": _format_uptime(uptime_seconds),
        "message": None,
    }
