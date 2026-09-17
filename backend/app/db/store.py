from contextlib import contextmanager

import psycopg

from app.config import settings


@contextmanager
def store_conn():
    """A connection to pgdba-store — this app's own history/metrics database."""
    with psycopg.connect(settings.store_database_url) as conn:
        yield conn
