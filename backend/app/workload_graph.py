"""Pure-function assembly of the Workload Map graph (routers/workload_graph.py)
from what the plan capture stored (query_relation_access, query_join_edges),
each query's DB time, the target's foreign keys and per-table statistics.

Edges come in three kinds, which is the point of the map:
  - "fk":        joined in the workload, and a declared FK covers the join
  - "no_fk":     joined in the workload on columns no FK covers — a missing
                 constraint, or a join that needs an index nobody created
  - "fk_unused": FK declared, never joined by any tracked query — a dead
                 relationship, or an FK index paying write cost for nothing

Weights are DB time (sum of calls x mean time) of the queries behind an
edge or node within the requested window. When nothing ran in the window at
all (an idle demo database, or a collector that only just started), the
graph falls back to each query's lifetime total from pg_stat_statements and
says so in meta.weight_basis — better a correctly-shaped map weighted by
history than an empty one.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.workload_extract import Table

MAX_NODES = 400
TOP_QUERIES_PER_ITEM = 5
QUERY_TEXT_LIMIT = 200
WRITE_ACCESS_KINDS = frozenset({"insert", "update", "delete", "merge"})
SEVERITY_RANK = {"critical": 3, "attention": 2, "unknown": 1, "healthy": 0}


@dataclass
class QueryInfo:
    tracked_id: object
    queryid: int
    text: str
    window_db_time_ms: float
    window_calls: int
    lifetime_db_time_ms: float
    lifetime_calls: int
    status: str | None  # query_workload_extracts.status, None = not planned yet
    checked_at: datetime | None = None


@dataclass
class RelationRow:
    tracked_id: object
    table: Table
    access_kinds: list[str]
    filter_columns: list[str]


@dataclass
class JoinRow:
    tracked_id: object
    left: Table
    right: Table
    left_columns: list[str]
    right_columns: list[str]
    join_type: str | None
    join_strategy: str | None


@dataclass
class StatRow:
    collected_at: datetime
    total_bytes: int | None
    n_live_tup: int | None
    n_dead_tup: int | None
    seq_scan: int | None
    seq_tup_read: int | None
    idx_scan: int | None
    n_tup_ins: int | None
    n_tup_upd: int | None
    n_tup_del: int | None
    n_tup_hot_upd: int | None


_COUNTERS = ("seq_scan", "seq_tup_read", "idx_scan", "n_tup_ins", "n_tup_upd", "n_tup_del", "n_tup_hot_upd")


@dataclass
class _NodeAcc:
    table: Table
    queries: set = field(default_factory=set)
    access_kinds: set = field(default_factory=set)
    filter_columns: set = field(default_factory=set)


@dataclass
class _EdgeAcc:
    left: Table
    right: Table
    queries: set = field(default_factory=set)
    column_pairs: list = field(default_factory=list)
    join_types: set = field(default_factory=set)
    strategies: set = field(default_factory=set)
    fks: list = field(default_factory=list)


def _truncate(text: str, limit: int = QUERY_TEXT_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _counter_delta(first: int | None, last: int | None) -> int | None:
    """Growth of a cumulative counter across the window. A counter that went
    backwards was reset (pg_stat_reset, a restart after a crash): everything
    since the reset is what's known."""
    if last is None:
        return None
    if first is None:
        return None
    return last - first if last >= first else last


def fold_table_stats(
    latest: dict[Table, StatRow], earliest: dict[Table, StatRow], parent_of: dict[Table, Table]
) -> tuple[dict[Table, dict], float | None]:
    """Per logical table: size and row counts from the latest snapshot,
    counter growth between the first and last snapshot in the window, both
    summed over partitions/chunks. Also returns the window the deltas
    actually span, in hours (None with fewer than two snapshots)."""
    folded: dict[Table, dict] = {}
    span_hours = None
    for physical, last in latest.items():
        logical = parent_of.get(physical, physical)
        acc = folded.setdefault(
            logical,
            {"total_bytes": 0, "n_live_tup": 0, "n_dead_tup": 0, "children": 0, **{c: None for c in _COUNTERS}},
        )
        if physical != logical:
            acc["children"] += 1
        acc["total_bytes"] += last.total_bytes or 0
        acc["n_live_tup"] += last.n_live_tup or 0
        acc["n_dead_tup"] += last.n_dead_tup or 0
        first = earliest.get(physical)
        if first is None or first.collected_at >= last.collected_at:
            continue
        hours = (last.collected_at - first.collected_at).total_seconds() / 3600
        span_hours = hours if span_hours is None else max(span_hours, hours)
        for counter in _COUNTERS:
            delta = _counter_delta(getattr(first, counter), getattr(last, counter))
            if delta is not None:
                acc[counter] = (acc[counter] or 0) + delta
    for acc in folded.values():
        rows = acc["n_live_tup"] + acc["n_dead_tup"]
        acc["dead_pct"] = round(100.0 * acc["n_dead_tup"] / rows, 1) if rows else 0.0
        updates = acc["n_tup_upd"]
        acc["hot_update_pct"] = (
            round(100.0 * (acc["n_tup_hot_upd"] or 0) / updates, 1) if updates else None
        )
        writes = [acc[c] for c in ("n_tup_ins", "n_tup_upd", "n_tup_del") if acc[c] is not None]
        acc["writes"] = sum(writes) if writes else None
    return folded, span_hours


def _query_weight(query: QueryInfo, basis: str) -> float:
    return query.window_db_time_ms if basis == "window" else query.lifetime_db_time_ms


def _query_calls(query: QueryInfo, basis: str) -> int:
    return query.window_calls if basis == "window" else query.lifetime_calls


def _top_queries(query_ids, queries: dict, basis: str) -> list[dict]:
    ranked = sorted((queries[q] for q in query_ids), key=lambda q: _query_weight(q, basis), reverse=True)
    return [
        {
            "queryid": q.queryid,
            "query": _truncate(q.text),
            "db_time_ms": round(_query_weight(q, basis), 3),
            "calls": _query_calls(q, basis),
        }
        for q in ranked[:TOP_QUERIES_PER_ITEM]
    ]


def build_workload_graph(
    *,
    hours: int,
    queries: list[QueryInfo],
    relations: list[RelationRow],
    joins: list[JoinRow],
    foreign_keys: list[dict],
    index_leading_columns: set[tuple[Table, str]],
    table_stats: dict[Table, dict],
    stats_window_hours: float | None,
    severities: dict[Table, str],
    include_table,
    max_nodes: int = MAX_NODES,
) -> dict:
    by_id = {q.tracked_id: q for q in queries}
    basis = "window" if any(q.window_db_time_ms > 0 for q in queries) else "lifetime"
    total_db_time = sum(_query_weight(q, basis) for q in queries)
    mapped_db_time = sum(_query_weight(q, basis) for q in queries if q.status == "ok")

    status_counts = {"ok": 0, "needs_generic_plan": 0, "explain_failed": 0, "not_planned_yet": 0}
    for q in queries:
        status_counts[q.status if q.status in status_counts else "not_planned_yet"] += 1

    nodes: dict[Table, _NodeAcc] = {}
    for row in relations:
        if row.tracked_id not in by_id or not include_table(row.table):
            continue
        acc = nodes.setdefault(row.table, _NodeAcc(row.table))
        acc.queries.add(row.tracked_id)
        acc.access_kinds.update(row.access_kinds)
        acc.filter_columns.update(row.filter_columns)

    edges: dict[tuple[Table, Table], _EdgeAcc] = {}
    for row in joins:
        if row.tracked_id not in by_id or row.left == row.right:
            continue
        if not (include_table(row.left) and include_table(row.right)):
            continue
        acc = edges.setdefault((row.left, row.right), _EdgeAcc(row.left, row.right))
        acc.queries.add(row.tracked_id)
        for pair in zip(row.left_columns, row.right_columns, strict=False):
            if pair not in acc.column_pairs:
                acc.column_pairs.append(pair)
        if row.join_type:
            acc.join_types.add(row.join_type)
        if row.join_strategy:
            acc.strategies.add(row.join_strategy)
        for table in (row.left, row.right):
            nodes.setdefault(table, _NodeAcc(table)).queries.add(row.tracked_id)

    unused_fks = []
    for fk in foreign_keys:
        child, parent = fk["child"], fk["parent"]
        if child == parent or not (include_table(child) and include_table(parent)):
            continue
        key = (child, parent) if child <= parent else (parent, child)
        pairs = list(zip(fk["child_columns"], fk["parent_columns"], strict=False))
        oriented = set(pairs) if key[0] == child else {(p, c) for c, p in pairs}
        workload_edge = edges.get(key)
        if workload_edge is not None and oriented <= set(workload_edge.column_pairs):
            workload_edge.fks.append(fk)
        else:
            unused_fks.append((key, fk))
        nodes.setdefault(child, _NodeAcc(child))
        nodes.setdefault(parent, _NodeAcc(parent))

    # Cap the graph for very large schemas: keep the tables carrying the most
    # DB time, then the biggest.
    def node_weight(acc: _NodeAcc) -> tuple:
        return (
            sum(_query_weight(by_id[q], basis) for q in acc.queries),
            table_stats.get(acc.table, {}).get("total_bytes") or 0,
        )

    truncated = 0
    if len(nodes) > max_nodes:
        kept = sorted(nodes.values(), key=node_weight, reverse=True)[:max_nodes]
        truncated = len(nodes) - max_nodes
        nodes = {acc.table: acc for acc in kept}

    def side_indexed(table: Table, columns) -> bool:
        return any((table, column) in index_leading_columns for column in columns)

    def fk_payload(fk: dict) -> dict:
        return {
            "name": fk["name"],
            "child": fk["child"].qualified,
            "child_columns": fk["child_columns"],
            "parent": fk["parent"].qualified,
            "parent_columns": fk["parent_columns"],
            "indexed": fk["indexed"],
        }

    edge_payloads = []
    for (left, right), acc in edges.items():
        if left not in nodes or right not in nodes:
            continue
        db_time = sum(_query_weight(by_id[q], basis) for q in acc.queries)
        edge_payloads.append(
            {
                "id": f"join:{left.qualified}|{right.qualified}",
                "source": left.qualified,
                "target": right.qualified,
                "kind": "fk" if acc.fks else "no_fk",
                "db_time_ms": round(db_time, 3),
                "share_pct": round(100.0 * db_time / total_db_time, 2) if total_db_time else 0.0,
                "calls": sum(_query_calls(by_id[q], basis) for q in acc.queries),
                "query_count": len(acc.queries),
                "column_pairs": [list(pair) for pair in acc.column_pairs],
                "join_types": sorted(acc.join_types),
                "join_strategies": sorted(acc.strategies),
                "source_indexed": side_indexed(left, [p[0] for p in acc.column_pairs]),
                "target_indexed": side_indexed(right, [p[1] for p in acc.column_pairs]),
                "foreign_keys": [fk_payload(fk) for fk in acc.fks],
                "top_queries": _top_queries(acc.queries, by_id, basis),
            }
        )
    for (left, right), fk in unused_fks:
        if left not in nodes or right not in nodes:
            continue
        child_is_left = fk["child"] == left
        edge_payloads.append(
            {
                "id": f"fk:{fk['child'].qualified}|{fk['name']}",
                "source": left.qualified,
                "target": right.qualified,
                "kind": "fk_unused",
                "db_time_ms": 0.0,
                "share_pct": 0.0,
                "calls": 0,
                "query_count": 0,
                "column_pairs": [
                    [c, p] if child_is_left else [p, c]
                    for c, p in zip(fk["child_columns"], fk["parent_columns"], strict=False)
                ],
                "join_types": [],
                "join_strategies": [],
                "source_indexed": fk["indexed"] if child_is_left else True,
                "target_indexed": True if child_is_left else fk["indexed"],
                "foreign_keys": [fk_payload(fk)],
                "top_queries": [],
            }
        )
    edge_payloads.sort(key=lambda e: e["db_time_ms"], reverse=True)

    node_payloads = []
    for table, acc in nodes.items():
        stats = table_stats.get(table, {})
        db_time = sum(_query_weight(by_id[q], basis) for q in acc.queries)
        node_payloads.append(
            {
                "id": table.qualified,
                "schema": table.schema,
                "table": table.name,
                "in_workload": bool(acc.queries),
                "db_time_ms": round(db_time, 3),
                "share_pct": round(100.0 * db_time / total_db_time, 2) if total_db_time else 0.0,
                "query_count": len(acc.queries),
                "access_kinds": sorted(acc.access_kinds),
                "written": bool(acc.access_kinds & WRITE_ACCESS_KINDS),
                "filter_columns": sorted(acc.filter_columns),
                "severity": severities.get(table),
                "total_bytes": stats.get("total_bytes"),
                "live_rows": stats.get("n_live_tup"),
                "dead_pct": stats.get("dead_pct"),
                "seq_scans": stats.get("seq_scan"),
                "seq_tup_read": stats.get("seq_tup_read"),
                "idx_scans": stats.get("idx_scan"),
                "writes": stats.get("writes"),
                "hot_update_pct": stats.get("hot_update_pct"),
                "folded_children": stats.get("children", 0),
                "top_queries": _top_queries(acc.queries, by_id, basis),
            }
        )
    node_payloads.sort(key=lambda n: (n["schema"], n["table"]))

    kind_counts = {"fk": 0, "no_fk": 0, "fk_unused": 0}
    for edge in edge_payloads:
        kind_counts[edge["kind"]] += 1
    checked = [q.checked_at for q in queries if q.checked_at is not None]

    return {
        "nodes": node_payloads,
        "edges": edge_payloads,
        "meta": {
            "hours": hours,
            "weight_basis": basis,
            "tracked_queries": len(queries),
            "query_status_counts": status_counts,
            "total_db_time_ms": round(total_db_time, 3),
            "mapped_db_time_ms": round(mapped_db_time, 3),
            "coverage_pct": round(100.0 * mapped_db_time / total_db_time, 1) if total_db_time else None,
            "edge_counts": kind_counts,
            "truncated_nodes": truncated,
            "stats_window_hours": round(stats_window_hours, 2) if stats_window_hours is not None else None,
            "last_captured_at": max(checked) if checked else None,
        },
    }


def worst_severity(severities) -> str | None:
    best = None
    for severity in severities:
        if severity not in SEVERITY_RANK:
            continue
        if best is None or SEVERITY_RANK[severity] > SEVERITY_RANK[best]:
            best = severity
    return best
