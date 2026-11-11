from datetime import datetime, timedelta, timezone

from app import scheduler
from app.schemas import WorkloadGraphResponse
from app.workload_catalog import is_user_schema
from app.workload_extract import Table, extract_workload, plan_relations, unquote_identifier
from app.workload_graph import (
    JoinRow,
    QueryInfo,
    RelationRow,
    StatRow,
    build_workload_graph,
    fold_table_stats,
    worst_severity,
)

ORDERS = Table("public", "orders")
CUSTOMERS = Table("public", "customers")
ITEMS = Table("public", "order_items")
REFUNDS = Table("public", "refunds")


def scan(node_type, relation, alias=None, schema="public", **extra):
    return {"Node Type": node_type, "Schema": schema, "Relation Name": relation, "Alias": alias or relation, **extra}


# --- Extraction from EXPLAIN (VERBOSE, FORMAT JSON) ---------------------------

HASH_JOIN_PLAN = {
    "Node Type": "Hash Join",
    "Join Type": "Inner",
    "Hash Cond": "(o.customer_id = c.id)",
    "Plans": [
        scan("Seq Scan", "orders", "o", Filter="(o.created_at > (now() - '30 days'::interval))"),
        {"Node Type": "Hash", "Plans": [scan("Seq Scan", "customers", "c")]},
    ],
}


def test_hash_join_gives_one_edge_with_ordered_columns_and_strategy():
    extract = extract_workload(HASH_JOIN_PLAN)
    assert len(extract.joins) == 1
    join = extract.joins[0]
    # Ordered by (table, column): customers < orders.
    assert (join.left, join.right) == (CUSTOMERS, ORDERS)
    assert (join.left_columns, join.right_columns) == (["id"], ["customer_id"])
    assert (join.join_type, join.join_strategy) == ("Inner", "Hash Join")


def test_scan_filters_become_filter_columns_and_access_kinds():
    extract = extract_workload(HASH_JOIN_PLAN)
    by_table = {r.table: r for r in extract.relations}
    assert by_table[ORDERS].filter_columns == {"created_at"}
    assert by_table[ORDERS].access_kinds == {"seq"}
    assert by_table[CUSTOMERS].filter_columns == set()


def test_nested_loop_join_pushed_into_inner_index_cond():
    plan = {
        "Node Type": "Nested Loop",
        "Join Type": "Left",
        "Plans": [
            scan("Seq Scan", "orders", "o"),
            scan("Index Scan", "order_items", "oi", **{"Index Cond": "(oi.order_id = o.id)", "Index Name": "x"}),
        ],
    }
    join = extract_workload(plan).joins[0]
    assert (join.left, join.right, join.left_columns, join.right_columns) == (ITEMS, ORDERS, ["order_id"], ["id"])
    assert (join.join_type, join.join_strategy) == ("Left", "Nested Loop")
    items = next(r for r in extract_workload(plan).relations if r.table == ITEMS)
    # A join column isn't also reported as a filter column.
    assert items.filter_columns == set()
    assert items.access_kinds == {"index"}


def test_casts_quoted_identifiers_and_multi_column_joins():
    plan = {
        "Node Type": "Merge Join",
        "Join Type": "Inner",
        "Merge Cond": '(((a."Tenant")::text = (b.tenant)::text) AND (a.id = b.a_id))',
        "Plans": [scan("Seq Scan", "Accounts", "a"), scan("Seq Scan", "bills", "b")],
    }
    join = extract_workload(plan).joins[0]
    assert join.left == Table("public", "Accounts")
    assert join.left_columns == ["Tenant", "id"] and join.right_columns == ["tenant", "a_id"]
    assert unquote_identifier('"we""ird"') == 'we"ird'


def test_literals_functions_and_same_alias_comparisons_are_not_joins():
    plan = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Hash Cond": "(lower(c.email) = lower(r.email))",
        "Join Filter": "((c.note = 'x.y = z.w') AND (c.id = c.parent_id))",
        "Plans": [scan("Seq Scan", "customers", "c"), scan("Seq Scan", "refunds", "r")],
    }
    assert extract_workload(plan).joins == []


def test_partition_children_fold_onto_parent_and_parent_alias_resolves():
    # Hash Cond names the partitioned parent's alias "o"; only its
    # partitions o_1/o_2 are scanned.
    plan = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Hash Cond": "(o.customer_id = c.id)",
        "Plans": [
            {
                "Node Type": "Append",
                "Plans": [scan("Seq Scan", "orders_2025", "o_1"), scan("Seq Scan", "orders_2026", "o_2")],
            },
            {"Node Type": "Hash", "Plans": [scan("Seq Scan", "customers", "c")]},
        ],
    }
    parents = {Table("public", "orders_2025"): ORDERS, Table("public", "orders_2026"): ORDERS}
    assert plan_relations(plan) == {Table("public", "orders_2025"), Table("public", "orders_2026"), CUSTOMERS}
    extract = extract_workload(plan, parents)
    assert {r.table for r in extract.relations} == {ORDERS, CUSTOMERS}
    assert [(j.left, j.right) for j in extract.joins] == [(CUSTOMERS, ORDERS)]


def test_hypertable_name_resolves_through_its_chunks():
    readings = Table("public", "sensor_readings")
    chunk = Table("_timescaledb_internal", "_hyper_1_3_chunk")
    plan = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Hash Cond": "(sensor_readings.device_id = d.device_id)",
        "Plans": [
            scan("Seq Scan", "_hyper_1_3_chunk", schema="_timescaledb_internal"),
            {"Node Type": "Hash", "Plans": [scan("Seq Scan", "devices", "d")]},
        ],
    }
    extract = extract_workload(plan, {chunk: readings})
    assert {r.table for r in extract.relations} == {readings, Table("public", "devices")}
    assert extract.joins[0].right == readings


def test_cte_and_subquery_aliases_are_ignored():
    plan = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Hash Cond": "(recent.customer_id = c.id)",
        "Plans": [{"Node Type": "CTE Scan", "Alias": "recent"}, scan("Seq Scan", "customers", "c")],
    }
    assert extract_workload(plan).joins == []


def test_modify_table_records_write_access():
    plan = {
        "Node Type": "ModifyTable",
        "Operation": "Update",
        "Schema": "public",
        "Relation Name": "orders",
        "Alias": "orders",
        "Plans": [scan("Seq Scan", "orders", Filter="(orders.id = 42)")],
    }
    relation = extract_workload(plan).relations[0]
    assert relation.access_kinds == {"update", "seq"}
    assert relation.filter_columns == {"id"}


def test_same_join_inside_and_outside_a_join_node_still_digests():
    # A correlated subquery in the select list (its Index Cond hangs off the
    # Agg, outside any join node) next to a join on the same FK: the same
    # pair/columns with join_type None and "Inner". Sorting those used to
    # raise TypeError and abort the whole capture.
    plan = {
        "Node Type": "Aggregate",
        "Plans": [
            {
                "Node Type": "Hash Join",
                "Join Type": "Inner",
                "Hash Cond": "(o.customer_id = c.id)",
                "Plans": [scan("Seq Scan", "orders", "o"), scan("Seq Scan", "customers", "c")],
            },
            scan("Index Scan", "orders", "x", **{"Index Cond": "(x.customer_id = c.id)", "Index Name": "i"}),
        ],
    }
    extract = extract_workload(plan)
    assert sorted(j.join_type or "" for j in extract.joins) == ["", "Inner"]
    assert len(extract.digest()) == 64


def test_arithmetic_on_the_right_side_is_not_a_join():
    plan = {
        "Node Type": "Nested Loop",
        "Join Type": "Inner",
        "Join Filter": "(r.day = (d.day + 1))",
        "Plans": [scan("Seq Scan", "readings", "r"), scan("Seq Scan", "days", "d")],
    }
    assert extract_workload(plan).joins == []


def test_qualified_quoted_and_typmod_casts_still_join():
    plan = {
        "Node Type": "Hash Join",
        "Join Type": "Inner",
        "Hash Cond": '(((a.code)::public.citext = (b.code)::"Code") AND ((a.n)::character varying(20) = b.n))',
        "Plans": [scan("Seq Scan", "a"), scan("Seq Scan", "b")],
    }
    join = extract_workload(plan).joins[0]
    assert (join.left_columns, join.right_columns) == (["code", "n"], ["code", "n"])


def test_having_filter_is_not_a_table_filter_column():
    plan = {
        "Node Type": "Aggregate",
        "Filter": "(count(o.id) > 5)",
        "Plans": [scan("Seq Scan", "orders", "o")],
    }
    assert extract_workload(plan).relations[0].filter_columns == set()


def test_digest_is_stable_and_changes_with_the_extraction():
    first = extract_workload(HASH_JOIN_PLAN).digest()
    assert first == extract_workload(HASH_JOIN_PLAN).digest()
    changed = dict(HASH_JOIN_PLAN, **{"Join Type": "Left"})
    assert extract_workload(changed).digest() != first


def test_system_and_timescale_internal_schemas_are_not_user_schemas():
    assert is_user_schema("public") and is_user_schema("sales")
    for schema in ("pg_catalog", "information_schema", "pg_toast", "_timescaledb_internal", "timescaledb_information"):
        assert not is_user_schema(schema)


# --- Graph assembly -----------------------------------------------------------


def query(tracked_id, window_ms=0.0, lifetime_ms=0.0, status="ok"):
    return QueryInfo(tracked_id, tracked_id * 100, f"SELECT {tracked_id}", window_ms, 1, lifetime_ms, 5, status)


def fk(name, child, child_cols, parent, parent_cols, indexed=False):
    return {
        "name": name,
        "child": child,
        "child_columns": child_cols,
        "parent": parent,
        "parent_columns": parent_cols,
        "indexed": indexed,
    }


def graph(queries, relations=(), joins=(), fks=(), leading=frozenset(), include=lambda t: True):
    return build_workload_graph(
        hours=168,
        queries=list(queries),
        relations=list(relations),
        joins=list(joins),
        foreign_keys=list(fks),
        index_leading_columns=set(leading),
        table_stats={},
        stats_window_hours=None,
        severities={ORDERS: "attention"},
        include_table=include,
    )


def test_edges_are_classified_fk_no_fk_and_fk_unused():
    result = graph(
        queries=[query(1, window_ms=60.0), query(2, window_ms=40.0)],
        joins=[
            JoinRow(1, CUSTOMERS, ORDERS, ["id"], ["customer_id"], "Inner", "Hash Join"),
            JoinRow(2, ORDERS, REFUNDS, ["id"], ["order_ref"], "Inner", "Hash Join"),
        ],
        fks=[
            fk("orders_customer_id_fkey", ORDERS, ["customer_id"], CUSTOMERS, ["id"]),
            fk("order_items_order_id_fkey", ITEMS, ["order_id"], ORDERS, ["id"], indexed=True),
        ],
        leading={(ORDERS, "id")},
    )
    edges = {e["kind"]: e for e in result["edges"]}
    assert set(edges) == {"fk", "no_fk", "fk_unused"}
    assert edges["fk"]["foreign_keys"][0]["indexed"] is False  # used, but unindexed
    assert edges["fk"]["share_pct"] == 60.0
    assert edges["no_fk"]["source_indexed"] is True and edges["no_fk"]["target_indexed"] is False
    assert edges["fk_unused"]["query_count"] == 0
    assert result["meta"]["edge_counts"] == {"fk": 1, "no_fk": 1, "fk_unused": 1}
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["public.order_items"]["in_workload"] is False
    assert nodes["public.orders"]["severity"] == "attention"


def test_fk_on_other_columns_than_the_join_does_not_count_as_used():
    result = graph(
        queries=[query(1, window_ms=10.0)],
        joins=[JoinRow(1, CUSTOMERS, ORDERS, ["email"], ["contact_email"], "Inner", "Hash Join")],
        fks=[fk("orders_customer_id_fkey", ORDERS, ["customer_id"], CUSTOMERS, ["id"])],
    )
    assert sorted(e["kind"] for e in result["edges"]) == ["fk_unused", "no_fk"]


def test_coverage_and_lifetime_fallback_when_nothing_ran_in_window():
    result = graph(
        queries=[query(1, lifetime_ms=75.0), query(2, lifetime_ms=25.0, status="needs_generic_plan")],
        relations=[RelationRow(1, ORDERS, ["seq"], [])],
    )
    meta = result["meta"]
    assert meta["weight_basis"] == "lifetime"
    assert meta["coverage_pct"] == 75.0
    assert meta["query_status_counts"]["needs_generic_plan"] == 1


def test_schema_filter_and_self_joins_are_excluded():
    other = Table("audit", "log")
    result = graph(
        queries=[query(1, window_ms=5.0)],
        relations=[RelationRow(1, ORDERS, ["seq"], []), RelationRow(1, other, ["seq"], [])],
        joins=[
            JoinRow(1, ORDERS, ORDERS, ["id"], ["parent_id"], "Inner", "Hash Join"),
            JoinRow(1, other, ORDERS, ["order_id"], ["id"], "Inner", "Hash Join"),
        ],
        include=lambda t: t.schema == "public",
    )
    assert [n["id"] for n in result["nodes"]] == ["public.orders"]
    assert result["edges"] == []


def test_node_cap_keeps_heaviest_tables():
    tables = [Table("public", f"t{i}") for i in range(5)]
    result = build_workload_graph(
        hours=24,
        queries=[query(i, window_ms=float(i + 1)) for i in range(5)],
        relations=[RelationRow(i, tables[i], ["seq"], []) for i in range(5)],
        joins=[],
        foreign_keys=[],
        index_leading_columns=set(),
        table_stats={},
        stats_window_hours=None,
        severities={},
        include_table=lambda t: True,
        max_nodes=2,
    )
    assert [n["id"] for n in result["nodes"]] == ["public.t3", "public.t4"]
    assert result["meta"]["truncated_nodes"] == 3


def test_table_stats_fold_partitions_and_handle_counter_resets():
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    start = now - timedelta(hours=6)

    def stat(at, size, seq_tup_read, ins):
        return StatRow(at, size, 90, 10, 1, seq_tup_read, 0, ins, 0, 0, 0)

    part_a, part_b = Table("public", "orders_2025"), Table("public", "orders_2026")
    latest = {part_a: stat(now, 100, 500, 30), part_b: stat(now, 50, 20, 5)}
    earliest = {part_a: stat(start, 90, 100, 10), part_b: stat(start, 40, 80, 1)}
    folded, span = fold_table_stats(latest, earliest, {part_a: ORDERS, part_b: ORDERS})
    orders = folded[ORDERS]
    assert orders["total_bytes"] == 150 and orders["children"] == 2
    # part_b's seq_tup_read went backwards (stats reset): count what's known since.
    assert orders["seq_tup_read"] == 400 + 20
    assert orders["writes"] == 20 + 4
    assert orders["dead_pct"] == 10.0
    assert span == 6.0


def test_graph_payload_matches_the_response_model():
    result = graph(
        queries=[query(1, window_ms=60.0)],
        relations=[RelationRow(1, ORDERS, ["seq"], ["created_at"])],
        joins=[JoinRow(1, CUSTOMERS, ORDERS, ["id"], ["customer_id"], "Inner", "Hash Join")],
        fks=[fk("orders_customer_id_fkey", ORDERS, ["customer_id"], CUSTOMERS, ["id"])],
    )
    dumped = WorkloadGraphResponse.model_validate(result).model_dump(by_alias=True)
    assert dumped["nodes"][0]["schema"] == "public"
    assert dumped["edges"][0]["foreign_keys"][0]["child"] == "public.orders"


def test_worst_severity_ignores_unknown_labels():
    assert worst_severity(["healthy", "critical", "attention"]) == "critical"
    assert worst_severity(["bogus"]) is None


# --- Capture job: storing extractions ----------------------------------------


class RecordingCursor:
    def __init__(self, existing=None):
        self.existing = existing
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))

    def executemany(self, sql, rows):
        self.statements.append(("many", " ".join(sql.split()), list(rows)))

    def fetchone(self):
        return self.existing


def test_unchanged_extraction_only_touches_checked_at():
    extract = extract_workload(HASH_JOIN_PLAN)
    cur = RecordingCursor(existing=("ok", extract.digest()))
    scheduler._store_workload_extracts(cur, [("q1", "ok", None, extract)])
    assert cur.statements[-1].startswith("UPDATE query_workload_extracts SET checked_at")
    assert not any(isinstance(s, tuple) for s in cur.statements)


def test_transient_explain_failure_keeps_the_last_good_extraction():
    cur = RecordingCursor(existing=("ok", "abc"))
    scheduler._store_workload_extracts(cur, [("q1", "explain_failed", "lock timeout", None)])
    assert cur.statements[-1].startswith("UPDATE query_workload_extracts")
    assert not any("DELETE" in s for s in cur.statements if isinstance(s, str))


def test_changed_extraction_replaces_child_rows():
    extract = extract_workload(HASH_JOIN_PLAN)
    cur = RecordingCursor(existing=("ok", "old-hash"))
    scheduler._store_workload_extracts(cur, [("q1", "ok", None, extract)])
    flat = [s if isinstance(s, str) else s[1] for s in cur.statements]
    assert any(s.startswith("INSERT INTO query_workload_extracts") for s in flat)
    assert any(s.startswith("DELETE FROM query_join_edges") for s in flat)
    joins = next(s for s in cur.statements if isinstance(s, tuple) and "query_join_edges" in s[1])
    assert joins[2] == [("q1", "public", "customers", "public", "orders", ["id"], ["customer_id"], "Inner", "Hash Join")]
