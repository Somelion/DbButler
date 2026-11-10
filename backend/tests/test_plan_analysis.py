from app.explain_analysis import analyze_plan, extract_execution_time_ms
from app.plan_fingerprint import (
    build_explain_sql,
    build_fingerprint,
    hash_fingerprint,
    summarize_fingerprint,
)

PLAN_JSON = {
    "Node Type": "Hash Join",
    "Join Type": "Inner",
    "Total Cost": 123.4,
    "Plan Rows": 10,
    "Plans": [
        {"Node Type": "Seq Scan", "Relation Name": "orders", "Total Cost": 50.0},
        {
            "Node Type": "Hash",
            "Plans": [
                {"Node Type": "Index Scan", "Relation Name": "customers", "Index Name": "customers_pkey"},
            ],
        },
    ],
}


def test_explain_sql_uses_generic_plan_only_when_needed_and_supported():
    assert build_explain_sql("SELECT 1", 150000) == "EXPLAIN (FORMAT JSON) SELECT 1"
    assert build_explain_sql("SELECT * FROM t WHERE id = $1", 160000) == (
        "EXPLAIN (GENERIC_PLAN, FORMAT JSON) SELECT * FROM t WHERE id = $1"
    )
    assert build_explain_sql("SELECT * FROM t WHERE id = $1", 150000) is None


def test_fingerprint_keeps_shape_and_drops_costs():
    fingerprint = build_fingerprint(PLAN_JSON)
    assert "Total Cost" not in fingerprint and "Plan Rows" not in fingerprint
    assert fingerprint["Plans"][1]["Plans"][0]["Index Name"] == "customers_pkey"


def test_hash_is_stable_across_cost_changes_but_not_shape_changes():
    cheaper = {**PLAN_JSON, "Total Cost": 1.0}
    assert hash_fingerprint(build_fingerprint(PLAN_JSON)) == hash_fingerprint(build_fingerprint(cheaper))

    reshaped = {**PLAN_JSON, "Node Type": "Merge Join"}
    assert hash_fingerprint(build_fingerprint(PLAN_JSON)) != hash_fingerprint(build_fingerprint(reshaped))


def test_summary_walks_depth_first_and_truncates():
    fingerprint = build_fingerprint(PLAN_JSON)
    assert summarize_fingerprint(fingerprint) == (
        "Inner Hash Join → Seq Scan on orders → Hash → Index Scan using customers_pkey on customers"
    )
    assert summarize_fingerprint(fingerprint, max_nodes=2) == "Inner Hash Join → Seq Scan on orders → …"


PLAN_TEXT = """Sort  (cost=10.00..11.00 rows=100 width=8) (actual time=5.0..6.0 rows=100 loops=1)
  Sort Method: external merge  Disk: 1024kB
  ->  Seq Scan on orders  (cost=0.00..5.00 rows=100 width=8) (actual time=0.1..4.0 rows=100 loops=1)
        Filter: (lower(status) = 'open'::text)
        Rows Removed by Filter: 250000
        Buffers: shared hit=10 read=5000
Planning Time: 0.100 ms
Execution Time: 6.543 ms"""


def test_plan_red_flags():
    findings = {finding["id"]: finding for finding in analyze_plan(PLAN_TEXT)}
    assert set(findings) == {
        "seq-scan-orders",
        "rows-removed-by-filter",
        "disk-sort",
        "function-wrapped-column",
        "heavy-disk-io",
    }
    assert findings["rows-removed-by-filter"]["severity"] == "critical"
    assert findings["function-wrapped-column"]["title"].startswith("lower(status)")


def test_quiet_plan_has_no_findings():
    plan = "Index Scan using t_pkey on t  (cost=0.1..8.1 rows=1 width=4)\n  Buffers: shared hit=3\nExecution Time: 0.02 ms"
    assert analyze_plan(plan) == []


def test_execution_time():
    assert extract_execution_time_ms(PLAN_TEXT) == 6.543
    assert extract_execution_time_ms("no timing here") is None
