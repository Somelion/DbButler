"""Pure-function helpers for the Query Plan Regression Detector
(scheduler.py::_capture_query_plans, routers/plan_regressions.py) — turning a
raw EXPLAIN (FORMAT JSON) plan into a comparable "shape" that ignores costs,
row estimates, and timings, so two captures only differ when the planner
actually chose a structurally different plan (a different scan method, join
strategy, or index), not just refreshed its estimates.

Never uses ANALYZE: this module only ever builds a plain EXPLAIN (planning
only, no execution), unlike the interactive Explain screen's EXPLAIN
(ANALYZE, BUFFERS) on a single user-pasted, read-only-guarded SELECT
(app/query_guard.py). The plan regression collector loops every tracked
query regardless of type — an INSERT/UPDATE/DELETE among them is common,
since pg_stat_statements tracks all of them — and ANALYZE actually executes
the query, so running it unattended here would duplicate real writes.
"""

import hashlib
import json
import re

# pg_stat_statements normalizes literal values into $1/$2/... — the same
# text query_history.py and query_guard.py work from. A plain EXPLAIN can't
# run against a query with unfilled placeholders (Postgres treats "$1" as an
# extended-query-protocol bind parameter); GENERIC_PLAN lifts that
# restriction by planning without real parameter values, but only exists on
# PostgreSQL 16+.
PLACEHOLDER_PATTERN = re.compile(r"\$\d+\b")

GENERIC_PLAN_MIN_VERSION = 160000

# Node fields worth comparing across captures — anything about *how* the
# planner executes the query, not how much it estimated it would cost.
_STRUCTURAL_KEYS = ("Node Type", "Relation Name", "Index Name", "Join Type", "Strategy")


def has_unfilled_placeholders(query_text: str) -> bool:
    return bool(PLACEHOLDER_PATTERN.search(query_text))


def build_explain_sql(query_text: str, server_version_num: int) -> str | None:
    """Returns the EXPLAIN statement to run for this query, or None if it
    can't be safely captured this cycle — a parameterized query on a server
    older than GENERIC_PLAN_MIN_VERSION has no way to be planned without real
    parameter values this collector doesn't have, so it's skipped rather than
    guessed at."""
    if has_unfilled_placeholders(query_text):
        if server_version_num < GENERIC_PLAN_MIN_VERSION:
            return None
        return f"EXPLAIN (GENERIC_PLAN, FORMAT JSON) {query_text}"
    return f"EXPLAIN (FORMAT JSON) {query_text}"


def build_fingerprint(node: dict) -> dict:
    """Recursively strips an EXPLAIN JSON plan node down to the fields that
    describe its shape, keeping child plans in the same order the planner
    returned them."""
    fingerprint = {key: node[key] for key in _STRUCTURAL_KEYS if key in node}
    children = node.get("Plans")
    if children:
        fingerprint["Plans"] = [build_fingerprint(child) for child in children]
    return fingerprint


def hash_fingerprint(fingerprint: dict) -> str:
    serialized = json.dumps(fingerprint, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _node_label(node: dict) -> str:
    label = node.get("Node Type", "?")
    if node.get("Join Type"):
        label = f"{node['Join Type']} {label}"

    index_name = node.get("Index Name")
    relation_name = node.get("Relation Name")
    if index_name and relation_name:
        label = f"{label} using {index_name} on {relation_name}"
    elif index_name:
        label = f"{label} using {index_name}"
    elif relation_name:
        label = f"{label} on {relation_name}"
    return label


def summarize_fingerprint(fingerprint: dict, max_nodes: int = 4) -> str:
    """A short, human-readable rendering of a fingerprint's shape for display
    in a finding, e.g. "Hash Join -> Seq Scan on orders -> Index Scan using
    idx_customers_id on customers" — walks the tree depth-first and caps how
    many nodes it renders; a wide plan's full shape belongs in plan_json, not
    a one-line summary."""
    parts = []
    truncated = False

    def walk(node):
        nonlocal truncated
        if len(parts) >= max_nodes:
            truncated = True
            return
        parts.append(_node_label(node))
        for child in node.get("Plans", []):
            walk(child)

    walk(fingerprint)
    return " → ".join(parts) + (" → …" if truncated else "")
