"""What-if indexes: cost a candidate index against the tracked workload
with HypoPG, without building it (routers/what_if.py).

HypoPG keeps "hypothetical" indexes in the backend's own memory: the
planner considers them in a plain EXPLAIN as if they existed, nothing is
written to the database, and they vanish with the session. EXPLAIN without
ANALYZE plans only, so INSERT/UPDATE/DELETE among the tracked queries are
safe to cost too. Costs are the planner's estimates for a generic plan, not
measured runtimes — the screen says so.

Thresholds follow Dexter / Supabase index_advisor: a query counts as
improved at >= 20% lower estimated cost, regressed at >= 10% higher.
"""

from app.plan_fingerprint import GENERIC_PLAN_MIN_VERSION, has_unfilled_placeholders
from app.sql_ident import quote_ident, quote_qualified

IMPROVED_PCT = -20.0
REGRESSED_PCT = 10.0
MAX_CANDIDATES = 5
MAX_COLUMNS = 8
MAX_PLANS_RETURNED = 10


def cost_explain_sql(query_text: str, server_version_num: int) -> str | None:
    """Plain EXPLAIN (FORMAT JSON) for costing — GENERIC_PLAN for a
    parameterized query on PostgreSQL 16+, None when it can't be planned."""
    if has_unfilled_placeholders(query_text):
        if server_version_num < GENERIC_PLAN_MIN_VERSION:
            return None
        return f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {query_text}"
    return f"EXPLAIN (FORMAT JSON) {query_text}"


def hypothetical_index_sql(schema: str, table: str, columns: list[str]) -> str:
    """The CREATE INDEX statement handed to hypopg_create_index(). Every
    identifier is quoted; the caller has also checked each one exists."""
    cols = ", ".join(quote_ident(c) for c in columns)
    return f"CREATE INDEX ON {quote_qualified(schema, table)} USING btree ({cols})"


def recommended_index_ddl(schema: str, table: str, columns: list[str]) -> str:
    name = f"idx_{table}_{'_'.join(columns)}".lower()[:63]
    cols = ", ".join(quote_ident(c) for c in columns)
    return f"CREATE INDEX CONCURRENTLY {quote_ident(name)} ON {quote_qualified(schema, table)} ({cols});"


def plan_root(plan_payload) -> dict:
    return plan_payload[0]["Plan"]


def plan_total_cost(plan_payload) -> float:
    return float(plan_root(plan_payload).get("Total Cost") or 0.0)


def plan_uses_index(plan_payload, index_name: str) -> bool:
    stack = [plan_root(plan_payload)]
    while stack:
        node = stack.pop()
        if node.get("Index Name") == index_name:
            return True
        stack.extend(node.get("Plans", []) or [])
    return False


def change_pct(base_cost: float, new_cost: float) -> float:
    if base_cost <= 0:
        return 0.0
    return round(100.0 * (new_cost - base_cost) / base_cost, 1)


def summarize_candidate(results: list[dict]) -> dict:
    """results: one dict per costed query with db_time_ms, base_cost,
    new_cost, change_pct, uses_index. Returns counts, the DB-time-weighted
    improvement and a verdict."""
    improved = sum(1 for r in results if r["change_pct"] <= IMPROVED_PCT)
    regressed = sum(1 for r in results if r["change_pct"] >= REGRESSED_PCT)
    used = any(r["uses_index"] for r in results)
    total_weight = sum(r["db_time_ms"] for r in results)
    if total_weight > 0:
        weighted = sum(r["db_time_ms"] * -r["change_pct"] for r in results) / total_weight
    elif results:
        weighted = sum(-r["change_pct"] for r in results) / len(results)
    else:
        weighted = 0.0
    if not used:
        verdict = "unused"
    elif improved and not regressed:
        verdict = "recommended"
    elif improved and regressed:
        verdict = "mixed"
    else:
        verdict = "no_gain"
    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": len(results) - improved - regressed,
        "weighted_improvement_pct": round(weighted, 1),
        "verdict": verdict,
    }
