"""Index Advisor analyzers — pure functions over already-queried rows, per
databank/02 - Concepts/PostgreSQL Bad Practices.md's "Indexing Anti-Patterns"
section. Kept separate from the router so each check stays independently
readable and the SQL execution lives in one place.
"""

import re

from app.sql_ident import quote_ident, quote_qualified

# Thresholds for find_seq_scan_heavy_findings. Deliberately much smaller than
# schema_lint.py's partitioning thresholds (5GB / 10M rows) — this check is
# about a planner/index mismatch, not raw table size, so it should fire on
# modest tables too as long as there's been enough scan activity to be a
# real signal rather than noise from a handful of one-off queries.
SEQ_SCAN_ROW_THRESHOLD = 10_000
SEQ_SCAN_MIN_COUNT = 50
SEQ_SCAN_IDX_SHARE_THRESHOLD = 0.05

# Thresholds for find_over_indexed_findings — deliberately generous (most
# tables never approach either number) since this is a soft "worth a look"
# signal, not a hard rule the way an invalid or duplicate index is.
OVER_INDEXED_COUNT_THRESHOLD = 6
OVER_INDEXED_WRITE_THRESHOLD = 100_000

# Thresholds for find_covering_index_candidates. 4+ columns is a "wide"
# index worth a second look; below that, a key+INCLUDE restructuring rarely
# earns its complexity. COVERING_MIN_MATCHED_QUERIES gates the *confident*
# (DDL-producing) path — below it, there isn't enough pg_stat_statements
# history to trust the column-usage read, so the finding still surfaces but
# without a recommended_ddl.
WIDE_INDEX_COLUMN_THRESHOLD = 4
COVERING_MIN_MATCHED_QUERIES = 5

# Thresholds for find_low_cardinality_index_findings. pg_stats.n_distinct is
# either a positive estimate of distinct values, or (when PostgreSQL expects
# distinct count to scale with table size) a negative fraction of
# distinct/total rows — both forms indicate low cardinality when close to
# zero. The softest heuristic of the Index Advisor checks: legitimate
# low-cardinality indexes exist (a boolean column an admin dashboard filters
# on constantly, say), so this stays at "attention," never "critical."
LOW_CARDINALITY_DISTINCT_THRESHOLD = 10
LOW_CARDINALITY_RATIO_THRESHOLD = -0.05
LOW_CARDINALITY_ROW_THRESHOLD = 10_000

# find_unused_indexes only suggests dropping an idle index once usage stats
# cover at least a week — enough for weekly batch jobs to have shown up.
UNUSED_INDEX_MIN_STATS_AGE_DAYS = 7


def _slug(identifier: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", identifier).strip("_")


def _drop_index_ddl(schema: str, index_name: str) -> str:
    return f"DROP INDEX CONCURRENTLY {quote_qualified(schema, index_name)};"


def _is_partition_managed(info: dict) -> bool:
    """A partitioned parent index can't be dropped CONCURRENTLY, and a
    partition's attached copy can't be dropped on its own at all — neither
    ever gets an Apply-able DROP INDEX CONCURRENTLY suggestion."""
    return info["is_partitioned_index"] or info["is_partition_child"]


def find_missing_fk_indexes(fk_rows: list[tuple], index_rows: list[tuple]) -> list[dict]:
    """fk_rows: (table_name, constraint_name, columns, info) — see
    routers/index_advisor.py::fetch_fk_rows. index_rows: (schema,
    table_name, index_name, method, is_unique, key_columns, info) — see
    fetch_index_rows. An FK is "covered" when some non-partial index's key
    columns start with the FK's columns, in the same order — a plain
    equality check on ordered lists, since a leftmost-prefix match is what
    lets an index serve the FK's lookups. A partial index doesn't count: it
    only covers the rows its predicate matches.

    A foreign key on a partitioned table still gets a finding, but no
    Apply-able DDL — PostgreSQL can't build an index CONCURRENTLY on a
    partitioned table, and a plain CREATE INDEX there blocks writes to every
    partition for the whole build."""
    indexes_by_table: dict[str, list[list[str]]] = {}
    for _schema, table_name, _index_name, _method, _is_unique, columns, info in index_rows:
        if not info["is_partial"]:
            indexes_by_table.setdefault(table_name, []).append(columns)

    findings = []
    for table_name, constraint_name, columns, fk_info in fk_rows:
        covered = any(
            existing[: len(columns)] == columns for existing in indexes_by_table.get(table_name, [])
        )
        if covered:
            continue

        column_list = ", ".join(columns)
        is_partitioned = fk_info["relkind"] == "p"
        if is_partitioned:
            recommended_ddl = None
            suggested_action = (
                "This is a partitioned table, so the index can't be built CONCURRENTLY in one step. "
                "Create it ON ONLY the parent, build a matching index CONCURRENTLY on each partition, "
                "then ALTER INDEX ... ATTACH PARTITION each one — or run a plain CREATE INDEX on the "
                "parent during a quiet window (it blocks writes to every partition while it builds)."
            )
        else:
            index_name = f"idx_{_slug(fk_info['table_relname'])}_{_slug('_'.join(columns))}"[:63].lower()
            quoted_columns = ", ".join(quote_ident(column) for column in columns)
            recommended_ddl = (
                f"CREATE INDEX CONCURRENTLY {quote_ident(index_name)} "
                f"ON {quote_qualified(fk_info['schema'], fk_info['table_relname'])} ({quoted_columns});"
            )
            suggested_action = "Add an index covering the foreign key columns."
        findings.append(
            {
                "id": f"missing-fk-index-{table_name}-{constraint_name}",
                "category": "index advisor",
                "severity": "attention",
                "title": f"Foreign key {constraint_name} on {table_name} has no index",
                "summary": (
                    f"PostgreSQL doesn't automatically index foreign key columns. Without one, "
                    f"deleting a row from the referenced table forces a full scan of {table_name}."
                ),
                "detail": f"constraint={constraint_name} columns=({column_list})"
                + (" partitioned_table=true" if is_partitioned else ""),
                "suggested_action": suggested_action,
                "recommended_ddl": recommended_ddl,
                "schema_name": fk_info["schema"],
                "table_name": fk_info["table_relname"],
            }
        )
    return findings


def find_unused_indexes(
    unused_rows: list[tuple],
    stats_age_days: float | None = None,
    fk_covering: dict[tuple[str, str], str] | None = None,
) -> list[dict]:
    """unused_rows: (schema, table_name, index_name, idx_scan, index_bytes).
    Already filtered by the caller's query to idx_scan = 0, excluding every
    index that can't be dropped on its own (PK/unique/exclusion/replica-
    identity/constraint-backed, or a partition's attached copy).

    idx_scan = 0 only means "no scans since the counters were last reset",
    so the finding says how long that is (stats_age_days), and below
    UNUSED_INDEX_MIN_STATS_AGE_DAYS it's informational only, with no DROP
    suggestion — a reset an hour ago proves nothing. fk_covering maps
    (schema, index_name) to the foreign key an index serves (see
    routers/index_advisor.py::fk_covering_indexes); those are informational
    too, never a DROP suggestion, since they're only scanned when a
    referenced row is deleted or its key changes. The counters are also
    per-server: scans on a read replica never show up here."""
    fk_covering = fk_covering or {}
    stats_known = stats_age_days is not None
    stats_mature = stats_known and stats_age_days >= UNUSED_INDEX_MIN_STATS_AGE_DAYS
    stats_window = f"in the {stats_age_days:.0f} days since usage stats were last reset" if stats_known else "since usage stats were last reset"

    findings = []
    for schema, table_name, index_name, _idx_scan, index_bytes in unused_rows:
        backing_fk = fk_covering.get((schema, index_name))
        summary = (
            f"This index has had 0 scans {stats_window}. It still costs write overhead on every "
            "insert/update/delete to this table. Scans on read replicas aren't counted here — "
            "if replicas serve queries, check this index there too."
        )
        if backing_fk:
            severity = "unknown"
            recommended_ddl = None
            summary += (
                f" It covers foreign key {backing_fk}, so it's only scanned when a referenced row is "
                "deleted or its key changes — dropping it would make those operations scan the whole table."
            )
            suggested_action = "Keep this index unless you're sure the referenced rows are never deleted or re-keyed."
        elif not stats_mature:
            severity = "unknown"
            recommended_ddl = None
            summary += (
                f" That's less than {UNUSED_INDEX_MIN_STATS_AGE_DAYS} days of history — too early to call "
                "it unused, since weekly or monthly jobs may not have run yet."
            )
            suggested_action = "Check back once usage stats cover at least a full business cycle."
        else:
            severity = "attention"
            recommended_ddl = _drop_index_ddl(schema, index_name)
            suggested_action = "Confirm this index isn't needed for an infrequent query, then drop it."
        findings.append(
            {
                "id": f"unused-index-{schema}-{index_name}",
                "category": "index advisor",
                "severity": severity,
                "title": f"Index {index_name} on {table_name} has no recorded scans",
                "summary": summary,
                "detail": f"idx_scan=0 index_bytes={index_bytes}"
                + (f" stats_age_days={stats_age_days:.1f}" if stats_known else "")
                + (f" covers_fk={backing_fk}" if backing_fk else ""),
                "suggested_action": suggested_action,
                "recommended_ddl": recommended_ddl,
                "schema_name": schema,
                "table_name": table_name,
            }
        )
    return findings


def find_duplicate_indexes(index_rows: list[tuple]) -> list[dict]:
    """index_rows: (schema, table_name, index_name, method, is_unique,
    key_columns, info). Groups indexes on the same table whose whole
    definition is identical — same access method, key and INCLUDE columns,
    operator classes, sort options, collations, expressions and partial
    predicate (info["definition_signature"]) — genuine duplicates, not just
    indexes over the same column names. A partition's attached copy of an
    index is left out; its parent's duplicate is what gets reported.

    The index kept is a protected one (PK/constraint-backed/replica
    identity) when there is one, then a unique one, then whichever sorts
    first. Only unprotected copies get a DROP suggestion, and never a
    partitioned parent index (DROP INDEX CONCURRENTLY can't run on one)."""
    groups: dict[tuple, list[tuple]] = {}
    for schema, table_name, index_name, method, is_unique, columns, info in index_rows:
        if info["is_partition_child"]:
            continue
        key = (table_name, method, info["definition_signature"])
        groups.setdefault(key, []).append((schema, index_name, is_unique, columns, info))

    findings = []
    seen_ids: set[str] = set()
    for (table_name, method, _signature), members in groups.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(
            members, key=lambda member: (not member[4]["is_protected"], not member[2], member[1])
        )
        keep_schema, keep_name, _keep_unique, columns, _keep_info = members_sorted[0]
        redundant = members_sorted[1:]
        droppable = [
            (schema, name)
            for schema, name, _unique, _columns, info in redundant
            if not info["is_protected"] and not _is_partition_managed(info)
        ]
        redundant_names = ", ".join(f"{schema}.{name}" for schema, name, _u, _c, _i in redundant)
        column_list = ", ".join(columns)

        finding_id = f"duplicate-index-{table_name}-{method}-{'-'.join(columns)}"
        suffix = 2
        while finding_id in seen_ids:
            finding_id = f"duplicate-index-{table_name}-{method}-{'-'.join(columns)}-{suffix}"
            suffix += 1
        seen_ids.add(finding_id)

        if droppable:
            suggested_action = f"Keep {keep_schema}.{keep_name} and drop the others."
            recommended_ddl = "\n".join(_drop_index_ddl(schema, name) for schema, name in droppable)
        else:
            suggested_action = (
                f"Keep {keep_schema}.{keep_name}. The other copies back a constraint or belong to a "
                "partitioned table, so they can't be dropped with a plain DROP INDEX CONCURRENTLY — "
                "review them manually."
            )
            recommended_ddl = None
        findings.append(
            {
                "id": finding_id,
                "category": "index advisor",
                "severity": "attention",
                "title": f"{len(members)} indexes on {table_name} have the exact same definition",
                "summary": (
                    f"{redundant_names} duplicate {keep_schema}.{keep_name} ({column_list}) — each "
                    "one costs write overhead for no extra benefit."
                ),
                "detail": f"table={table_name} method={method} columns=({column_list}) "
                f"indexes={[f'{schema}.{name}' for schema, name, _u, _c, _i in members]}",
                "suggested_action": suggested_action,
                "recommended_ddl": recommended_ddl,
                "schema_name": keep_schema,
                "table_name": table_name,
            }
        )
    return findings


def find_seq_scan_heavy_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table_name, seq_scan, idx_scan, live_tup, total_bytes)
    for tables with at least one non-PK index — see SEQ_SCAN_HEAVY_QUERY in
    routers/index_advisor.py. A distinct signal from find_unused_indexes
    (idx_scan = 0): here the index isn't completely idle, but the planner
    still prefers a sequential scan almost every time despite meaningful
    seq_scan volume — usually a sign the index doesn't match the columns
    actually filtered on, or that statistics are stale."""
    findings = []
    for schema, table_name, seq_scan, idx_scan, live_tup, total_bytes in rows:
        seq_scan = seq_scan or 0
        idx_scan = idx_scan or 0
        live_tup = live_tup or 0
        total_scans = seq_scan + idx_scan
        if live_tup < SEQ_SCAN_ROW_THRESHOLD or seq_scan < SEQ_SCAN_MIN_COUNT or total_scans == 0:
            continue
        idx_share = idx_scan / total_scans
        if idx_share >= SEQ_SCAN_IDX_SHARE_THRESHOLD:
            continue

        full_name = f"{schema}.{table_name}"
        findings.append(
            {
                "id": f"seq-scan-heavy-{schema}-{table_name}",
                "category": "index advisor",
                "severity": "attention",
                "title": f"{full_name} has an index but is mostly sequentially scanned",
                "summary": (
                    f"This table has an index, but only {idx_share * 100:.0f}% of its "
                    f"{total_scans:,} scans used it — the other {seq_scan:,} read the full "
                    f"table ({live_tup:,} rows) instead. The index may not match the columns "
                    "your queries actually filter on, or its statistics may be stale."
                ),
                "detail": f"seq_scan={seq_scan} idx_scan={idx_scan} live_tup={live_tup} total_bytes={total_bytes}",
                "suggested_action": (
                    "Run EXPLAIN on this table's common queries to check whether an index exists "
                    "for the columns actually filtered on, and run ANALYZE in case statistics are stale."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table_name,
            }
        )
    return findings


def find_invalid_indexes(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table_name, index_name, index_bytes) for indexes left
    behind by a failed CREATE INDEX CONCURRENTLY or REINDEX CONCURRENTLY —
    indisvalid=false. The planner never uses an invalid index; it's pure
    dead weight, both on disk and (until dropped) blocking a same-named
    replacement from being created."""
    findings = []
    for schema, table_name, index_name, index_bytes in rows:
        findings.append(
            {
                "id": f"invalid-index-{schema}-{index_name}",
                "category": "index advisor",
                "severity": "attention",
                "title": f"Index {index_name} on {table_name} is invalid",
                "summary": (
                    "This index was left in a broken state, most likely by a CREATE INDEX "
                    "CONCURRENTLY or REINDEX CONCURRENTLY that failed partway through. The planner "
                    "never uses an invalid index — it's pure dead weight."
                ),
                "detail": f"index_bytes={index_bytes}",
                "suggested_action": "Drop it and, if it's still needed, recreate it with CREATE INDEX CONCURRENTLY.",
                "recommended_ddl": _drop_index_ddl(schema, index_name),
                "schema_name": schema,
                "table_name": table_name,
            }
        )
    return findings


def find_redundant_indexes(index_rows: list[tuple]) -> list[dict]:
    """index_rows: (schema, table_name, index_name, method, is_unique,
    key_columns, info) — the same rows find_duplicate_indexes and
    find_missing_fk_indexes use. A plain B-tree index is redundant once
    another full (non-partial) B-tree index on the same table starts with
    the exact same ordered key columns *and* the same operator class, sort
    option and collation at each of those positions (a leftmost prefix) —
    any query the shorter index could serve, the wider one serves just as
    well. Distinct from find_duplicate_indexes, which only catches an
    *identical* definition; this catches the more common case of one
    index's keys being a strict prefix of another's.

    The prefix rule only holds for B-tree, and only between plain column
    indexes, so a partial index, an expression index or one with INCLUDE
    columns is never called redundant, and a partial or expression index is
    never treated as the wider one either. Indexes on partitioned tables are
    skipped (no CONCURRENTLY drop exists for them)."""
    by_table: dict[str, list[tuple]] = {}
    for schema, table_name, index_name, method, is_unique, columns, info in index_rows:
        if method != "btree" or info["is_partial"] or info["has_expressions"] or _is_partition_managed(info):
            continue
        by_table.setdefault(table_name, []).append(
            (schema, index_name, is_unique, tuple(columns), tuple(info["key_signatures"]), info)
        )

    findings = []
    for table_name, members in by_table.items():
        for schema, index_name, is_unique, columns, signatures, info in members:
            # Dropping a unique/PK-backing index loses its constraint, not
            # just an access path, so it stays regardless of a wider index.
            if is_unique or info["is_protected"] or info["include_columns"]:
                continue
            covering = next(
                (
                    other_name
                    for _other_schema, other_name, _other_unique, other_columns, other_signatures, _other_info in members
                    if other_name != index_name
                    and len(other_columns) > len(columns)
                    and other_columns[: len(columns)] == columns
                    and other_signatures[: len(signatures)] == signatures
                ),
                None,
            )
            if covering is None:
                continue
            column_list = ", ".join(columns)
            findings.append(
                {
                    "id": f"redundant-index-{table_name}-{index_name}",
                    "category": "index advisor",
                    "severity": "attention",
                    "title": f"{index_name} on {table_name} is covered by a wider index",
                    "summary": (
                        f"{index_name} ({column_list}) is a leftmost prefix of {covering} on the same "
                        "table — any query the shorter index could serve, the wider one serves just "
                        "as well."
                    ),
                    "detail": f"table={table_name} method=btree redundant={index_name} covers={covering}",
                    "suggested_action": f"Drop {index_name} and keep {covering}.",
                    "recommended_ddl": _drop_index_ddl(schema, index_name),
                    "schema_name": schema,
                    "table_name": table_name,
                }
            )
    return findings


def find_seq_scan_no_index_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table_name, seq_scan, live_tup, total_bytes) for
    tables with no non-primary-key index at all — see SEQ_SCAN_NO_INDEX_QUERY
    in routers/index_advisor.py. Distinct from find_seq_scan_heavy_findings,
    which only fires once an index already exists but goes unused; this is
    the more common case of a genuinely missing index. Reuses that check's
    thresholds since both are "is there enough scan activity to be a real
    signal" questions of the same shape."""
    findings = []
    for schema, table_name, seq_scan, live_tup, total_bytes in rows:
        seq_scan = seq_scan or 0
        live_tup = live_tup or 0
        if live_tup < SEQ_SCAN_ROW_THRESHOLD or seq_scan < SEQ_SCAN_MIN_COUNT:
            continue
        full_name = f"{schema}.{table_name}"
        findings.append(
            {
                "id": f"seq-scan-no-index-{schema}-{table_name}",
                "category": "index advisor",
                "severity": "attention",
                "title": f"{full_name} has no index and is scanned sequentially {seq_scan:,} times",
                "summary": (
                    f"This table has {live_tup:,} rows and not a single non-primary-key index. Every "
                    "query that filters on anything but the primary key reads the whole table."
                ),
                "detail": f"seq_scan={seq_scan} live_tup={live_tup} total_bytes={total_bytes}",
                "suggested_action": (
                    "Check which columns this table's queries actually filter on and add an index "
                    "covering them."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table_name,
            }
        )
    return findings


def _indexes_by_table(index_rows: list[tuple], index_bytes: dict[tuple[str, str], int]) -> dict[tuple[str, str], list[dict]]:
    """index_rows: (schema, table_name, index_name, method, is_unique,
    key_columns, info) — table_name may come from a regclass cast (schema-qualified
    when the schema isn't on search_path) or a bare relname depending on the
    query, so the grouping key strips any schema prefix defensively to stay
    consistent regardless of which one produced it."""
    by_table: dict[tuple[str, str], list[dict]] = {}
    for schema, table_name, index_name, method, is_unique, columns, _info in index_rows:
        bare_table = table_name.rsplit(".", 1)[-1]
        by_table.setdefault((schema, bare_table), []).append(
            {
                "index_name": index_name,
                "columns": columns,
                "is_unique": is_unique,
                "method": method,
                "index_bytes": index_bytes.get((schema, index_name)),
            }
        )
    return by_table


def find_over_indexed_findings(
    rows: list[tuple], index_rows: list[tuple], index_bytes: dict[tuple[str, str], int]
) -> list[dict]:
    """rows: (schema, table_name, index_count, write_ops) — write_ops is
    n_tup_ins + n_tup_upd + n_tup_del from pg_stat_user_tables. Every index
    adds write overhead on every insert/update/delete; a table with many
    indexes and heavy write volume is paying that tax broadly, distinct from
    find_unused_indexes, which only catches indexes that are individually
    idle. index_rows/index_bytes (same shape as find_covering_index_candidates
    below) let each finding carry the table's actual indexes and their
    columns, so the UI can render a coverage table instead of just a count."""
    indexes_by_table = _indexes_by_table(index_rows, index_bytes)

    findings = []
    for schema, table_name, index_count, write_ops in rows:
        index_count = index_count or 0
        write_ops = write_ops or 0
        if index_count < OVER_INDEXED_COUNT_THRESHOLD or write_ops < OVER_INDEXED_WRITE_THRESHOLD:
            continue
        full_name = f"{schema}.{table_name}"
        finding = {
            "id": f"over-indexed-{schema}-{table_name}",
            "category": "index advisor",
            "severity": "attention",
            "title": f"{full_name} has {index_count} indexes on a write-heavy table",
            "summary": (
                f"Every index costs write overhead on insert/update/delete. This table has seen "
                f"{write_ops:,} write operations while carrying {index_count} indexes — worth "
                "confirming each one earns its keep, especially alongside any Unused Indexes "
                "findings for the same table."
            ),
            "detail": f"index_count={index_count} write_ops={write_ops}",
            "suggested_action": "Cross-check against the Unused Indexes findings for this table before deciding what to drop.",
            "recommended_ddl": None,
            "schema_name": schema,
            "table_name": table_name,
        }
        table_indexes = indexes_by_table.get((schema, table_name))
        if table_indexes:
            finding["index_columns"] = table_indexes
        findings.append(finding)
    return findings


def find_low_cardinality_index_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table_name, index_name, column_name, n_distinct,
    reltuples) for single-column, non-unique, non-partial indexes — see
    LOW_CARDINALITY_INDEX_QUERY in routers/index_advisor.py. A full index on
    a handful of repeated values is rarely selective enough for the planner
    to prefer over a sequential scan, but still costs write overhead on
    every insert/update/delete."""
    findings = []
    for schema, table_name, index_name, column_name, n_distinct, reltuples in rows:
        if n_distinct is None or reltuples is None or reltuples < LOW_CARDINALITY_ROW_THRESHOLD:
            continue
        is_low = (0 <= n_distinct <= LOW_CARDINALITY_DISTINCT_THRESHOLD) or (
            n_distinct < 0 and n_distinct >= LOW_CARDINALITY_RATIO_THRESHOLD
        )
        if not is_low:
            continue
        full_name = f"{schema}.{table_name}"
        distinct_display = (
            f"~{n_distinct:.0f} distinct values" if n_distinct >= 0 else f"~{-n_distinct * 100:.1f}% distinct"
        )
        findings.append(
            {
                "id": f"low-cardinality-index-{schema}-{index_name}",
                "category": "index advisor",
                "severity": "attention",
                "title": f"{index_name} indexes a low-cardinality column",
                "summary": (
                    f"{full_name}.{column_name} has {distinct_display} across ~{int(reltuples):,} rows. "
                    "A full index this repetitive rarely earns the planner's preference over a "
                    "sequential scan, but still costs write overhead."
                ),
                "detail": f"column={column_name} n_distinct={n_distinct} reltuples={reltuples}",
                "suggested_action": (
                    "If only one value (or a small set) is actually queried often, a partial index on "
                    "just that value is usually far more effective than indexing the whole column."
                ),
                "recommended_ddl": None,
                "schema_name": schema,
                "table_name": table_name,
            }
        )
    return findings


# Regex-based, best-effort read of whether a column shows up in a
# predicate/ordering position — not a real SQL parser, so it can be fooled
# (a column name that's also a string literal, or another table's
# same-named column). Same category of limitation as schema_lint.py's
# query-pattern checks, which also regex-match pg_stat_statements' text.
_COMPARISON_AFTER_PATTERN = re.compile(r"^\s*(=|<>|!=|<=|>=|<|>|\bin\b|\blike\b|\bbetween\b)", re.IGNORECASE)
_COMPARISON_BEFORE_PATTERN = re.compile(r"(=|<>|!=|<=|>=|<|>)\s*$", re.IGNORECASE)
_ORDER_GROUP_CLAUSE_PATTERN = re.compile(
    r"\b(?:order|group)\s+by\b(.*?)(?:\blimit\b|\boffset\b|\bfor\s+update\b|$)", re.IGNORECASE | re.DOTALL
)
_PROXIMITY_WINDOW = 20


def _column_verdict(column: str, matched_queries: list[str]) -> str:
    """"key" if `column` ever appears next to a comparison operator or
    inside an ORDER BY/GROUP BY clause across matched_queries (evidence
    it's actually used for filtering/sorting, so it should stay in the
    index key); "include_candidate" if it's referenced but never in that
    position (almost always a SELECT list — a candidate to move to
    INCLUDE); "unused_in_sample" if it isn't referenced at all in the
    sample."""
    col_pattern = re.compile(rf"\b{re.escape(column)}\b", re.IGNORECASE)
    referenced = False
    for query in matched_queries:
        for match in col_pattern.finditer(query):
            referenced = True
            after = query[match.end() : match.end() + _PROXIMITY_WINDOW]
            before = query[max(0, match.start() - _PROXIMITY_WINDOW) : match.start()]
            if _COMPARISON_AFTER_PATTERN.match(after) or _COMPARISON_BEFORE_PATTERN.search(before):
                return "key"
        order_group = _ORDER_GROUP_CLAUSE_PATTERN.search(query)
        if order_group and col_pattern.search(order_group.group(1)):
            return "key"
    return "include_candidate" if referenced else "unused_in_sample"


def build_covering_index_ddl(
    schema: str, table_relname: str, index_name: str, key_columns: list[str], include_columns: list[str]
) -> str:
    """Shared by find_covering_index_candidates' synchronous heuristic path
    below and the AI-analysis background task
    (routers/index_coverage_analysis.py) so the DDL is generated identically
    either way — the AI is only ever asked to judge which columns to move,
    never to write DDL itself. CREATE runs before DROP so there's never a
    window with no covering index on the table at all. table_relname is the
    table's bare, unquoted pg_class name; every identifier is quoted here."""
    new_index_name = f"{index_name}_covering"[:63]
    key_list = ", ".join(quote_ident(column) for column in key_columns)
    include_list = ", ".join(quote_ident(column) for column in include_columns)
    return (
        f"CREATE INDEX CONCURRENTLY {quote_ident(new_index_name)} ON {quote_qualified(schema, table_relname)} "
        f"({key_list}) INCLUDE ({include_list});\n"
        f"{_drop_index_ddl(schema, index_name)}"
    )


def _covering_summary(confident: bool, matched_queries: list[str], verdicts: list[dict]) -> str:
    candidates = [v["column"] for v in verdicts if v["verdict"] in ("include_candidate", "unused_in_sample")]
    if not matched_queries:
        return (
            "This index has 4+ columns, but no recent queries in pg_stat_statements reference this "
            "table, so there's no usage data yet to judge which columns are actually filtered/sorted "
            "on versus only ever fetched."
        )
    if confident:
        column_list = ", ".join(candidates)
        return (
            f"Across {len(matched_queries):,} recent queries touching this table, {column_list} never "
            "showed up in a WHERE/ORDER BY/GROUP BY position — only ever fetched, not filtered or "
            "sorted on. Moving them to INCLUDE keeps the same index-only-scan benefit with a "
            "narrower, cheaper-to-maintain key."
        )
    query_word = "query" if len(matched_queries) == 1 else "queries"
    return (
        f"This index has 4+ columns, but only {len(matched_queries):,} recent {query_word} reference "
        "this table — not enough to confidently tell filter-only columns from fetch-only ones. Run "
        "AI Analyze for a closer read, or revisit once more query history has accumulated."
    )


def queries_referencing_table(table_name: str, recent_queries: list[tuple]) -> list[str]:
    """recent_queries: (query, calls) rows. Returns the query texts whose
    text mentions table_name as a whole word — shared by
    find_covering_index_candidates and the AI-analysis background task
    (routers/index_coverage_analysis.py), which needs the same matched-query
    set to build its prompt."""
    bare_table = table_name.rsplit(".", 1)[-1]
    table_pattern = re.compile(rf"\b{re.escape(bare_table)}\b", re.IGNORECASE)
    return [query for query, _calls in recent_queries if table_pattern.search(query)]


def find_covering_index_candidates(
    index_rows: list[tuple], index_bytes: dict[tuple[str, str], int], recent_queries: list[tuple]
) -> list[dict]:
    """index_rows/index_bytes: same shape as find_over_indexed_findings.
    recent_queries: (query, calls) from pg_stat_statements — see
    RECENT_QUERIES_QUERY in routers/index_advisor.py. For every wide
    (WIDE_INDEX_COLUMN_THRESHOLD+ column), non-unique index (a unique/PK-
    backing index is a constraint, not just an access path — restructuring
    it is a different, riskier operation, same exclusion
    find_redundant_indexes applies), checks whether each *trailing* column
    (every column after the first — the leading column always stays, since
    it's what the index is fundamentally organized around) shows evidence of
    being used to filter/sort in queries that reference the table, or only
    ever shows up elsewhere (almost always a SELECT list). A confident
    finding — enough matched queries, and at least one trailing column with
    no filter/sort evidence — gets a ready-to-apply CREATE ... INCLUDE / DROP
    DDL pair; otherwise the finding still surfaces (there's still something
    worth a look) but without recommended_ddl, pointing at AI Analyze
    instead. A wide index where every trailing column *does* show
    filter/sort evidence isn't flagged at all — it looks justified.

    Only plain B-tree column indexes qualify: INCLUDE is B-tree/GiST/SP-GiST
    only, an expression or partial index can't be rebuilt from its column
    names alone, one that already has INCLUDE columns has already been
    restructured, and a partitioned or protected index can't be swapped with
    a CONCURRENTLY create/drop pair."""
    findings = []
    for schema, table_name, index_name, method, is_unique, columns, info in index_rows:
        if is_unique or len(columns) < WIDE_INDEX_COLUMN_THRESHOLD:
            continue
        if (
            method != "btree"
            or info["is_partial"]
            or info["has_expressions"]
            or info["include_columns"]
            or info["is_protected"]
            or _is_partition_managed(info)
        ):
            continue

        bare_table = table_name.rsplit(".", 1)[-1]
        matched_queries = queries_referencing_table(table_name, recent_queries)

        verdicts = [{"column": column, "verdict": _column_verdict(column, matched_queries)} for column in columns[1:]]

        if matched_queries and all(v["verdict"] == "key" for v in verdicts):
            continue  # every trailing column shows real usage — looks justified, nothing to flag

        confident = len(matched_queries) >= COVERING_MIN_MATCHED_QUERIES and any(
            v["verdict"] in ("include_candidate", "unused_in_sample") for v in verdicts
        )

        recommended_ddl = None
        if confident:
            include_columns = [v["column"] for v in verdicts if v["verdict"] in ("include_candidate", "unused_in_sample")]
            key_columns = [columns[0]] + [v["column"] for v in verdicts if v["verdict"] == "key"]
            recommended_ddl = build_covering_index_ddl(
                schema, info["table_relname"], index_name, key_columns, include_columns
            )

        full_name = f"{schema}.{bare_table}"
        column_list = ", ".join(columns)
        findings.append(
            {
                "id": f"covering-index-{schema}-{bare_table}-{index_name}",
                "category": "index advisor",
                "severity": "attention" if confident else "unknown",
                "title": f"{index_name} on {full_name} is a wide index ({len(columns)} columns)",
                "summary": _covering_summary(confident, matched_queries, verdicts),
                "detail": f"columns=({column_list}) matched_queries={len(matched_queries)}",
                "suggested_action": (
                    "Review the suggested DDL below before applying — this restructures the index."
                    if confident
                    else "Review the column verdicts below, or run AI Analyze for a closer read."
                ),
                "recommended_ddl": recommended_ddl,
                "index_columns": [
                    {
                        "index_name": index_name,
                        "columns": columns,
                        "is_unique": is_unique,
                        "method": method,
                        "index_bytes": index_bytes.get((schema, index_name)),
                    }
                ],
                "covering_analysis": {
                    "matched_query_count": len(matched_queries),
                    "confident": confident,
                    "columns": verdicts,
                },
                "schema_name": schema,
                "table_name": bare_table,
            }
        )
    return findings
