"""Pure-function extraction for the Workload Map: which tables a query
touches, how, and which tables it joins on which columns, read from the
planner's own EXPLAIN (VERBOSE, FORMAT JSON) output rather than by parsing
SQL text.

The planner has already resolved everything a text parser would have to
guess at: aliases, views, search_path, and which table each column belongs
to. With VERBOSE every column reference in a condition is qualified by its
scan's alias ("o.customer_id"), and every scan node carries "Schema",
"Relation Name" and "Alias". Nothing here executes the query — the capture
job (scheduler.py::_capture_query_plans) runs plain EXPLAIN, planning only.

What counts as a join: an equality between columns of two different scans,
found in any join condition the planner prints — "Hash Cond", "Merge Cond",
"Join Filter", and the "Index Cond" of a nested loop's inner index scan,
where the planner pushes the join clause down. Non-equality joins (ranges,
LIKE, function calls on the join column) are not edges; that's deliberate,
they say nothing about a missing foreign key.

Partitions and TimescaleDB chunks are folded onto their logical parent via
a caller-supplied parent map (app/workload_catalog.py::fetch_parent_map), so
a query over 200 chunks is one table on the map, not 200.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field

# Bumped when extraction logic changes in a way that should re-extract
# every query on the next capture even though its plan is unchanged.
EXTRACTOR_VERSION = 1

JOIN_NODE_TYPES = frozenset({"Hash Join", "Merge Join", "Nested Loop"})

# Condition fields that can carry a join clause, and the subset that only
# filter a single scan.
JOIN_CONDITION_KEYS = ("Hash Cond", "Merge Cond", "Join Filter", "Index Cond", "Recheck Cond", "Filter")
SCAN_FILTER_KEYS = frozenset({"Index Cond", "Recheck Cond", "Filter"})

_ACCESS_KIND_BY_NODE = {
    "Seq Scan": "seq",
    "Index Scan": "index",
    "Index Only Scan": "index_only",
    "Bitmap Heap Scan": "bitmap",
    "Tid Scan": "tid",
    "Tid Range Scan": "tid",
    "Sample Scan": "seq",
}

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
# alias.column — not preceded by another identifier/dot (a 3-part name) or
# by "::" (a schema-qualified type in a cast), not followed by "(" (a
# schema-qualified function call such as pg_catalog.lower(...)).
_COLUMN_REF = rf'(?<![\w."$])(?<!::)({_IDENT})\.({_IDENT})(?!\s*\()(?![\w."$])'
_COLUMN_REF_RE = re.compile(_COLUMN_REF)
# A cast's type: plain, multi-word ("character varying"), schema-qualified
# or quoted, with an optional typmod and array suffix.
_CAST = rf'::\s*{_IDENT}(?:\.{_IDENT})?(?:\s+[A-Za-z_]\w*)*(?:\(\d+(?:,\s*\d+)?\))?(?:\[\])?'
# ref = ref, allowing the closing parens and casts EXPLAIN wraps around a
# column ("(o.customer_id)::bigint = c.id"). The right-hand column must end
# its operand — followed only by parens, a cast, AND/OR or the end — so
# "r.day = (d.day + 1)" isn't read as a join on day = day.
_EQUALITY_RE = re.compile(
    _COLUMN_REF
    + rf'(?:\s*\))*(?:\s*{_CAST})?(?:\s*\))*\s*=\s*(?:\(\s*)*'
    + _COLUMN_REF
    + rf'(?=(?:\s*\))*(?:\s*{_CAST})?(?:\s*\))*\s*(?:$|AND\b|OR\b))'
)
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")


@dataclass(frozen=True, order=True)
class Table:
    schema: str
    name: str

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class RelationAccess:
    table: Table
    access_kinds: set[str] = field(default_factory=set)
    filter_columns: set[str] = field(default_factory=set)


@dataclass
class JoinEdge:
    left: Table
    right: Table
    left_columns: list[str]
    right_columns: list[str]
    join_type: str | None
    join_strategy: str | None


@dataclass
class WorkloadExtract:
    relations: list[RelationAccess]
    joins: list[JoinEdge]

    def digest(self) -> str:
        """Stable hash of what was extracted, so an unchanged query isn't
        rewritten on every capture."""
        payload = {
            "v": EXTRACTOR_VERSION,
            "relations": [
                [r.table.qualified, sorted(r.access_kinds), sorted(r.filter_columns)]
                for r in sorted(self.relations, key=lambda r: r.table)
            ],
            # Sorted by their JSON text: join_type/strategy can be None next to
            # a string for the same pair (a clause outside any join node), and
            # Python won't order None against str.
            "joins": sorted(
                (
                    [j.left.qualified, j.right.qualified, j.left_columns, j.right_columns, j.join_type, j.join_strategy]
                    for j in self.joins
                ),
                key=json.dumps,
            ),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def unquote_identifier(ident: str) -> str:
    if len(ident) >= 2 and ident[0] == '"' and ident[-1] == '"':
        return ident[1:-1].replace('""', '"')
    return ident


def _walk(node: dict, ancestors: tuple = ()):
    """Yields (node, nearest enclosing join node or None), depth-first,
    including init plans and subplans (they're ordinary "Plans" children in
    EXPLAIN JSON)."""
    nearest_join = next((a for a in reversed(ancestors) if a.get("Node Type") in JOIN_NODE_TYPES), None)
    yield node, (node if node.get("Node Type") in JOIN_NODE_TYPES else nearest_join)
    for child in node.get("Plans", []) or []:
        yield from _walk(child, ancestors + (node,))


def plan_relations(plan_root: dict) -> set[Table]:
    """Every physical relation a plan scans or modifies — what the caller
    needs parent mappings for."""
    found = set()
    for node, _join in _walk(plan_root):
        if node.get("Relation Name") and node.get("Schema"):
            found.add(Table(node["Schema"], node["Relation Name"]))
    return found


def _access_kind(node: dict) -> str:
    if node.get("Node Type") == "ModifyTable":
        return (node.get("Operation") or "modify").lower()
    return _ACCESS_KIND_BY_NODE.get(node.get("Node Type"), "other")


def _resolve_aliases(scans: dict[str, Table], logical: dict[Table, Table]):
    """Returns a function alias -> logical Table or None.

    A condition above an Append over partitions references the *parent*
    alias ("o"), which has no scan node of its own — the children are
    "o_1", "o_2", ... (PostgreSQL's EXPLAIN de-duplicates names that way).
    Likewise a TimescaleDB hypertable referenced without an alias appears as
    its own name while only its chunks are scanned. Both resolve to the
    logical table their scans fold onto, when that's unambiguous. Anything
    else unknown (a CTE, a subquery alias) resolves to None and is ignored.
    """
    cache: dict[str, Table | None] = {}

    def resolve(alias: str) -> Table | None:
        if alias in cache:
            return cache[alias]
        if alias in scans:
            result = logical[scans[alias]]
        else:
            suffixed = re.compile(rf"^{re.escape(alias)}_\d+$")
            candidates = {logical[t] for a, t in scans.items() if suffixed.match(a)}
            if not candidates:
                candidates = {logical[t] for t in scans.values() if logical[t].name == alias}
            result = candidates.pop() if len(candidates) == 1 else None
        cache[alias] = result
        return result

    return resolve


def extract_workload(plan_root: dict, parent_of: dict[Table, Table] | None = None) -> WorkloadExtract:
    """Relations and join edges for one plan. parent_of maps a partition or
    chunk to its logical parent; relations not in it are their own parent."""
    parent_of = parent_of or {}
    scans: dict[str, Table] = {}
    logical: dict[Table, Table] = {}
    relations: dict[Table, RelationAccess] = {}

    for node, _join in _walk(plan_root):
        if not (node.get("Relation Name") and node.get("Schema")):
            continue
        physical = Table(node["Schema"], node["Relation Name"])
        logical[physical] = parent_of.get(physical, physical)
        scans[node.get("Alias") or physical.name] = physical
        access = relations.setdefault(logical[physical], RelationAccess(logical[physical]))
        access.access_kinds.add(_access_kind(node))

    resolve = _resolve_aliases(scans, logical)
    # (left, right, join_type, strategy) -> ordered unique column pairs
    grouped: dict[tuple, list[tuple[str, str]]] = {}

    for node, join_node in _walk(plan_root):
        for key in JOIN_CONDITION_KEYS:
            condition = node.get(key)
            if not condition:
                continue
            text = _STRING_LITERAL_RE.sub("''", condition)
            join_spans = []
            for match in _EQUALITY_RE.finditer(text):
                left_alias, left_col, right_alias, right_col = (unquote_identifier(g) for g in match.groups())
                left, right = resolve(left_alias), resolve(right_alias)
                if left is None or right is None or left_alias == right_alias:
                    continue
                join_spans.append(match.span())
                if (right, right_col) < (left, left_col):
                    left, right, left_col, right_col = right, left, right_col, left_col
                group_key = (
                    left,
                    right,
                    join_node.get("Join Type") if join_node else None,
                    join_node.get("Node Type") if join_node else None,
                )
                pairs = grouped.setdefault(group_key, [])
                if (left_col, right_col) not in pairs:
                    pairs.append((left_col, right_col))

            # Filter columns only from scans: an Agg's "Filter" is a HAVING
            # clause ("count(o.id) > 5"), not a filter on the table.
            if key not in SCAN_FILTER_KEYS or not (node.get("Relation Name") or node.get("Index Name")):
                continue
            for match in _COLUMN_REF_RE.finditer(text):
                if any(start <= match.start() < end for start, end in join_spans):
                    continue
                table = resolve(unquote_identifier(match.group(1)))
                if table is not None and table in relations:
                    relations[table].filter_columns.add(unquote_identifier(match.group(2)))

    joins = [
        JoinEdge(left, right, [p[0] for p in pairs], [p[1] for p in pairs], join_type, strategy)
        for (left, right, join_type, strategy), pairs in grouped.items()
    ]
    return WorkloadExtract(relations=list(relations.values()), joins=joins)
