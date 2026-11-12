from app.refactoring_advisor import ColumnStats, IndexInfo, RefactorInput, TableInfo, compute_refactoring_findings
from app.schemas import IndexFinding, WhatIfCandidateResult
from app.what_if import (
    change_pct,
    cost_explain_sql,
    hypothetical_index_sql,
    plan_total_cost,
    plan_uses_index,
    summarize_candidate,
)
from app.workload_extract import Table, extract_workload
from app.workload_graph import JoinRow, QueryInfo, RelationRow, build_workload_graph

ORDERS = Table("public", "orders")
CUSTOMERS = Table("public", "customers")
REFUNDS = Table("public", "refunds")
AUDIT = Table("public", "audit_log")
MB = 1024 * 1024


def query(tracked_id, ms, version=3):
    return QueryInfo(tracked_id, 1000 + tracked_id, f"SELECT q{tracked_id}", ms, 10, ms, 10, "ok", extractor_version=version)


def index(name, cols, unique=False, primary=False, size=8192, valid=True, partial=False, constraint=False):
    return IndexInfo(name, "btree", cols, unique, primary, valid, partial, constraint or primary, size, f"CREATE INDEX {name}")


def table_info(columns, indexes=(), relkind="r", fillfactor=None, stats=None, hypertable=False):
    info = TableInfo(relkind, fillfactor, hypertable)
    info.columns = [(name, typ, cat) for name, typ, cat in columns]
    info.indexes = list(indexes)
    info.column_stats = stats or {}
    return info


def run(
    queries,
    relations=(),
    joins=(),
    fks=(),
    table_stats=None,
    tables=None,
    leading=(),
    deltas=None,
    min_mb=1,
    window_hours=48.0,
):
    table_stats = table_stats or {}
    graph = build_workload_graph(
        hours=168,
        queries=queries,
        relations=list(relations),
        joins=list(joins),
        foreign_keys=list(fks),
        index_leading_columns=set(leading),
        table_stats=table_stats,
        stats_window_hours=window_hours,
        severities={},
        include_table=lambda t: True,
    )
    findings = compute_refactoring_findings(
        RefactorInput(
            graph=graph,
            queries=queries,
            relations=list(relations),
            joins=list(joins),
            table_stats=table_stats,
            tables=tables or {},
            index_scan_deltas=deltas or {},
            index_window_hours=window_hours if deltas is not None else None,
            partition_min_bytes=min_mb * MB,
        )
    )
    for finding in findings:
        IndexFinding(**finding)  # every finding fits the shared card shape
    return {f["id"]: f for f in findings}


def stats(total_bytes=10 * MB, live=100_000, **counters):
    base = {"total_bytes": total_bytes, "n_live_tup": live, "n_dead_tup": 0, "dead_pct": 0.0, "children": 0}
    for key in ("seq_scan", "seq_tup_read", "idx_scan", "n_tup_ins", "n_tup_upd", "n_tup_del", "n_tup_hot_upd"):
        base[key] = counters.get(key)
    base["hot_update_pct"] = counters.get("hot_update_pct")
    base["writes"] = counters.get("writes")
    return base


ORDERS_COLUMNS = [("id", "bigint", "N"), ("customer_id", "bigint", "N"), ("total", "numeric", "N"), ("created_at", "timestamptz", "D")]
REFUNDS_COLUMNS = [("id", "bigint", "N"), ("order_ref", "bigint", "N"), ("amount", "numeric", "N")]


def test_hot_join_without_fk_suggests_a_two_step_fk_and_a_costable_index():
    findings = run(
        queries=[query(1, 80.0), query(2, 20.0)],
        relations=[RelationRow(1, REFUNDS, ["seq"], []), RelationRow(1, ORDERS, ["index"], [])],
        joins=[JoinRow(1, ORDERS, REFUNDS, ["id"], ["order_ref"], "Inner", "Nested Loop")],
        table_stats={ORDERS: stats(), REFUNDS: stats()},
        tables={
            ORDERS: table_info(ORDERS_COLUMNS, [index("orders_pkey", ["id"], unique=True, primary=True)]),
            REFUNDS: table_info(REFUNDS_COLUMNS, [index("refunds_pkey", ["id"], unique=True, primary=True)]),
        },
        leading={(ORDERS, "id")},
    )
    fk = findings["missing-fk-join-public.refunds-order_ref"]
    assert "NOT VALID" in fk["recommended_ddl"] and "VALIDATE CONSTRAINT" in fk["recommended_ddl"]
    assert "REFERENCES public.orders (id)" in fk["recommended_ddl"]
    assert fk["occurrences"][0]["query"] == "SELECT q1"
    join_index = findings["missing-join-index-public.refunds-order_ref"]
    assert join_index["index_candidate"] == {"schema_name": "public", "table_name": "refunds", "columns": ["order_ref"]}
    assert join_index["recommended_ddl"].startswith("CREATE INDEX CONCURRENTLY")


def test_join_on_non_key_columns_is_flagged_without_fk_ddl():
    findings = run(
        queries=[query(1, 100.0)],
        joins=[JoinRow(1, ORDERS, REFUNDS, ["total"], ["amount"], "Inner", "Hash Join")],
        table_stats={ORDERS: stats(), REFUNDS: stats()},
        tables={ORDERS: table_info(ORDERS_COLUMNS), REFUNDS: table_info(REFUNDS_COLUMNS)},
    )
    finding = findings["missing-fk-join-public.orders-public.refunds-total"]
    assert finding["severity"] == "unknown" and finding.get("recommended_ddl") is None


def test_partition_candidate_on_time_filtered_table_with_retention_delete():
    audit_columns = [("id", "bigint", "N"), ("created_at", "timestamptz", "D"), ("actor", "text", "S")]
    findings = run(
        queries=[query(1, 60.0), query(2, 30.0), query(3, 10.0)],
        relations=[
            RelationRow(1, AUDIT, ["index"], ["created_at"]),
            RelationRow(2, AUDIT, ["delete", "index"], ["created_at"]),
            RelationRow(3, AUDIT, ["seq"], ["actor"]),
        ],
        table_stats={AUDIT: stats(total_bytes=50 * MB, n_tup_del=5000)},
        tables={AUDIT: table_info(audit_columns)},
        min_mb=10,
    )
    finding = findings["partition-candidate-public.audit_log"]
    assert finding["severity"] == "attention"
    assert "90.0%" in finding["summary"] and "retention" in finding["summary"]
    assert "PARTITION BY RANGE (created_at)" in finding["detail"]


def test_partition_rule_respects_size_floor_and_skips_already_partitioned():
    audit_columns = [("created_at", "timestamptz", "D")]
    rows = [RelationRow(1, AUDIT, ["seq"], ["created_at"]), RelationRow(2, AUDIT, ["seq"], ["created_at"])]
    small = run(
        queries=[query(1, 50.0), query(2, 50.0)],
        relations=rows,
        table_stats={AUDIT: stats(total_bytes=5 * MB)},
        tables={AUDIT: table_info(audit_columns)},
        min_mb=10,
    )
    partitioned = run(
        queries=[query(1, 50.0), query(2, 50.0)],
        relations=rows,
        table_stats={AUDIT: stats(total_bytes=50 * MB)},
        tables={AUDIT: table_info(audit_columns, relkind="p")},
        min_mb=10,
    )
    assert not any(f.startswith("partition-candidate") for f in small)
    assert not any(f.startswith("partition-candidate") for f in partitioned)


def test_index_consolidation_uses_window_scans():
    tables = {
        ORDERS: table_info(
            ORDERS_COLUMNS,
            [index("idx_orders_customer", ["customer_id"], size=4 * MB), index("idx_orders_customer_created", ["customer_id", "created_at"])],
        )
    }
    deltas = {(ORDERS, "idx_orders_customer"): 3, (ORDERS, "idx_orders_customer_created"): 900}
    finding = run(queries=[query(1, 1.0)], table_stats={ORDERS: stats()}, tables=tables, deltas=deltas)[
        "consolidate-index-public.orders-idx_orders_customer"
    ]
    assert finding["severity"] == "attention"
    assert finding["recommended_ddl"] == "DROP INDEX CONCURRENTLY public.idx_orders_customer;"
    # Without window data the rule stays quiet.
    assert "consolidate-index-public.orders-idx_orders_customer" not in run(
        queries=[query(1, 1.0)], table_stats={ORDERS: stats()}, tables=tables
    )


def test_write_amplification_needs_updates_low_hot_ratio_and_many_indexes():
    indexes = [index(f"i{n}", [c]) for n, c in enumerate(["id", "customer_id", "total", "created_at"])]
    findings = run(
        queries=[query(1, 1.0)],
        table_stats={ORDERS: stats(live=20_000, n_tup_upd=50_000, hot_update_pct=12.0)},
        tables={ORDERS: table_info(ORDERS_COLUMNS, indexes)},
    )
    assert "fillfactor = 85" in findings["write-amplification-public.orders"]["recommended_ddl"]


def test_cold_columns_need_enough_v2_queries_and_coverage():
    columns = ORDERS_COLUMNS + [("legacy_code", "text", "S"), ("notes", "text", "S")]
    relations = [RelationRow(i, ORDERS, ["seq"], ["created_at"], ["id", "total"]) for i in (1, 2, 3)]
    tables = {ORDERS: table_info(columns, [index("orders_pkey", ["id"], primary=True)])}
    findings = run(queries=[query(i, 10.0) for i in (1, 2, 3)], relations=relations, tables=tables)
    finding = findings["cold-columns-public.orders"]
    assert "customer_id" in finding["summary"] and "legacy_code" in finding["summary"]
    assert "created_at" not in finding["summary"]
    # A query extracted before column tracking (v1) makes the evidence incomplete.
    stale = run(queries=[query(1, 10.0, version=1), query(2, 10.0), query(3, 10.0)], relations=relations, tables=tables)
    assert "cold-columns-public.orders" not in stale


def test_unused_fk_index_only_when_index_idle_and_parent_unchanged():
    fk = {
        "name": "notes_customer_fkey",
        "child": Table("public", "notes"),
        "parent": CUSTOMERS,
        "child_columns": ["customer_id"],
        "parent_columns": ["id"],
        "indexed": True,
    }
    notes = Table("public", "notes")
    tables = {notes: table_info([("id", "bigint", "N"), ("customer_id", "bigint", "N")], [index("idx_notes_customer", ["customer_id"])])}
    quiet = {CUSTOMERS: stats(n_tup_del=0, n_tup_upd=0), notes: stats()}
    found = run(queries=[query(1, 1.0)], fks=[fk], table_stats=quiet, tables=tables, deltas={(notes, "idx_notes_customer"): 0})
    assert "unused-fk-index-public.notes-idx_notes_customer" in found
    busy_parent = {CUSTOMERS: stats(n_tup_del=4, n_tup_upd=0), notes: stats()}
    assert "unused-fk-index-public.notes-idx_notes_customer" not in run(
        queries=[query(1, 1.0)], fks=[fk], table_stats=busy_parent, tables=tables, deltas={(notes, "idx_notes_customer"): 0}
    )


def test_value_skew_reports_frequency_never_the_value():
    findings = run(
        queries=[query(1, 100.0)],
        relations=[RelationRow(1, ORDERS, ["seq"], ["customer_id"])],
        table_stats={ORDERS: stats()},
        tables={ORDERS: table_info(ORDERS_COLUMNS, stats={"customer_id": ColumnStats(8, 0.62, 0.04, 900)})},
    )
    finding = findings["value-skew-public.orders-customer_id"]
    assert "62.0%" in finding["title"]


def test_balanced_booleans_and_few_valued_columns_are_not_skew():
    columns = ORDERS_COLUMNS + [("active", "boolean", "B"), ("status", "text", "S")]
    findings = run(
        queries=[query(1, 100.0)],
        relations=[RelationRow(1, ORDERS, ["seq"], ["active", "status"])],
        table_stats={ORDERS: stats()},
        tables={
            ORDERS: table_info(
                columns,
                stats={
                    "active": ColumnStats(1, 0.5, 0.5, 2),  # 50/50 boolean
                    "status": ColumnStats(8, 0.6, 0.3, 3),  # 3 values, 60/30/10
                },
            )
        },
    )
    assert not any(f.startswith("value-skew") for f in findings)


def test_separate_join_conditions_stay_separate_findings():
    users, messages = Table("public", "users"), Table("public", "messages")
    tables = {
        users: table_info([("id", "bigint", "N")], [index("users_pkey", ["id"], unique=True, primary=True)]),
        messages: table_info([("id", "bigint", "N"), ("sender_id", "bigint", "N"), ("recipient_id", "bigint", "N")]),
    }
    findings = run(
        queries=[query(1, 50.0), query(2, 50.0)],
        relations=[RelationRow(1, messages, ["seq"], []), RelationRow(2, messages, ["seq"], [])],
        joins=[
            JoinRow(1, messages, users, ["sender_id"], ["id"], "Inner", "Hash Join"),
            JoinRow(2, messages, users, ["recipient_id"], ["id"], "Inner", "Hash Join"),
        ],
        table_stats={users: stats(), messages: stats()},
        tables=tables,
        leading={(users, "id")},
    )
    sender = findings["missing-fk-join-public.messages-sender_id"]
    recipient = findings["missing-fk-join-public.messages-recipient_id"]
    assert "FOREIGN KEY (sender_id) REFERENCES public.users (id)" in sender["recommended_ddl"]
    assert "recipient_id" in recipient["recommended_ddl"] and "sender_id" not in recipient["recommended_ddl"]
    assert findings["missing-join-index-public.messages-sender_id"]["index_candidate"]["columns"] == ["sender_id"]
    assert findings["missing-join-index-public.messages-recipient_id"]["index_candidate"]["columns"] == ["recipient_id"]


def test_fk_on_partitioned_child_is_not_offered_as_not_valid():
    findings = run(
        queries=[query(1, 100.0)],
        joins=[JoinRow(1, ORDERS, REFUNDS, ["id"], ["order_ref"], "Inner", "Hash Join")],
        table_stats={ORDERS: stats(), REFUNDS: stats()},
        tables={
            ORDERS: table_info(ORDERS_COLUMNS, [index("orders_pkey", ["id"], unique=True, primary=True)]),
            REFUNDS: table_info(REFUNDS_COLUMNS, relkind="p"),
        },
    )
    ddl = findings["missing-fk-join-public.refunds-order_ref"]["recommended_ddl"]
    assert "NOT VALID" not in ddl and "VALIDATE" not in ddl


def test_unused_claims_need_a_day_of_evidence():
    tables = {
        ORDERS: table_info(
            ORDERS_COLUMNS,
            [index("idx_orders_customer", ["customer_id"]), index("idx_orders_customer_created", ["customer_id", "created_at"])],
        )
    }
    deltas = {(ORDERS, "idx_orders_customer"): 0, (ORDERS, "idx_orders_customer_created"): 900}
    short = run(queries=[query(1, 1.0)], table_stats={ORDERS: stats()}, tables=tables, deltas=deltas, window_hours=0.2)
    assert not any(f.startswith("consolidate-index") for f in short)


def test_scan_output_columns_are_recorded_as_referenced():
    plan = {
        "Node Type": "Seq Scan",
        "Schema": "public",
        "Relation Name": "orders",
        "Alias": "o",
        "Output": ["o.id", "o.total", "(o.total * '2'::numeric)"],
        "Filter": "(o.created_at > now())",
    }
    relation = extract_workload(plan).relations[0]
    assert relation.referenced_columns == {"id", "total"}
    assert relation.filter_columns == {"created_at"}


def test_unqualified_output_of_a_single_table_query_is_recorded():
    # What PostgreSQL really prints for SELECT id, actor, action FROM
    # audit_log WHERE ...: conditions are alias-qualified, Output isn't
    # (it only qualifies when the query has more than one range-table entry).
    plan = {
        "Node Type": "Seq Scan",
        "Schema": "public",
        "Relation Name": "audit_log",
        "Alias": "audit_log",
        "Output": ["id", "lower(actor)", "action", "created_at", "(detail)::character varying"],
        "Filter": "(audit_log.action = 'export'::text)",
    }
    relation = extract_workload(plan).relations[0]
    assert {"id", "actor", "action", "created_at", "detail"} <= relation.referenced_columns
    assert "lower" not in relation.referenced_columns and "character" not in relation.referenced_columns


# --- What-if helpers -----------------------------------------------------------


def test_cost_explain_sql_mirrors_generic_plan_rules():
    assert cost_explain_sql("SELECT 1", 150000) == "EXPLAIN (FORMAT JSON) SELECT 1"
    assert cost_explain_sql("SELECT * FROM t WHERE id = $1", 160000).startswith("EXPLAIN (GENERIC_PLAN, FORMAT JSON)")
    assert cost_explain_sql("SELECT * FROM t WHERE id = $1", 150000) is None


def test_hypothetical_index_sql_quotes_identifiers():
    assert hypothetical_index_sql("public", "Order", ["customer id"]) == (
        'CREATE INDEX ON public."Order" USING btree ("customer id")'
    )


def test_plan_cost_and_index_use_are_read_from_explain_json():
    plan = [{"Plan": {"Node Type": "Nested Loop", "Total Cost": 42.5, "Plans": [{"Node Type": "Index Scan", "Index Name": "<13>btree_orders_customer_id"}]}}]
    assert plan_total_cost(plan) == 42.5
    assert plan_uses_index(plan, "<13>btree_orders_customer_id")
    assert not plan_uses_index(plan, "<14>btree_other")
    assert change_pct(100.0, 25.0) == -75.0


def test_verdicts_follow_improvement_and_regression_thresholds():
    def row(change, uses=True, ms=10.0):
        return {"db_time_ms": ms, "base_cost": 100.0, "new_cost": 100.0 + change, "change_pct": change, "uses_index": uses}

    assert summarize_candidate([row(-60.0), row(0.0)])["verdict"] == "recommended"
    assert summarize_candidate([row(-60.0), row(15.0)])["verdict"] == "mixed"
    assert summarize_candidate([row(-5.0)])["verdict"] == "no_gain"
    assert summarize_candidate([row(0.0, uses=False)])["verdict"] == "unused"
    weighted = summarize_candidate([row(-50.0, ms=30.0), row(0.0, ms=10.0)])
    assert weighted["weighted_improvement_pct"] == 37.5
    assert weighted["improved"] == 1 and weighted["unchanged"] == 1


def test_what_if_result_model_accepts_plans():
    WhatIfCandidateResult(
        schema_name="public",
        table_name="orders",
        columns=["customer_id"],
        ddl="CREATE INDEX CONCURRENTLY x ON public.orders (customer_id);",
        estimated_size_bytes=8192,
        error=None,
        verdict="recommended",
        improved=1,
        regressed=0,
        unchanged=0,
        weighted_improvement_pct=50.0,
        queries=[
            {
                "queryid": 1,
                "query": "SELECT 1",
                "db_time_ms": 1.0,
                "base_cost": 10.0,
                "new_cost": 5.0,
                "change_pct": -50.0,
                "uses_index": True,
                "plan_before": [{"Plan": {}}],
                "plan_after": [{"Plan": {}}],
            }
        ],
    )
