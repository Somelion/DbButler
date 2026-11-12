"""Workload-driven Refactoring Advisor: schema changes suggested by what
the tracked queries actually do (app/workload_data.py — the Workload Map's
data), plus table/index statistics and a few catalog facts.

Every finding cites the queries behind it and their share of DB time, so it
reads as evidence, not a verdict. The rules with weaker evidence (column
usage, value skew, denormalization) use severity "unknown" — this
codebase's "advisory, review it" level — and say so in their wording.

Pure functions over plain data; routers/refactoring_advisor.py gathers it.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.sql_ident import quote_ident, quote_qualified
from app.workload_extract import REFERENCED_COLUMNS_MIN_VERSION, Table
from app.workload_graph import JoinRow, QueryInfo, RelationRow, query_calls, query_weight

CATEGORY = "refactoring advisor"

# Thresholds. Shares are % of tracked DB time in the window.
HOT_JOIN_SHARE_PCT = 5.0
JOIN_INDEX_MIN_SHARE_PCT = 1.0
MIN_TABLE_BYTES = 64 * 1024  # below this a seq scan is as good as an index
PARTITION_FILTER_SHARE = 0.8
PARTITION_DELETE_CHURN = 0.01  # deleted rows / live rows in the window
DENORM_SHARE_PCT = 20.0
DENORM_MAX_WRITE_RATIO = 0.01
CONSOLIDATE_LOW_USAGE = 0.1  # narrower index's scans vs the wider one's
WRITE_AMP_MIN_UPDATES = 1000
WRITE_AMP_MIN_UPDATE_RATIO = 0.1
WRITE_AMP_MAX_HOT_PCT = 50.0
WRITE_AMP_MIN_INDEXES = 4
COLD_MIN_QUERIES = 3
COLD_MIN_COVERAGE_PCT = 70.0
COLD_MIN_COLUMNS = 4
WIDE_MIN_ROW_BYTES = 400
WIDE_MIN_COLUMNS = 20
WIDE_MAX_COLUMN_FRACTION = 0.3
SKEW_MIN_SHARE_PCT = 5.0
SKEW_MIN_FREQUENCY = 0.3
# The top value must also be this many times more common than an even
# spread (1 / n_distinct) and than the runner-up — a 50/50 boolean or a
# three-value status column isn't skew.
SKEW_DOMINANCE = 3.0
MIN_EVIDENCE_HOURS = 24.0
MAX_OCCURRENCES = 5

TENANT_COLUMN = re.compile(r"^(tenant|account|org|organization|organisation|company|client|customer|workspace)_?id$")
TIME_TYPE_CATEGORY = "D"  # pg_type.typcategory: date/time types
WRITE_KINDS = frozenset({"insert", "update", "delete", "merge"})


@dataclass
class IndexInfo:
    name: str
    method: str
    key_columns: list[str | None]  # None = expression column
    is_unique: bool
    is_primary: bool
    is_valid: bool
    is_partial: bool
    backs_constraint: bool
    size_bytes: int | None
    definition: str


@dataclass
class ColumnStats:
    """From pg_stats — never the values themselves, which can be personal data."""

    avg_width: int | None
    top_frequency: float | None  # most_common_freqs[1]
    second_frequency: float | None  # most_common_freqs[2]
    n_distinct: float | None  # > 0 a count, < 0 a fraction of rows


@dataclass
class TableInfo:
    relkind: str
    fillfactor: int | None
    is_hypertable: bool
    columns: list[tuple[str, str, str]] = field(default_factory=list)  # (name, type, typcategory)
    indexes: list[IndexInfo] = field(default_factory=list)
    column_stats: dict[str, ColumnStats] = field(default_factory=dict)

    @property
    def column_names(self) -> set[str]:
        return {name for name, _type, _category in self.columns}


@dataclass
class RefactorInput:
    graph: dict
    queries: list[QueryInfo]
    relations: list[RelationRow]
    joins: list[JoinRow]
    table_stats: dict[Table, dict]
    tables: dict[Table, TableInfo]
    # (Table, index name) -> idx_scan growth over the window (None = no data)
    index_scan_deltas: dict[tuple[Table, str], int]
    partition_min_bytes: int
    # How many hours the index-scan deltas actually span (None: under two
    # snapshots). "Unused" claims need enough of it: see _min_evidence_hours.
    index_window_hours: float | None = None
    timescaledb_installed: bool = False
    include_table: Callable[[Table], bool] = lambda _table: True


def _parse_table(qualified: str) -> Table:
    schema, _, name = qualified.partition(".")
    return Table(schema, name)


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "unknown size"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _occurrences(top_queries: list[dict]) -> list[dict]:
    return [
        {"query": q["query"], "calls": q["calls"], "note": f"{q['db_time_ms']:.1f} ms DB time"}
        for q in top_queries[:MAX_OCCURRENCES]
    ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _index_name(table: Table, columns: list[str]) -> str:
    return f"idx_{_slug(table.name)}_{_slug('_'.join(columns))}"[:63]


def _create_index_ddl(table: Table, columns: list[str]) -> str:
    cols = ", ".join(quote_ident(c) for c in columns)
    return (
        f"CREATE INDEX CONCURRENTLY {quote_ident(_index_name(table, columns))} "
        f"ON {quote_qualified(table.schema, table.name)} ({cols});"
    )


def _finding(id_, severity, table: Table | None, title, summary, detail, action, **extra) -> dict:
    return {
        "id": id_,
        "category": CATEGORY,
        "severity": severity,
        "title": title,
        "summary": summary,
        "detail": detail,
        "suggested_action": action,
        "schema_name": table.schema if table else None,
        "table_name": table.name if table else None,
        **extra,
    }


class _Context:
    """Lookups shared by the rules."""

    def __init__(self, data: RefactorInput):
        self.data = data
        self.basis = data.graph["meta"]["weight_basis"]
        self.nodes = {n["id"]: n for n in data.graph["nodes"]}
        self.queries = {q.tracked_id: q for q in data.queries}
        self.relations_by_table: dict[Table, list[RelationRow]] = {}
        for row in data.relations:
            if row.tracked_id in self.queries:
                self.relations_by_table.setdefault(row.table, []).append(row)
        self.join_columns_by_table: dict[Table, set[str]] = {}
        for row in data.joins:
            if row.tracked_id in self.queries:
                self.join_columns_by_table.setdefault(row.left, set()).update(row.left_columns)
                self.join_columns_by_table.setdefault(row.right, set()).update(row.right_columns)

    def weight(self, tracked_id) -> float:
        return query_weight(self.queries[tracked_id], self.basis)

    def min_evidence_hours(self) -> float:
        """Usage and change counters must cover at least a day (or half of a
        shorter window) before "unused" or "barely used" means anything — a
        target connected ten minutes ago has two snapshots and no evidence."""
        return max(1.0, min(MIN_EVIDENCE_HOURS, self.data.graph["meta"]["hours"] / 2))

    def index_evidence(self) -> bool:
        span = self.data.index_window_hours
        return span is not None and span >= self.min_evidence_hours()

    def table_evidence(self) -> bool:
        span = self.data.graph["meta"]["stats_window_hours"]
        return span is not None and span >= self.min_evidence_hours()

    def table_info(self, table: Table) -> TableInfo | None:
        return self.data.tables.get(table)

    def is_partitioned(self, table: Table) -> bool:
        info = self.table_info(table)
        return info is not None and (info.relkind == "p" or info.is_hypertable)

    def usable_indexes(self, table: Table) -> list[IndexInfo]:
        info = self.table_info(table)
        return [i for i in (info.indexes if info else []) if i.is_valid and not i.is_partial]

    def unique_key_sets(self, table: Table) -> list[set[str]]:
        return [
            set(i.key_columns)
            for i in self.usable_indexes(table)
            if (i.is_unique or i.is_primary) and None not in i.key_columns
        ]

    def query_samples(self, table: Table) -> list[dict]:
        rows = sorted(self.relations_by_table.get(table, []), key=lambda r: self.weight(r.tracked_id), reverse=True)
        samples = []
        for row in rows[:MAX_OCCURRENCES]:
            q = self.queries[row.tracked_id]
            samples.append(
                {
                    "query": " ".join(q.text.split())[:200],
                    "calls": query_calls(q, self.basis),
                    "db_time_ms": round(self.weight(row.tracked_id), 3),
                }
            )
        return samples


# --- Rules -------------------------------------------------------------------


def _uncovered_groups(edge: dict, min_share: float):
    """The edge's join conditions with no FK behind them, each on its own —
    two different joins between the same tables (sender_id and
    recipient_id to users.id) are never treated as one composite key."""
    for group in edge["column_groups"]:
        if group["foreign_key"] is None and group["share_pct"] >= min_share:
            yield group


def _missing_fk_on_hot_join(ctx: _Context) -> list[dict]:
    findings = []
    for edge in ctx.data.graph["edges"]:
        source, target = _parse_table(edge["source"]), _parse_table(edge["target"])
        if any((ctx.nodes[t.qualified]["total_bytes"] or 0) < MIN_TABLE_BYTES for t in (source, target)):
            continue
        pair = f"{source.qualified} ⇄ {target.qualified}"
        for group in _uncovered_groups(edge, HOT_JOIN_SHARE_PCT):
            source_cols = [p[0] for p in group["pairs"]]
            target_cols = [p[1] for p in group["pairs"]]
            source_unique = set(source_cols) in ctx.unique_key_sets(source)
            target_unique = set(target_cols) in ctx.unique_key_sets(target)
            share = _fmt_pct(group["share_pct"])
            condition = ", ".join(f"{a} = {b}" for a, b in group["pairs"])

            if not (target_unique or source_unique):
                findings.append(
                    _finding(
                        f"missing-fk-join-{source.qualified}-{target.qualified}-{'_'.join(source_cols)}",
                        "unknown",
                        source,
                        f"{pair} are joined in {share} of DB time on columns that aren't a key on either side",
                        "A foreign key needs a unique key on the referenced side, and neither side's join columns "
                        "are unique — so this is a many-to-many match or a join on a non-key column.",
                        f"Join condition: {condition}.",
                        "Check whether the join is intended. If one side should be unique, add a unique constraint "
                        "and then a foreign key; if it's a many-to-many relationship, a junction table usually "
                        "models it better.",
                        occurrences=_occurrences(edge["top_queries"]),
                    )
                )
                continue

            child, child_cols, parent, parent_cols = (
                (source, source_cols, target, target_cols) if target_unique else (target, target_cols, source, source_cols)
            )
            constraint = f"{_slug(child.name)}_{_slug('_'.join(child_cols))}_fkey"[:63]
            child_q = quote_qualified(child.schema, child.name)
            add_fk = (
                f"ALTER TABLE {child_q} ADD CONSTRAINT {quote_ident(constraint)} "
                f"FOREIGN KEY ({', '.join(quote_ident(c) for c in child_cols)}) "
                f"REFERENCES {quote_qualified(parent.schema, parent.name)} "
                f"({', '.join(quote_ident(c) for c in parent_cols)})"
            )
            # PostgreSQL refuses NOT VALID foreign keys on a partitioned table.
            partitioned_child = ctx.is_partitioned(child)
            if partitioned_child:
                ddl = f"{add_fk};"
                how = (
                    f"{child.qualified} is partitioned, where PostgreSQL can't add a NOT VALID foreign key: the "
                    "constraint is checked against every partition while it's added, holding a lock that blocks "
                    "writes — run it in a quiet window."
                )
            else:
                ddl = f"{add_fk} NOT VALID;\nALTER TABLE {child_q} VALIDATE CONSTRAINT {quote_ident(constraint)};"
                how = (
                    "Declare it in two steps: ADD ... NOT VALID takes only a brief lock and checks new rows from "
                    "then on; VALIDATE CONSTRAINT then checks existing rows without blocking writes."
                )
            orphan_check = (
                f"SELECT count(*) FROM {child_q} c WHERE "
                + " AND ".join(f"c.{quote_ident(c)} IS NOT NULL" for c in child_cols)
                + f" AND NOT EXISTS (SELECT 1 FROM {quote_qualified(parent.schema, parent.name)} p WHERE "
                + " AND ".join(
                    f"p.{quote_ident(p)} = c.{quote_ident(c)}" for c, p in zip(child_cols, parent_cols, strict=False)
                )
                + ");"
            )
            count = group["query_count"]
            findings.append(
                _finding(
                    f"missing-fk-join-{child.qualified}-{'_'.join(child_cols)}",
                    "attention",
                    child,
                    f"{pair} are joined in {share} of DB time with no foreign key",
                    f"{count} tracked quer{'y' if count == 1 else 'ies'} join {child.qualified} "
                    f"({', '.join(child_cols)}) to {parent.qualified} ({', '.join(parent_cols)}), which is a key "
                    f"there — but nothing stops a {child.name} row pointing at a missing {parent.name}.",
                    f"Join condition: {condition}. Before adding the constraint, check for orphans:\n{orphan_check}",
                    f"{how} Index the referencing columns too, if they aren't already.",
                    recommended_ddl=ddl,
                    occurrences=_occurrences(edge["top_queries"]),
                )
            )
    return findings


def _missing_join_index(ctx: _Context) -> list[dict]:
    findings = []
    for edge in ctx.data.graph["edges"]:
        for group in _uncovered_groups(edge, JOIN_INDEX_MIN_SHARE_PCT):
            nested_loop = group["nested_loop"]
            for side_key, indexed_key, col_index in (
                ("source", "source_indexed", 0),
                ("target", "target_indexed", 1),
            ):
                if group[indexed_key]:
                    continue
                table = _parse_table(edge[side_key])
                node = ctx.nodes[table.qualified]
                if (node["total_bytes"] or 0) < MIN_TABLE_BYTES:
                    continue
                if not (nested_loop or "seq" in node["access_kinds"]):
                    continue
                columns = list(dict.fromkeys(p[col_index] for p in group["pairs"]))
                partitioned = ctx.is_partitioned(table)
                share = _fmt_pct(group["share_pct"])
                findings.append(
                    _finding(
                        f"missing-join-index-{table.qualified}-{'_'.join(columns)}",
                        "attention" if group["share_pct"] >= HOT_JOIN_SHARE_PCT else "unknown",
                        table,
                        f"Join column {table.name}.{', '.join(columns)} has no index ({share} of DB time)",
                        f"{table.qualified} is joined to {edge['target'] if side_key == 'source' else edge['source']} "
                        f"on {', '.join(columns)}, and no index leads with that column, so "
                        + (
                            "each nested-loop probe scans the table."
                            if nested_loop
                            else "the join reads the whole table."
                        ),
                        f"Join strategies: {', '.join(edge['join_strategies']) or 'not shown'}. Access to "
                        f"{table.name}: {', '.join(node['access_kinds'])}. Table size {_fmt_bytes(node['total_bytes'])}.",
                        (
                            "Partitioned table: create the index on the parent (it can't be built CONCURRENTLY "
                            "there), or build it CONCURRENTLY on each partition and attach. "
                            if partitioned
                            else ""
                        )
                        + "Cost it first in What-if Indexes — HypoPG estimates which queries get cheaper without "
                        "building anything.",
                        recommended_ddl=None if partitioned else _create_index_ddl(table, columns),
                        occurrences=_occurrences(edge["top_queries"]),
                        index_candidate={"schema_name": table.schema, "table_name": table.name, "columns": columns},
                    )
                )
    return findings


def _partitioning_candidate(ctx: _Context) -> list[dict]:
    findings = []
    for table, rows in ctx.relations_by_table.items():
        node = ctx.nodes.get(table.qualified)
        info = ctx.table_info(table)
        if node is None or info is None or info.relkind != "r" or info.is_hypertable:
            continue
        if (node["total_bytes"] or 0) < ctx.data.partition_min_bytes or len(rows) < 2:
            continue
        total = sum(ctx.weight(r.tracked_id) for r in rows)
        if total <= 0:
            continue
        candidates = []
        for column, _type, category in info.columns:
            is_time = category == TIME_TYPE_CATEGORY
            if not (is_time or TENANT_COLUMN.match(column)):
                continue
            share = sum(ctx.weight(r.tracked_id) for r in rows if column in r.filter_columns) / total
            if share >= PARTITION_FILTER_SHARE:
                candidates.append((share, column, is_time))
        if not candidates:
            continue
        share, column, is_time = max(candidates)
        retention_queries = [r for r in rows if "delete" in r.access_kinds and column in r.filter_columns]
        stats = ctx.data.table_stats.get(table, {})
        deleted = stats.get("n_tup_del")
        live = stats.get("n_live_tup") or 0
        churn = deleted / live if deleted is not None and live > 0 else None
        evidence = []
        if retention_queries:
            evidence.append(f"{len(retention_queries)} tracked DELETE filters on {column} (a retention pattern)")
        if churn is not None and churn >= PARTITION_DELETE_CHURN:
            evidence.append(f"{_fmt_pct(100 * churn)} of its rows were deleted in the window")
        kind = "range" if is_time else "list or hash"
        table_q = quote_qualified(table.schema, table.name)
        sketch = (
            f"CREATE TABLE {quote_qualified(table.schema, table.name + '_partitioned')} "
            f"(LIKE {table_q} INCLUDING DEFAULTS INCLUDING CONSTRAINTS) PARTITION BY "
            + (f"RANGE ({quote_ident(column)});" if is_time else f"HASH ({quote_ident(column)});")
        )
        timescale_note = (
            f" TimescaleDB is installed: create_hypertable('{table.qualified}', '{column}', migrate_data => true) "
            "converts it in place (it locks the table while it copies)."
            if is_time and ctx.data.timescaledb_installed
            else ""
        )
        findings.append(
            _finding(
                f"partition-candidate-{table.qualified}",
                "attention" if evidence else "unknown",
                table,
                f"{table.qualified} ({_fmt_bytes(node['total_bytes'])}) is a {kind} partitioning candidate on {column}",
                f"{_fmt_pct(100 * share)} of the DB time spent on this table is in queries filtering on {column}"
                + (f"; {'; '.join(evidence)}" if evidence else "")
                + ". Partitioning lets those queries skip whole partitions, and turns retention deletes into "
                "dropping an old partition.",
                f"Sketch of the target layout (a real migration copies data in batches and swaps names):\n{sketch}\n"
                "A primary key or unique constraint on a partitioned table must include the partition key.",
                f"Partition by {kind} on {column}"
                + (", e.g. monthly ranges managed by pg_partman." if is_time else ".")
                + timescale_note,
                occurrences=_occurrences(ctx.query_samples(table)),
            )
        )
    return findings


def _denormalization_hint(ctx: _Context) -> list[dict]:
    findings = []
    for edge in ctx.data.graph["edges"]:
        if edge["kind"] == "fk_unused" or edge["share_pct"] < DENORM_SHARE_PCT:
            continue
        read_mostly = []
        for qualified in (edge["source"], edge["target"]):
            node = ctx.nodes[qualified]
            live = node["live_rows"] or 0
            writes = node["writes"]
            if node["written"]:
                continue
            if writes is None or (live > 0 and writes / live <= DENORM_MAX_WRITE_RATIO):
                read_mostly.append(qualified)
        if not read_mostly:
            continue
        source, target = _parse_table(edge["source"]), _parse_table(edge["target"])
        findings.append(
            _finding(
                f"denormalize-{source.qualified}-{target.qualified}",
                "unknown",
                source,
                f"The {source.name} ⇄ {target.name} join carries {_fmt_pct(edge['share_pct'])} of DB time",
                f"One join pair dominates the workload, and {', '.join(read_mostly)} is rarely written — the "
                "shape where a materialized view, or copying the column the queries need, can pay off.",
                f"Join columns: {', '.join(f'{a} = {b}' for a, b in edge['column_pairs'])}. "
                f"{edge['query_count']} quer{'y' if edge['query_count'] == 1 else 'ies'} behind it.",
                "Advisory only: first make sure the join itself is indexed and planned well (Explain the top "
                "queries). If it still dominates, consider a materialized view refreshed on a schedule, or "
                "duplicating the read-mostly column onto the other table, kept in sync by the application or a "
                "trigger.",
                occurrences=_occurrences(edge["top_queries"]),
            )
        )
    return findings


def _index_consolidation(ctx: _Context) -> list[dict]:
    findings = []
    if not ctx.index_evidence():
        return findings
    span = f"{ctx.data.index_window_hours:.0f}h"
    for table in ctx.data.tables:
        if ctx.is_partitioned(table) or not ctx.data.include_table(table):
            continue
        btrees = [i for i in ctx.usable_indexes(table) if i.method == "btree" and None not in i.key_columns]
        for narrow in btrees:
            if narrow.is_unique or narrow.is_primary or narrow.backs_constraint:
                continue
            for wide in btrees:
                if wide is narrow or len(wide.key_columns) <= len(narrow.key_columns):
                    continue
                if wide.key_columns[: len(narrow.key_columns)] != narrow.key_columns:
                    continue
                narrow_scans = ctx.data.index_scan_deltas.get((table, narrow.name))
                wide_scans = ctx.data.index_scan_deltas.get((table, wide.name))
                if narrow_scans is None or wide_scans is None:
                    continue
                low_usage = narrow_scans <= CONSOLIDATE_LOW_USAGE * max(wide_scans, 1)
                findings.append(
                    _finding(
                        f"consolidate-index-{table.qualified}-{narrow.name}",
                        "attention" if low_usage else "unknown",
                        table,
                        f"{narrow.name} is a prefix of {wide.name} on {table.qualified}",
                        f"{wide.name} ({', '.join(wide.key_columns)}) can serve every lookup {narrow.name} "
                        f"({', '.join(narrow.key_columns)}) does. Over the last {span}, {narrow.name} was scanned "
                        f"{narrow_scans:,} times and {wide.name} {wide_scans:,} times.",
                        f"{narrow.definition}\n{wide.definition}",
                        f"Dropping {narrow.name} saves {_fmt_bytes(narrow.size_bytes)} and one index update per "
                        "write. "
                        + (
                            "It's barely used, so the cost of moving its lookups to the wider index is small."
                            if low_usage
                            else "It is used, so its lookups will move to the slightly larger index — check "
                            "that's acceptable on a staging copy first."
                        ),
                        recommended_ddl=f"DROP INDEX CONCURRENTLY {quote_qualified(table.schema, narrow.name)};",
                    )
                )
                break
    return findings


def _write_amplification(ctx: _Context) -> list[dict]:
    findings = []
    for table, info in ctx.data.tables.items():
        stats = ctx.data.table_stats.get(table, {})
        updates = stats.get("n_tup_upd")
        live = stats.get("n_live_tup") or 0
        hot_pct = stats.get("hot_update_pct")
        indexes = ctx.usable_indexes(table)
        if updates is None or hot_pct is None or not ctx.data.include_table(table):
            continue
        if updates < WRITE_AMP_MIN_UPDATES or (live and updates / live < WRITE_AMP_MIN_UPDATE_RATIO):
            continue
        if hot_pct >= WRITE_AMP_MAX_HOT_PCT or len(indexes) < WRITE_AMP_MIN_INDEXES:
            continue
        if not ctx.table_evidence():
            continue
        fillfactor = info.fillfactor or 100
        # A partitioned table takes no storage parameters: fillfactor is set
        # per partition, so no one-line DDL for it.
        partitioned = ctx.is_partitioned(table)
        findings.append(
            _finding(
                f"write-amplification-{table.qualified}",
                "attention",
                table,
                f"{table.qualified}: {updates:,} updates, only {_fmt_pct(hot_pct)} HOT, {len(indexes)} indexes",
                "Most updates aren't HOT (heap-only), so every one of them also writes a new entry into every "
                f"index — {len(indexes)} here. That's write amplification, plus index bloat for vacuum to clean.",
                f"fillfactor={fillfactor}. Indexes: {', '.join(i.name for i in indexes)}.",
                "Leave free space on each page so updates can stay HOT: lower fillfactor (85–90 is a common "
                "start; it applies to newly written pages, a rewrite with pg_repack applies it to all), and "
                "drop indexes on columns that are updated often but rarely searched — an update is only HOT "
                "when no indexed column changes."
                + (
                    " This table is partitioned: set fillfactor on its partitions (ALTER TABLE <partition> SET "
                    "(fillfactor = 85)), and on the parent's future partitions as they're created."
                    if partitioned
                    else ""
                ),
                recommended_ddl=(
                    f"ALTER TABLE {quote_qualified(table.schema, table.name)} SET (fillfactor = 85);"
                    if fillfactor > 90 and not partitioned
                    else None
                ),
                occurrences=_occurrences(ctx.query_samples(table)),
            )
        )
    return findings


def _referenced_columns(ctx: _Context, table: Table) -> set[str] | None:
    """Every column of the table the tracked queries read, filter or join
    on — or None when that can't be said (too few queries, or some were
    extracted before column tracking was complete). Intersected with the
    table's real columns: the extraction also picks up keywords and other
    non-column words from Output expressions."""
    rows = ctx.relations_by_table.get(table, [])
    info = ctx.table_info(table)
    if info is None or len(rows) < COLD_MIN_QUERIES:
        return None
    if any((ctx.queries[r.tracked_id].extractor_version or 0) < REFERENCED_COLUMNS_MIN_VERSION for r in rows):
        return None
    used = set(ctx.join_columns_by_table.get(table, set()))
    for row in rows:
        used.update(row.referenced_columns)
        used.update(row.filter_columns)
    return used & info.column_names


def _row_columns(row: RelationRow, columns: set[str]) -> set[str]:
    return (set(row.referenced_columns) | set(row.filter_columns)) & columns


def _cold_columns(ctx: _Context) -> list[dict]:
    findings = []
    coverage = ctx.data.graph["meta"]["coverage_pct"]
    if coverage is None or coverage < COLD_MIN_COVERAGE_PCT:
        return findings
    days = max(1, round(ctx.data.graph["meta"]["hours"] / 24))
    for table, info in ctx.data.tables.items():
        if table.qualified not in ctx.nodes or len(info.columns) < COLD_MIN_COLUMNS:
            continue
        used = _referenced_columns(ctx, table)
        if not used:
            continue
        # Index keys and this table's side of every relationship (joins and
        # declared FKs) are never "cold".
        protected = {c for i in info.indexes for c in i.key_columns if c}
        for edge in ctx.data.graph["edges"]:
            for side, index in (("source", 0), ("target", 1)):
                if edge[side] == table.qualified:
                    protected.update(pair[index] for pair in edge["column_pairs"])
        cold = [name for name, _type, _cat in info.columns if name not in used and name not in protected]
        if not cold:
            continue
        findings.append(
            _finding(
                f"cold-columns-{table.qualified}",
                "unknown",
                table,
                f"{len(cold)} column{'s' if len(cold) != 1 else ''} of {table.qualified} not seen in the workload for "
                f"{days} day{'s' if days != 1 else ''}",
                f"None of the {len(ctx.relations_by_table[table])} tracked queries on this table read, filter or "
                f"join on: {', '.join(cold)}.",
                "Weak evidence: only the top 50 queries by time are tracked, so a rarely-run report or an "
                "application path that wasn't busy in this window may still use these columns. Writes "
                "(INSERT) don't count as use.",
                "Confirm with the application owners before acting. Columns nobody reads are candidates to drop, "
                "or to move to a side table so the hot rows get narrower.",
                occurrences=_occurrences(ctx.query_samples(table)),
            )
        )
    return findings


def _wide_rows(ctx: _Context) -> list[dict]:
    findings = []
    for table, info in ctx.data.tables.items():
        if table.qualified not in ctx.nodes or not info.columns:
            continue
        widths = [s.avg_width for s in info.column_stats.values() if s.avg_width is not None]
        row_width = sum(widths) if widths else None
        if (row_width or 0) < WIDE_MIN_ROW_BYTES and len(info.columns) < WIDE_MIN_COLUMNS:
            continue
        if _referenced_columns(ctx, table) is None:
            continue
        columns = info.column_names
        rows = ctx.relations_by_table[table]
        total = sum(ctx.weight(r.tracked_id) for r in rows)
        reading = [r for r in rows if _row_columns(r, columns)]
        reading_weight = sum(ctx.weight(r.tracked_id) for r in reading)
        if total <= 0 or reading_weight <= 0:
            continue
        fraction = (
            sum(ctx.weight(r.tracked_id) * len(_row_columns(r, columns)) for r in reading)
            / reading_weight
            / len(columns)
        )
        if fraction > WIDE_MAX_COLUMN_FRACTION:
            continue
        wide_columns = sorted(
            ((s.avg_width, c) for c, s in info.column_stats.items() if s.avg_width and s.avg_width >= 100),
            reverse=True,
        )[:5]
        findings.append(
            _finding(
                f"wide-row-{table.qualified}",
                "unknown",
                table,
                f"Queries on {table.qualified} read about {_fmt_pct(100 * fraction)} of its {len(info.columns)} columns",
                (f"Rows average about {row_width} bytes. " if row_width else "")
                + "Weighted by DB time, the tracked queries touch only a small part of each row, but every row "
                "read still brings the whole row into memory.",
                "Widest columns: " + (", ".join(f"{c} (~{w} B)" for w, c in wide_columns) or "not known")
                + ". Based on the top 50 tracked queries only.",
                "Consider a vertical split: keep the frequently read columns in the main table and move the rest to "
                "a 1:1 side table. Large text/jsonb values are already stored out of line by TOAST once they "
                "pass ~2 kB; lowering toast_tuple_target moves medium-sized ones out too.",
                occurrences=_occurrences(ctx.query_samples(table)),
            )
        )
    return findings


def _unused_fk_index(ctx: _Context) -> list[dict]:
    findings = []
    # "No scans, parent never changed" needs a real stretch of history on
    # both counters before it can justify dropping anything.
    if not (ctx.index_evidence() and ctx.table_evidence()):
        return findings
    span = f"{ctx.data.index_window_hours:.0f}h"
    for edge in ctx.data.graph["edges"]:
        if edge["kind"] != "fk_unused":
            continue
        for fk in edge["foreign_keys"]:
            if not fk["indexed"]:
                continue
            child, parent = _parse_table(fk["child"]), _parse_table(fk["parent"])
            if ctx.is_partitioned(child):
                continue
            fk_cols = set(fk["child_columns"])
            index = next(
                (
                    i
                    for i in ctx.usable_indexes(child)
                    if set(i.key_columns[: len(fk_cols)]) == fk_cols
                    and not (i.is_unique or i.is_primary or i.backs_constraint)
                ),
                None,
            )
            if index is None:
                continue
            scans = ctx.data.index_scan_deltas.get((child, index.name))
            parent_stats = ctx.data.table_stats.get(parent, {})
            parent_changes = [parent_stats.get("n_tup_del"), parent_stats.get("n_tup_upd")]
            if scans is None or scans > 0 or any(v is None or v > 0 for v in parent_changes):
                continue
            findings.append(
                _finding(
                    f"unused-fk-index-{child.qualified}-{index.name}",
                    "unknown",
                    child,
                    f"FK index {index.name} on {child.qualified} wasn't used in the last {span}",
                    f"No tracked query joins {child.qualified} to {parent.qualified} through {fk['name']}, "
                    f"{index.name} had no scans in {span}, and nothing was deleted or updated in "
                    f"{parent.qualified} — so the index only cost writes.",
                    f"{index.definition} ({_fmt_bytes(index.size_bytes)}).",
                    f"Review whether it's needed. It still matters if rows in {parent.qualified} are ever deleted "
                    f"or their key changes (each such change looks up {child.qualified} through it), or if a "
                    "rarely-run job joins on it. If neither applies, dropping it saves its size and one index "
                    "update per write.",
                    recommended_ddl=f"DROP INDEX CONCURRENTLY {quote_qualified(child.schema, index.name)};",
                )
            )
    return findings


def _is_skewed(stats: ColumnStats | None, live_rows: int) -> bool:
    """One value dominates: common in absolute terms, and well above both an
    even spread over the distinct values and the next most common value."""
    if stats is None or stats.top_frequency is None or stats.top_frequency < SKEW_MIN_FREQUENCY:
        return False
    distinct = stats.n_distinct
    if distinct is not None and distinct < 0:
        distinct = -distinct * live_rows  # negative = a fraction of the rows
    even_share = 1.0 / distinct if distinct and distinct > 0 else 0.0
    runner_up = stats.second_frequency or 0.0
    return stats.top_frequency >= SKEW_DOMINANCE * max(even_share, runner_up)


def _value_skew(ctx: _Context) -> list[dict]:
    findings = []
    for node in ctx.data.graph["nodes"]:
        if node["share_pct"] < SKEW_MIN_SHARE_PCT:
            continue
        table = Table(node["schema"], node["table"])
        info = ctx.table_info(table)
        if info is None:
            continue
        live_rows = node["live_rows"] or 0
        for column in node["filter_columns"]:
            stats = info.column_stats.get(column)
            if not _is_skewed(stats, live_rows):
                continue
            frequency = stats.top_frequency
            findings.append(
                _finding(
                    f"value-skew-{table.qualified}-{column}",
                    "unknown",
                    table,
                    f"One value of {table.name}.{column} covers {_fmt_pct(100 * frequency)} of rows",
                    f"Queries filter {table.qualified} on {column}, and the planner's statistics say a single "
                    f"value accounts for {_fmt_pct(100 * frequency)} of the table. The same query is cheap for "
                    "most values and expensive for that one, and a generic plan fits neither well.",
                    "From pg_stats.most_common_freqs (the value itself isn't shown here). This table carries "
                    f"{_fmt_pct(node['share_pct'])} of DB time.",
                    "If queries mostly look for the other values, a partial index that leaves the dominant value "
                    "out is smaller and faster. If the dominant value is a big tenant, list-partitioning it into "
                    "its own partition keeps its scans from touching everyone else's rows.",
                    occurrences=_occurrences(ctx.query_samples(table)),
                )
            )
    return findings


RULES = (
    _missing_fk_on_hot_join,
    _missing_join_index,
    _partitioning_candidate,
    _denormalization_hint,
    _index_consolidation,
    _write_amplification,
    _cold_columns,
    _wide_rows,
    _unused_fk_index,
    _value_skew,
)


def compute_refactoring_findings(data: RefactorInput) -> list[dict]:
    ctx = _Context(data)
    findings = []
    for rule in RULES:
        findings.extend(rule(ctx))
    return findings
