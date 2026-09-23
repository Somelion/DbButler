"""Schema/anti-pattern linters, per databank/02 - Concepts/PostgreSQL Bad
Practices.md's "Schema Design" and "Query" anti-pattern sections, and
databank/02 - Concepts/Table Partitioning.md.

The query-pattern checks read already-executed query text from
pg_stat_statements. That text has literal constants normalized to $N
placeholders, so an OFFSET check can detect the *pattern* (LIMIT/OFFSET
pagination) but not how deep a specific page went — the pattern itself is
the risk regardless of how deep any one call happened to be.
"""

import re

MONEY_NAME_PATTERN = re.compile(
    r"(price|amount|cost|total|balance|salary|fee|payment|revenue|charge)", re.IGNORECASE
)
BLOB_NAME_PATTERN = re.compile(
    r"(image|photo|picture|file|document|attachment|blob|video|pdf)", re.IGNORECASE
)
RANDOM_UUID_DEFAULT_PATTERN = re.compile(r"gen_random_uuid|uuid_generate_v4", re.IGNORECASE)
FLOAT_TYPES = {"real", "double precision"}

SELECT_STAR_PATTERN = re.compile(r"select\s+\*\s+from", re.IGNORECASE)
NOT_IN_SUBQUERY_PATTERN = re.compile(r"not\s+in\s*\(\s*select", re.IGNORECASE)
OFFSET_PAGINATION_PATTERN = re.compile(r"\blimit\b.*\boffset\b|\boffset\b.*\blimit\b", re.IGNORECASE | re.DOTALL)
FUNCTION_WRAP_WHERE_PATTERN = re.compile(
    r"\b(date|lower|upper|extract|trim)\s*\([a-zA-Z_][a-zA-Z0-9_.]*\)\s*(=|<>|!=|<=|>=|<|>|like)",
    re.IGNORECASE,
)

# A single-key-equality lookup with no JOIN and no IN(...) batching — the
# shape of "fetch one related row" rather than "fetch a page" or "fetch a
# batch." pg_stat_statements normalizes every literal to $N, so this matches
# the pattern itself (WHERE col = $1), not any specific value.
N_PLUS_ONE_SHAPE_PATTERN = re.compile(r"\bwhere\s+[\w.\"]+\s*=\s*\$1\b", re.IGNORECASE)
N_PLUS_ONE_JOIN_PATTERN = re.compile(r"\bjoin\b", re.IGNORECASE)
N_PLUS_ONE_BATCH_PATTERN = re.compile(r"\bin\s*\(", re.IGNORECASE)

# Called this often, a single-row lookup is either a legitimate hot path or
# an application fetching related rows one at a time in a loop instead of
# batching them — same order of magnitude as Index Advisor's
# low-cardinality-index threshold (10,000 rows) for "clearly not a rare
# code path."
N_PLUS_ONE_MIN_CALLS = 10_000
# Average rows returned per call has to stay close to exactly one — a query
# matching the WHERE-equality shape but usually returning many rows (e.g.
# WHERE status = $1) isn't a per-row lookup, whatever its shape looks like.
N_PLUS_ONE_MAX_AVG_ROWS = 2.0

# A table past either threshold is a partitioning candidate per the databank's
# own framing ("hundreds of millions of rows... without partitioning").
# Sized well below "hundreds of millions" so it's still a useful signal on
# smaller real-world databases, not just the largest ones.
PARTITION_SIZE_BYTES_THRESHOLD = 5 * 1024**3  # 5GB
PARTITION_ROW_THRESHOLD = 10_000_000

# Widening a column/sequence to bigint before it's actually needed is free;
# waiting until nextval() starts failing outright is an outage. Attention
# well before critical so there's time to plan the migration.
SEQUENCE_EXHAUSTION_ATTENTION_PCT = 75
SEQUENCE_EXHAUSTION_CRITICAL_PCT = 90

# A query's sort/hash spilling even a modest number of temp blocks to disk
# is a real signal — this isn't sized to catch only extreme cases the way
# schema_lint's other numeric thresholds (partitioning) are.
DISK_SPILL_MIN_TEMP_BLOCKS = 1_000

NAIVE_TIMESTAMP_NAME_PATTERN = re.compile(r"(_at|_date|_time|timestamp)$", re.IGNORECASE)

# A table where at least half its columns are JSONB (and there are at least
# two of them, to skip a table with one legitimately-flexible column) reads
# as JSONB substituting for schema design rather than genuine flexibility.
JSONB_OVERUSE_MIN_COLUMNS = 2
JSONB_OVERUSE_MIN_SHARE = 0.5

BOOLEAN_NAME_PATTERN = re.compile(r"^(is_|has_)|_flag$", re.IGNORECASE)
BOOLEAN_AS_INT_TYPES = {"smallint", "integer"}


def _normalize_whitespace(text: str) -> str:
    """Collapses whitespace without cutting the text short — used for
    occurrence query text, which the UI truncates visually (CSS ellipsis)
    and reveals in full on hover, the same pattern Query Intelligence uses."""
    return " ".join(text.split())


def _format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def find_bad_data_type_findings(column_rows: list[tuple]) -> list[dict]:
    """column_rows: (schema, table, column, data_type, char_max_length,
    column_default, is_identity)."""
    findings = []
    for schema, table, column, data_type, char_max_length, column_default, is_identity in column_rows:
        full_name = f"{schema}.{table}"

        if data_type in FLOAT_TYPES and MONEY_NAME_PATTERN.search(column):
            findings.append(
                {
                    "id": f"float-money-{schema}-{table}-{column}",
                    "category": "schema lint",
                    "severity": "attention",
                    "title": f"{full_name}.{column} stores money as {data_type}",
                    "summary": (
                        "Floating-point arithmetic is imprecise — 0.1 + 0.2 doesn't equal 0.3 in a "
                        "float. That's a real risk for a monetary value."
                    ),
                    "detail": f"column={column} type={data_type}",
                    "suggested_action": f"Change {column} to numeric(12,2) (or the precision your currency needs).",
                    "recommended_ddl": f"ALTER TABLE {full_name} ALTER COLUMN {column} TYPE numeric(12,2);",
                    "schema_name": schema,
                    "table_name": table,
                }
            )

        if data_type == "character varying" and char_max_length == 255:
            findings.append(
                {
                    "id": f"varchar255-{schema}-{table}-{column}",
                    "category": "schema lint",
                    "severity": "unknown",
                    "title": f"{full_name}.{column} is varchar(255)",
                    "summary": (
                        "255 is a MySQL-era default with no special meaning in PostgreSQL — text and "
                        "varchar perform identically here. Worth picking a length that means something, "
                        "or using text."
                    ),
                    "detail": f"column={column} type=varchar(255)",
                    "suggested_action": "Use text, or a varchar(N) where N reflects a real constraint.",
                    "recommended_ddl": None,
                    "schema_name": schema,
                    "table_name": table,
                }
            )

        if data_type == "character" and char_max_length:
            findings.append(
                {
                    "id": f"char-type-{schema}-{table}-{column}",
                    "category": "schema lint",
                    "severity": "unknown",
                    "title": f"{full_name}.{column} is char({char_max_length})",
                    "summary": (
                        "char(n) space-pads every value out to its full length and offers no real "
                        "advantage over text/varchar in PostgreSQL — it's rarely the right choice."
                    ),
                    "detail": f"column={column} type=char({char_max_length})",
                    "suggested_action": "Use text, or varchar(n) if you genuinely need a length constraint.",
                    "recommended_ddl": f"ALTER TABLE {full_name} ALTER COLUMN {column} TYPE text;",
                    "schema_name": schema,
                    "table_name": table,
                }
            )

        if column_default and column_default.startswith("nextval(") and is_identity != "YES":
            findings.append(
                {
                    "id": f"legacy-serial-{schema}-{table}-{column}",
                    "category": "schema lint",
                    "severity": "unknown",
                    "title": f"{full_name}.{column} uses serial instead of identity",
                    "summary": (
                        "serial is sugar for a sequence plus a default, with a subtle gotcha: the "
                        "sequence can be dropped or reassigned independently of the column it backs. "
                        "GENERATED ALWAYS AS IDENTITY (PostgreSQL 10+) ties the two together properly."
                    ),
                    "detail": f"column={column} default={column_default}",
                    "suggested_action": (
                        "Use GENERATED ALWAYS AS IDENTITY for new tables. Converting an existing serial "
                        "column takes a few careful steps (drop the old default, add identity, migrate "
                        "the sequence) — not a one-line fix, so no DDL is suggested here."
                    ),
                    "recommended_ddl": None,
                    "schema_name": schema,
                    "table_name": table,
                }
            )

        if data_type == "bytea" and BLOB_NAME_PATTERN.search(column):
            findings.append(
                {
                    "id": f"blob-in-db-{schema}-{table}-{column}",
                    "category": "schema lint",
                    "severity": "attention",
                    "title": f"{full_name}.{column} stores binary files in the database",
                    "summary": (
                        "Large binary objects (images, PDFs, videos) in a bytea column inflate backup "
                        "size and slow vacuuming, with no real advantage over file/object storage for "
                        "static assets."
                    ),
                    "detail": f"column={column} type=bytea",
                    "suggested_action": (
                        "Store the file in object storage (S3, MinIO, local filesystem) and keep only "
                        "a reference or URL in the database."
                    ),
                    "recommended_ddl": None,
                    "schema_name": schema,
                    "table_name": table,
                }
            )

    return findings


def find_partitioning_findings(table_rows: list[tuple]) -> list[dict]:
    """table_rows: (schema, table, live_tup, total_bytes) for tables not
    already part of any partitioning scheme — see PARTITION_CANDIDATES_QUERY
    in routers/schema_lint.py, which excludes partitioned parents and their
    partition children before this function ever sees a row."""
    findings = []
    for schema, table, live_tup, total_bytes in table_rows:
        live_tup = live_tup or 0
        total_bytes = total_bytes or 0
        if total_bytes < PARTITION_SIZE_BYTES_THRESHOLD and live_tup < PARTITION_ROW_THRESHOLD:
            continue

        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"missing-partitioning-{schema}-{table}",
                "category": "schema lint",
                "severity": "attention",
                "title": f"{full_name} is large and not partitioned",
                "summary": (
                    f"This table has {live_tup:,} rows ({_format_bytes(total_bytes)}) and isn't "
                    "partitioned. At this size, partitioning (typically by date range) can make bulk "
                    "deletes instant — DROP a partition instead of DELETE — and let queries that "
                    "filter on the partition key skip most of the table entirely."
                ),
                "detail": f"live_tup={live_tup} total_bytes={total_bytes}",
                "suggested_action": (
                    "Consider range partitioning if this table has a natural key (like created_at) "
                    "that most queries and deletes already filter on."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_uuid_fragmentation_findings(rows: list[tuple]) -> list[dict]:
    """rows: (table_name, column_name, column_default, live_tup, total_bytes)
    — single-column uuid primary keys, from UUID_PK_QUERY in
    routers/schema_lint.py. Only flags tables past the same size threshold
    used for the partitioning check, and only random (v4-style) defaults —
    a sequential/time-ordered UUID default wouldn't have this problem."""
    findings = []
    for table_name, column_name, column_default, live_tup, total_bytes in rows:
        live_tup = live_tup or 0
        total_bytes = total_bytes or 0
        if not column_default or not RANDOM_UUID_DEFAULT_PATTERN.search(column_default):
            continue
        if total_bytes < PARTITION_SIZE_BYTES_THRESHOLD and live_tup < PARTITION_ROW_THRESHOLD:
            continue

        findings.append(
            {
                "id": f"uuid-fragmentation-{table_name}-{column_name}",
                "category": "schema lint",
                "severity": "attention",
                "title": f"{table_name}.{column_name} is a random UUID primary key",
                "summary": (
                    f"With {live_tup:,} rows ({_format_bytes(total_bytes)}), random UUID inserts "
                    "(v4-style) scatter writes across the whole primary-key index instead of "
                    "appending at the end, fragmenting it and hurting insert/vacuum performance "
                    "over time."
                ),
                "detail": f"column={column_name} default={column_default}",
                "suggested_action": (
                    "Consider a time-ordered key instead — UUIDv7 (sortable, still globally unique) "
                    "or bigserial/identity if a plain sequence is acceptable."
                ),
                "recommended_ddl": None,
                # table_name is a ::regclass::text cast — schema-qualified only
                # when the schema isn't on search_path, so this is a
                # best-effort split, same caveat as index_analysis.py's.
                "schema_name": table_name.rsplit(".", 1)[0] if "." in table_name else None,
                "table_name": table_name.rsplit(".", 1)[-1],
            }
        )
    return findings


def _occurrence_count_phrase(occurrences: list[dict]) -> str:
    count = len(occurrences)
    return f"{count:,} matching quer{'y' if count == 1 else 'ies'} — see below."


def find_query_pattern_findings(rows: list[tuple]) -> list[dict]:
    """rows: (query, calls) from pg_stat_statements — see RECENT_QUERIES_QUERY
    in routers/schema_lint.py. One finding per pattern (not per query) — each
    carries every matching query as `occurrences` so the UI can expand it
    into the actual offending queries instead of naming only the first
    match, without flooding the section with one card per query."""
    select_star_hits = []
    not_in_subquery_hits = []
    deep_offset_hits = []
    function_wrap_hits = []

    for query, calls in rows:
        occurrence = {"query": _normalize_whitespace(query), "calls": calls}
        if SELECT_STAR_PATTERN.search(query):
            select_star_hits.append(occurrence)
        if NOT_IN_SUBQUERY_PATTERN.search(query):
            not_in_subquery_hits.append(occurrence)
        if OFFSET_PAGINATION_PATTERN.search(query):
            deep_offset_hits.append(occurrence)
        if FUNCTION_WRAP_WHERE_PATTERN.search(query):
            function_wrap_hits.append(occurrence)

    findings = []

    if select_star_hits:
        findings.append(
            {
                "id": "select-star",
                "category": "schema lint",
                "severity": "unknown",
                "title": "A query uses SELECT *",
                "summary": (
                    "Fetching every column costs bandwidth, blocks index-only scans, and silently "
                    "changes behavior when columns are added or reordered."
                ),
                "detail": _occurrence_count_phrase(select_star_hits),
                "suggested_action": "List only the columns the query actually needs.",
                "recommended_ddl": None,
                "occurrences": select_star_hits,
            }
        )

    if not_in_subquery_hits:
        findings.append(
            {
                "id": "not-in-subquery",
                "category": "schema lint",
                "severity": "attention",
                "title": "A query uses NOT IN with a subquery",
                "summary": (
                    "If the subquery ever returns a NULL, the whole NOT IN comparison returns no "
                    "rows at all — a landmine from three-valued logic, not a bug in your data."
                ),
                "detail": _occurrence_count_phrase(not_in_subquery_hits),
                "suggested_action": "Rewrite as NOT EXISTS (...), which doesn't have this trap.",
                "recommended_ddl": None,
                "occurrences": not_in_subquery_hits,
            }
        )

    if deep_offset_hits:
        findings.append(
            {
                "id": "deep-offset-pagination",
                "category": "schema lint",
                "severity": "attention",
                "title": "A query paginates with LIMIT/OFFSET",
                "summary": (
                    "OFFSET makes PostgreSQL read and discard every row before it — page 1000 reads "
                    "and throws away 20,000 rows just to return 20. It gets slower with every page."
                ),
                "detail": _occurrence_count_phrase(deep_offset_hits),
                "suggested_action": (
                    "Switch to keyset pagination: WHERE (created_at, id) < (:last_seen) "
                    "ORDER BY ... LIMIT N."
                ),
                "recommended_ddl": None,
                "occurrences": deep_offset_hits,
            }
        )

    if function_wrap_hits:
        findings.append(
            {
                "id": "function-wrapped-column",
                "category": "schema lint",
                "severity": "attention",
                "title": "A query wraps a column in a function inside WHERE",
                "summary": (
                    "Applying DATE(), LOWER(), UPPER(), EXTRACT(), or TRIM() to a column before "
                    "comparing it stops PostgreSQL from using a plain index on that column — it "
                    "has to check every row by hand."
                ),
                "detail": _occurrence_count_phrase(function_wrap_hits),
                "suggested_action": (
                    "Rewrite the condition to compare the column directly (e.g. a range instead of "
                    "DATE(col) = ...), or add an expression index that matches the function call."
                ),
                "recommended_ddl": None,
                "occurrences": function_wrap_hits,
            }
        )

    return findings


def find_disk_spill_findings(rows: list[tuple]) -> list[dict]:
    """rows: (query, calls, temp_blks_written) from pg_stat_statements — see
    DISK_SPILL_QUERY in routers/schema_lint.py. A nonzero temp_blks_written
    means a sort or hash operation outgrew work_mem and spilled to disk,
    which is orders of magnitude slower than staying in memory — a distinct,
    numeric signal from the regex-based query-pattern checks above. One
    finding total, carrying every offending query as `occurrences` (with its
    temp-block count folded into each occurrence's note), same grouping
    approach as find_query_pattern_findings."""
    hits = []
    for query, calls, temp_blks_written in rows:
        temp_blks_written = temp_blks_written or 0
        if temp_blks_written < DISK_SPILL_MIN_TEMP_BLOCKS:
            continue
        hits.append(
            {
                "query": _normalize_whitespace(query),
                "calls": calls,
                "note": f"{temp_blks_written:,} temp blocks written",
            }
        )

    if not hits:
        return []

    return [
        {
            "id": "disk-spill",
            "category": "schema lint",
            "severity": "attention",
            "title": "A query is spilling a sort or hash to disk",
            "summary": (
                "One or more queries have written temporary blocks to disk — their sort or hash step "
                "outgrew work_mem and had to use disk, which is far slower than staying in memory."
            ),
            "detail": _occurrence_count_phrase(hits),
            "suggested_action": (
                "Raise work_mem for this session/role, or add an index/rewrite the query to avoid "
                "the sort or hash entirely."
            ),
            "recommended_ddl": None,
            "occurrences": hits,
        }
    ]


def find_n_plus_one_signature_findings(rows: list[tuple]) -> list[dict]:
    """rows: (query, calls, total_rows) from pg_stat_statements — see
    N_PLUS_ONE_QUERY in routers/schema_lint.py. This can't see request or
    transaction boundaries (pg_stat_statements has none), so it can't prove
    an application loop caused any of this — it flags the *shape* consistent
    with one: a single-key equality lookup, no JOIN, no IN(...) batching,
    averaging about one row per call, called often enough that "occasional
    ad hoc lookup" stops being a plausible explanation."""
    hits = []
    for query, calls, total_rows in rows:
        if calls < N_PLUS_ONE_MIN_CALLS:
            continue
        if not N_PLUS_ONE_SHAPE_PATTERN.search(query):
            continue
        if N_PLUS_ONE_JOIN_PATTERN.search(query) or N_PLUS_ONE_BATCH_PATTERN.search(query):
            continue
        avg_rows = (total_rows or 0) / calls
        if avg_rows > N_PLUS_ONE_MAX_AVG_ROWS:
            continue
        hits.append(
            {
                "query": _normalize_whitespace(query),
                "calls": calls,
                "note": f"~{avg_rows:.1f} row(s)/call",
            }
        )

    if not hits:
        return []

    return [
        {
            "id": "n-plus-one-signature",
            "category": "schema lint",
            "severity": "attention",
            "title": "A single-row lookup is called at N+1-loop scale",
            "summary": (
                "One or more queries fetch a single row by key, with no JOIN or batching, called "
                "tens of thousands of times each averaging about one row back — the shape of an "
                "application fetching related rows one at a time in a loop instead of a single "
                "batched query (e.g. WHERE id = ANY($1) or a JOIN)."
            ),
            "detail": _occurrence_count_phrase(hits),
            "suggested_action": (
                "If this comes from an ORM loading a relation per parent row, batch it (eager-load, "
                "or rewrite the lookup as one query with WHERE id = ANY($1))."
            ),
            "recommended_ddl": None,
            "occurrences": hits,
        }
    ]


def find_missing_primary_key_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, live_tup, total_bytes) for tables with neither
    a primary key nor a unique constraint — see MISSING_PRIMARY_KEY_QUERY in
    routers/schema_lint.py. A table with no reliable row identity blocks
    logical replication and ON CONFLICT upserts, and gives application code
    no natural handle for "this exact row.\""""
    findings = []
    for schema, table, live_tup, total_bytes in rows:
        live_tup = live_tup or 0
        total_bytes = total_bytes or 0
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"missing-primary-key-{schema}-{table}",
                "category": "schema lint",
                "severity": "attention",
                "title": f"{full_name} has no primary key",
                "summary": (
                    f"This table ({live_tup:,} rows) has neither a primary key nor a unique "
                    "constraint. That blocks logical replication, ON CONFLICT upserts, and leaves "
                    "application code with no reliable way to reference one exact row."
                ),
                "detail": f"live_tup={live_tup} total_bytes={total_bytes}",
                "suggested_action": (
                    "Add a primary key — a surrogate identity column if there's no natural unique key "
                    "already."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_sequence_exhaustion_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, column, seq_name, data_type, last_value,
    max_value) for sequence-backed columns — see SEQUENCE_EXHAUSTION_QUERY in
    routers/schema_lint.py. serial/int4 (and their identity equivalents) cap
    at ~2.1 billion; once the backing sequence nears that ceiling, every
    insert starts failing outright — a well-known production outage pattern
    that can hit even a small table under high insert+delete churn."""
    findings = []
    for schema, table, column, seq_name, data_type, last_value, max_value in rows:
        if last_value is None or not max_value:
            continue
        pct_used = 100.0 * last_value / max_value
        if pct_used < SEQUENCE_EXHAUSTION_ATTENTION_PCT:
            continue
        severity = "critical" if pct_used >= SEQUENCE_EXHAUSTION_CRITICAL_PCT else "attention"
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"sequence-exhaustion-{schema}-{seq_name}",
                "category": "schema lint",
                "severity": severity,
                "title": f"{full_name}.{column} is approaching its sequence limit",
                "summary": (
                    f"{seq_name} ({data_type}) has used {pct_used:.0f}% of its range. Once it's "
                    "exhausted, every insert into this table fails outright until the column is "
                    "widened."
                ),
                "detail": f"column={column} data_type={data_type} last_value={last_value} max_value={max_value}",
                "suggested_action": (
                    "Widen the column and its sequence to bigint before this becomes an outage: "
                    "ALTER TABLE ... ALTER COLUMN ... TYPE bigint, then ALTER SEQUENCE ... AS bigint."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_naive_timestamp_findings(column_rows: list[tuple]) -> list[dict]:
    """Reuses the same column_rows shape as find_bad_data_type_findings —
    (schema, table, column, data_type, char_max_length, column_default,
    is_identity). timestamp without time zone silently discards timezone
    information and breaks the moment a deployment spans more than one
    timezone or observes DST; scoped to columns whose name suggests a real
    point in time, to cut noise from columns that are naive on purpose."""
    findings = []
    for schema, table, column, data_type, _char_max_length, _column_default, _is_identity in column_rows:
        if data_type != "timestamp without time zone" or not NAIVE_TIMESTAMP_NAME_PATTERN.search(column):
            continue
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"naive-timestamp-{schema}-{table}-{column}",
                "category": "schema lint",
                "severity": "unknown",
                "title": f"{full_name}.{column} is timestamp without time zone",
                "summary": (
                    "A naive timestamp silently discards timezone information. That's fine only if "
                    "every writer and reader agrees on one timezone forever — a multi-region "
                    "deployment or a single DST transition is enough to make values ambiguous."
                ),
                "detail": f"column={column} type=timestamp without time zone",
                "suggested_action": (
                    "Use timestamptz instead, unless this genuinely represents a timezone-independent "
                    'wall-clock value (e.g. "9:00 daily" business rules).'
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_jsonb_overuse_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, jsonb_column_count, total_column_count) — see
    JSONB_OVERUSE_QUERY in routers/schema_lint.py. JSONB is a good fit for
    genuinely flexible, sparse data, but a table where most of its columns
    are JSONB has usually substituted it for actual schema design, giving up
    type enforcement, constraints, and straightforward joins in the process."""
    findings = []
    for schema, table, jsonb_column_count, total_column_count in rows:
        if not total_column_count or jsonb_column_count < JSONB_OVERUSE_MIN_COLUMNS:
            continue
        share = jsonb_column_count / total_column_count
        if share < JSONB_OVERUSE_MIN_SHARE:
            continue
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"jsonb-overuse-{schema}-{table}",
                "category": "schema lint",
                "severity": "unknown",
                "title": f"{full_name} stores most of its data in JSONB columns",
                "summary": (
                    f"{jsonb_column_count} of {total_column_count} columns on this table are JSONB. "
                    "That's a reasonable fit for genuinely flexible or sparse data, but often signals "
                    "JSONB substituting for real schema design — giving up type enforcement, "
                    "constraints, and straightforward joins."
                ),
                "detail": f"jsonb_column_count={jsonb_column_count} total_column_count={total_column_count}",
                "suggested_action": (
                    "If some of these fields are consistently present and typed, consider pulling them "
                    "out into real columns."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_boolean_as_int_findings(column_rows: list[tuple]) -> list[dict]:
    """Reuses the same column_rows shape as find_bad_data_type_findings. A
    column named like a boolean (is_/has_/..._flag) but typed as an integer
    usually means 0/1 is standing in for true/false — native boolean is the
    same storage cost and self-documenting besides."""
    findings = []
    for schema, table, column, data_type, _char_max_length, _column_default, _is_identity in column_rows:
        if data_type not in BOOLEAN_AS_INT_TYPES or not BOOLEAN_NAME_PATTERN.search(column):
            continue
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"boolean-as-int-{schema}-{table}-{column}",
                "category": "schema lint",
                "severity": "unknown",
                "title": f"{full_name}.{column} looks like a boolean stored as {data_type}",
                "summary": (
                    "A column named like a yes/no flag but typed as an integer usually means 0/1 is "
                    "standing in for true/false. Native boolean costs the same and is self-documenting."
                ),
                "detail": f"column={column} type={data_type}",
                "suggested_action": (
                    "Confirm the column only ever holds 0/1, then convert: ALTER TABLE ... ALTER "
                    "COLUMN ... TYPE boolean USING column::boolean."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_fk_type_mismatch_findings(rows: list[tuple]) -> list[dict]:
    """rows: (constraint_name, table_name, column_name, column_type,
    ref_table, ref_column, ref_type) — see FK_TYPE_MISMATCH_QUERY in
    routers/schema_lint.py. A foreign key whose column type doesn't match
    the column it references forces an implicit cast on every join and FK
    check, which silently defeats index use on that column — the same class
    of problem as PostgreSQL's general implicit-type-casting anti-pattern,
    applied specifically to FK pairs."""
    findings = []
    for constraint_name, table_name, column_name, column_type, ref_table, ref_column, ref_type in rows:
        if column_type == ref_type:
            continue
        findings.append(
            {
                "id": f"fk-type-mismatch-{table_name}-{constraint_name}",
                "category": "schema lint",
                "severity": "attention",
                "title": f"{table_name}.{column_name} doesn't match its referenced column's type",
                "summary": (
                    f"{table_name}.{column_name} is {column_type}, but it references "
                    f"{ref_table}.{ref_column} ({ref_type}). The mismatch forces an implicit cast on "
                    "every join and FK check, which silently defeats index use on that column."
                ),
                "detail": f"constraint={constraint_name} column_type={column_type} ref_type={ref_type}",
                "suggested_action": f"Align {column_name}'s type with {ref_table}.{ref_column}.",
                "recommended_ddl": None,
                "schema_name": table_name.rsplit(".", 1)[0] if "." in table_name else None,
                "table_name": table_name.rsplit(".", 1)[-1],
            }
        )
    return findings


def find_unlogged_table_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table) for ordinary tables with relpersistence='u' —
    see UNLOGGED_TABLES_QUERY in routers/schema_lint.py. Not wrong by
    itself (a deliberate, reasonable choice for session/cache tables that
    don't need crash-safety), but every row in an unlogged table is wiped
    on an unclean shutdown or crash — worth a nudge to confirm that's
    understood rather than an overlooked `UNLOGGED` left over from a
    scratch table."""
    findings = []
    for schema, table in rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"unlogged-table-{schema}-{table}",
                "category": "schema lint",
                "severity": "unknown",
                "title": f"{full_name} is UNLOGGED",
                "summary": (
                    f"{full_name} skips WAL logging — faster writes, but every row is wiped on an "
                    "unclean shutdown or crash, and it isn't replicated to any standby."
                ),
                "detail": f"schema={schema} table={table} relpersistence=u",
                "suggested_action": (
                    "Confirm this table only ever holds data that's safe to lose (a cache, session "
                    "state, scratch space) — otherwise ALTER TABLE ... SET LOGGED."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_unvalidated_constraint_findings(rows: list[tuple]) -> list[dict]:
    """rows: (table_name, constraint_name, constraint_type) for CHECK ('c')
    and FOREIGN KEY ('f') constraints with convalidated=false — see
    UNVALIDATED_CONSTRAINTS_QUERY in routers/schema_lint.py. Adding a
    constraint NOT VALID is a legitimate way to avoid locking the table for
    a full scan, but it's easy to forget the follow-up VALIDATE CONSTRAINT
    step — until then, Postgres enforces the rule for new/changed rows only
    and the planner can't use it for constraint exclusion, so existing rows
    aren't actually guaranteed to satisfy it."""
    findings = []
    for table_name, constraint_name, constraint_type in rows:
        kind = "CHECK" if constraint_type == "c" else "foreign key"
        findings.append(
            {
                "id": f"unvalidated-constraint-{table_name}-{constraint_name}",
                "category": "schema lint",
                "severity": "attention",
                "title": f"{table_name}.{constraint_name} ({kind}) was added NOT VALID and never validated",
                "summary": (
                    f"This {kind} constraint is enforced for new and updated rows only — Postgres "
                    "hasn't confirmed existing rows satisfy it, and the planner can't use it for "
                    "constraint exclusion until it's validated."
                ),
                "detail": f"table={table_name} constraint={constraint_name} type={constraint_type}",
                "suggested_action": (
                    f"Run ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name} — it only "
                    "takes a brief lock to check existing rows, not a full rewrite."
                ),
                "recommended_ddl": f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name};",
                "schema_name": table_name.rsplit(".", 1)[0] if "." in table_name else None,
                "table_name": table_name.rsplit(".", 1)[-1],
            }
        )
    return findings
