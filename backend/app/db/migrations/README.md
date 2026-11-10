# Schema migrations

`backend/app/db/schema.sql` is frozen as of introducing this mechanism (see the comment at its
end) — it never gets edited again. Every schema change from now on is a new file here, applied
once by `app/db/migrate.py` at backend startup and tracked in the `schema_migrations` table, so an
existing install picks it up automatically on its next restart instead of needing a manual
`ALTER TABLE` or a volume reset.

## Adding a migration

1. Pick the next integer after the highest version currently in this directory (start at `2` —
   version `1` is `schema.sql` itself, tracked as a baseline row rather than a file here).
2. Add `NNN_short_description.sql`, e.g. `002_add_finding_suppression.sql`.
3. Write plain SQL — whatever you'd normally hand-run against `pgdba-store`. It runs inside its own
   transaction, so a mid-file failure rolls back cleanly and the migration is retried (not marked
   applied) on the next backend startup.
4. Keep each file forward-only — no down-migrations. This app has no rollback tooling; if a
   migration turns out wrong, ship a new migration that corrects it.

## Why not Alembic

Consistent with this codebase's "no full ORM" choice (`docs/ARCHITECTURE.md`) — the schema is
simple enough that plain numbered SQL files plus one tracking table cover what's needed without
the extra dependency and generated-migration machinery.
