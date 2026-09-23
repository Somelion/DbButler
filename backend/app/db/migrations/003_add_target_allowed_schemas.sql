-- Optional per-connection schema allowlist (PROBLEMS.md #9/#10). NULL or
-- empty means "no filter" — every schema the target has is considered,
-- same as before this migration. When set, every query across the app that
-- lists schemas/tables for this target only considers the ones named here.
ALTER TABLE targets ADD COLUMN allowed_schemas TEXT[];
