"""Lightweight schema migration runner — not Alembic, deliberately:
consistent with this codebase's "no full ORM" choice (docs/ARCHITECTURE.md),
numbered .sql files under app/db/migrations/ are applied once each and
tracked in schema_migrations, run at backend startup (see main.py).

schema.sql (mounted as docker-entrypoint-initdb.d/01-schema.sql) still
bootstraps a brand-new, empty pgdba-store volume via Postgres's own init
hook, before this runner or even the backend process exists — and its last
statement inserts schema_migrations version 1 itself. An existing volume
from before this table existed never got that row (docker-entrypoint-initdb.d
only runs once, on an empty volume), so this runner inserts it on that
volume's first startup under the new code instead — same end state, reached
via whichever path applies. Either way, schema.sql itself is frozen from
here on: every schema change is a new numbered file in migrations/.
"""

import logging
import re
from pathlib import Path

import psycopg

from app.config import settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_PATTERN = re.compile(r"^(\d+)_.+\.sql$")

BASELINE_VERSION = 1
BASELINE_FILENAME = "schema.sql (pre-migration-system baseline)"


def _migration_files() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    files = []
    for path in MIGRATIONS_DIR.iterdir():
        match = _FILENAME_PATTERN.match(path.name)
        if match:
            files.append((int(match.group(1)), path))
    return sorted(files, key=lambda item: item[0])


def run_migrations() -> None:
    with psycopg.connect(settings.store_database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version     INTEGER PRIMARY KEY,
                filename    TEXT NOT NULL,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        cur.execute("SELECT count(*) FROM schema_migrations")
        (tracked_count,) = cur.fetchone()
        if tracked_count == 0:
            # A volume that predates this migration system — schema.sql
            # already created every table it manages, it just never
            # recorded that fact (a brand-new install already has this row,
            # inserted by schema.sql itself). Fires at most once per volume.
            logger.info("schema_migrations is empty — recording the existing schema as baseline (version 1).")
            cur.execute(
                "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                (BASELINE_VERSION, BASELINE_FILENAME),
            )

        cur.execute("SELECT version FROM schema_migrations")
        applied_versions = {row[0] for row in cur.fetchall()}

        for version, path in _migration_files():
            if version in applied_versions:
                continue
            logger.info("Applying migration %s...", path.name)
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, filename) VALUES (%s, %s)",
                    (version, path.name),
                )
            logger.info("Applied migration %s.", path.name)
