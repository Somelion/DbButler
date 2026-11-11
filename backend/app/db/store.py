import threading
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from app.config import settings

# One pool for the whole process, created on first use rather than at import
# time, so importing app modules (the test suite, migrate.py) never needs a
# reachable store. check_connection pings a pooled connection before handing
# it out, so a pgdba-store restart costs one reconnect instead of an error.
_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    settings.store_database_url,
                    min_size=1,
                    max_size=settings.store_pool_max_size,
                    timeout=settings.store_pool_timeout_seconds,
                    check=ConnectionPool.check_connection,
                    name="pgdba-store",
                    open=True,
                )
    return _pool


@contextmanager
def store_conn():
    """A connection to pgdba-store — this app's own history/metrics database.

    Borrowed from the pool and given back on exit. The block behaves exactly
    like `with psycopg.connect(...) as conn`: committed on a clean exit,
    rolled back on an exception, and explicit conn.commit() calls inside it
    still work."""
    with _get_pool().connection() as conn:
        yield conn


def close_store_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
