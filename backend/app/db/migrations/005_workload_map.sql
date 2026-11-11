-- Workload Map (Sprint 5): what each tracked query touches and joins, read
-- from its EXPLAIN (VERBOSE) plan by scheduler.py::_capture_query_plans
-- (app/workload_extract.py). Additive only: three new tables, nothing
-- existing changes, so the previous backend version runs unchanged against
-- an upgraded store (it simply never reads these).

-- One row per tracked query whose plan capture has been attempted: whether
-- it could be planned, and a hash of what was extracted. checked_at moves on
-- every capture; changed_at (and the child rows) only when the extraction
-- actually changed, so an unchanged query isn't rewritten every cycle.
-- status: 'ok' | 'needs_generic_plan' (parameterized query on PostgreSQL < 16)
--       | 'explain_failed' (utility statement, or text that no longer plans)
CREATE TABLE IF NOT EXISTS query_workload_extracts (
    tracked_query_id  UUID PRIMARY KEY REFERENCES tracked_queries(id) ON DELETE CASCADE,
    status            TEXT NOT NULL,
    error             TEXT,
    extract_hash      TEXT,
    extractor_version INTEGER NOT NULL,
    checked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS query_workload_extracts_checked_idx
    ON query_workload_extracts (checked_at);

-- Tables a query reads or writes, partitions and TimescaleDB chunks folded
-- onto their logical parent. access_kinds: seq | index | index_only |
-- bitmap | tid | insert | update | delete | merge | other. filter_columns:
-- this table's columns in the query's WHERE / index conditions.
CREATE TABLE IF NOT EXISTS query_relation_access (
    id               BIGSERIAL PRIMARY KEY,
    tracked_query_id UUID NOT NULL REFERENCES query_workload_extracts(tracked_query_id) ON DELETE CASCADE,
    schema_name      TEXT NOT NULL,
    table_name       TEXT NOT NULL,
    access_kinds     TEXT[] NOT NULL,
    filter_columns   TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS query_relation_access_query_idx
    ON query_relation_access (tracked_query_id);

-- Equality joins between two tables in one query. left/right are ordered
-- (schema, table) so the same pair always reads the same way round;
-- left_columns[i] = right_columns[i]. join_type: Inner | Left | Right |
-- Full | Semi | Anti; join_strategy: Hash Join | Merge Join | Nested Loop
-- (NULL when the clause sits outside a join node, e.g. in a subplan).
CREATE TABLE IF NOT EXISTS query_join_edges (
    id               BIGSERIAL PRIMARY KEY,
    tracked_query_id UUID NOT NULL REFERENCES query_workload_extracts(tracked_query_id) ON DELETE CASCADE,
    left_schema      TEXT NOT NULL,
    left_table       TEXT NOT NULL,
    right_schema     TEXT NOT NULL,
    right_table      TEXT NOT NULL,
    left_columns     TEXT[] NOT NULL,
    right_columns    TEXT[] NOT NULL,
    join_type        TEXT,
    join_strategy    TEXT
);

CREATE INDEX IF NOT EXISTS query_join_edges_query_idx
    ON query_join_edges (tracked_query_id);
