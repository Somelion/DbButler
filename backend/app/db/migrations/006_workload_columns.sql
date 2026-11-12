-- Refactoring Advisor (Sprint 6): which columns each tracked query reads
-- from a table (EXPLAIN VERBOSE "Output" of its scans), for the cold-column
-- and wide-row rules. Additive: a new column with a default, so the previous
-- backend keeps inserting rows without it. Existing rows read as "nothing
-- referenced" until the next plan capture re-extracts them
-- (app/workload_extract.py::EXTRACTOR_VERSION 2 changes every hash).
ALTER TABLE query_relation_access ADD COLUMN IF NOT EXISTS referenced_columns TEXT[] NOT NULL DEFAULT '{}';
