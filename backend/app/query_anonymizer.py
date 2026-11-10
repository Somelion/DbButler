"""Best-effort anonymization for the AI analysis features
(routers/query_analysis.py, routers/index_coverage_analysis.py) — what gets
sent to an external AI provider when the user leaves Anonymize on.

Two kinds of data are masked:

- Identifiers: schema/table/view/index/column names found in the target's
  own catalog become generic placeholders (schema_N/table_N/index_N/column_N).
- Literal values: an analyzed query usually has real values filled in for
  pg_stat_statements' $1/$2/... placeholders (query_guard.py refuses to
  EXPLAIN ANALYZE an unfilled one), and EXPLAIN ANALYZE echoes those values
  back in its Filter/Index Cond lines — so every string literal becomes
  'str_N' and every numeric literal num_N, in both the query and the plan.

In plan text, the plan's own vocabulary — node types, labels like "Rows
Removed by Filter", the (cost=... rows=... width=...) blocks and timings —
is never rewritten, even when a column happens to be called "rows", "cost"
or "time"; and numbers are only masked inside condition/output lines, so
row counts and timings stay intact for the AI to reason about.

Function and type names are left as-is — they're almost always built-ins,
and the AI needs them to say anything useful."""

import re
from dataclasses import dataclass, field

# Not narrowed by a target's schema allowlist (app/schema_filter.py) —
# skipped deliberately: this only builds a candidate identifier pool that
# still gets filtered down to whatever literally appears in the query/plan
# text below, so an out-of-allowlist name would need to already be part of
# a query the user is actively analyzing to matter at all. Index names are
# included because EXPLAIN prints them ("Index Scan using orders_email_idx"),
# and materialized views because information_schema.columns doesn't list them.
IDENTIFIER_QUERY = """
    SELECT n.nspname, c.relname, c.relkind, a.attname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attribute a
        ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind NOT IN ('i', 'I')
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'i', 'I')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname !~ '^pg_toast'
"""

_INDEX_RELKINDS = ("i", "I")

# Literals, as they appear in SQL text and in EXPLAIN's deparsed conditions.
# Dollar-quoted bodies first (they can contain quotes), then E'' strings
# (backslash escapes), then standard '' strings.
_STRING_LITERAL = re.compile(
    r"\$(?P<tag>[A-Za-z_][A-Za-z0-9_]*)?\$.*?\$(?P=tag)?\$"
    r"|[eE]'(?:[^'\\]|\\.|'')*'"
    r"|'(?:[^']|'')*'",
    re.DOTALL,
)
_NUMERIC_LITERAL = re.compile(r"(?<![\w$.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])")

# Plan lines whose text after the label is a deparsed expression that can
# carry literal values. "Rows Removed by Filter: 12" doesn't match — the
# label has to start the line (after indentation / "->").
_PLAN_CONDITION_LINE = re.compile(
    r"^(?P<label>[ \t]*(?:->[ \t]+)?"
    r"(?:Index Cond|Recheck Cond|Hash Cond|Merge Cond|TID Cond|Join Filter|One-Time Filter|Filter|"
    r"Order By|Output|Cache Key|Run Condition):)(?P<expr>.*)$",
    re.MULTILINE,
)

# Spans of plan text that are EXPLAIN's own vocabulary and must never be
# rewritten: cost/actual blocks, "never executed", and the node type or
# label that starts a line ("Seq Scan", "Index Scan", "Rows Removed by
# Filter", "Planning Time", "Buffers", ...).
_PLAN_PROTECTED = re.compile(
    r"\((?:cost|actual)\b[^)]*\)"
    r"|\(never executed\)"
    r"|^[ \t]*(?:->[ \t]+)?[A-Z][A-Za-z -]*?(?=:|[ \t]+(?:on|using)[ \t]|[ \t]{2}|[ \t]*\(|$)",
    re.MULTILINE,
)


def _word_pattern(identifier: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)


def _plan_word_pattern(identifier: str) -> re.Pattern:
    # Not when followed by "=" (rows=, hit=, read=) or a single ":" (a label
    # like "Memory:") — but still before a "::" cast.
    return re.compile(rf"\b{re.escape(identifier)}\b(?!=|:(?!:))", re.IGNORECASE)


def build_identifier_map(cur, *texts: str) -> dict[str, str]:
    """Looks up every schema/table/view/index/column name in the target's
    own catalog and returns a mapping from each one that actually appears
    (as a whole word, case-insensitive) in any of the given texts to a
    stable, generic placeholder (schema_N/table_N/index_N/column_N). Only
    identifiers actually referenced end up in the map, so it stays small.

    A name is claimed by the first category that matches it (schema, then
    table, then index, then column) — the rare case of a column sharing a
    name with an unrelated table gets one consistent placeholder rather than
    two, which is a fine simplification for this feature's purpose."""
    cur.execute(IDENTIFIER_QUERY)
    rows = cur.fetchall()

    schemas: set[str] = set()
    tables: set[str] = set()
    indexes: set[str] = set()
    columns: set[str] = set()
    for schema_name, rel_name, relkind, column_name in rows:
        schemas.add(schema_name)
        (indexes if relkind in _INDEX_RELKINDS else tables).add(rel_name)
        if column_name:
            columns.add(column_name)

    combined_text = "\n".join(texts)
    mapping: dict[str, str] = {}

    for prefix, names in (("schema", schemas), ("table", tables), ("index", indexes), ("column", columns)):
        counter = 1
        for name in sorted(names, key=len, reverse=True):
            if name.lower() in mapping:
                continue
            if _word_pattern(name).search(combined_text):
                mapping[name.lower()] = f"{prefix}_{counter}"
                counter += 1

    return mapping


def anonymize_text(text: str, mapping: dict[str, str]) -> str:
    """Applies an identifier map built by build_identifier_map to SQL text.
    Longer identifiers are substituted first so one name can't clobber a
    longer name that contains it as a substring before the longer match
    runs."""
    for original, placeholder in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = _word_pattern(original).sub(placeholder, text)
    return text


def anonymize_plan_text(text: str, mapping: dict[str, str]) -> str:
    """anonymize_text for EXPLAIN output: identifiers are only replaced
    outside the plan's own vocabulary (_PLAN_PROTECTED), so a column called
    "rows" or "time" can't corrupt "rows=12" or "Execution Time"."""
    ordered = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    patterns = [(_plan_word_pattern(original), placeholder) for original, placeholder in ordered]

    pieces = []
    last_end = 0
    for protected in _PLAN_PROTECTED.finditer(text):
        pieces.append(_replace_all(text[last_end : protected.start()], patterns))
        pieces.append(protected.group(0))
        last_end = protected.end()
    pieces.append(_replace_all(text[last_end:], patterns))
    return "".join(pieces)


def _replace_all(chunk: str, patterns: list[tuple[re.Pattern, str]]) -> str:
    for pattern, placeholder in patterns:
        chunk = pattern.sub(placeholder, chunk)
    return chunk


def deanonymize_text(text: str, mapping: dict[str, str]) -> str:
    """Reverses anonymize_text for display — swaps each placeholder back to
    its real identifier so the AI's recommendations read naturally, even
    though the AI itself never saw the real names."""
    for original, placeholder in mapping.items():
        text = re.sub(rf"\b{re.escape(placeholder)}\b", original, text, flags=re.IGNORECASE)
    return text


@dataclass
class LiteralMasker:
    """Replaces literal values with stable placeholders — the same value
    gets the same placeholder everywhere (a query's 'alice@example.com' and
    the plan's 'alice@example.com'::text both become 'str_1')."""

    strings: dict[str, str] = field(default_factory=dict)  # original literal -> placeholder
    numbers: dict[str, str] = field(default_factory=dict)

    def _string_placeholder(self, match: re.Match) -> str:
        literal = match.group(0)
        if literal not in self.strings:
            self.strings[literal] = f"str_{len(self.strings) + 1}"
        return f"'{self.strings[literal]}'"

    def _number_placeholder(self, match: re.Match) -> str:
        literal = match.group(0)
        if literal not in self.numbers:
            self.numbers[literal] = f"num_{len(self.numbers) + 1}"
        return self.numbers[literal]

    def mask_strings(self, text: str) -> str:
        return _STRING_LITERAL.sub(self._string_placeholder, text)

    def mask_numbers(self, text: str) -> str:
        return _NUMERIC_LITERAL.sub(self._number_placeholder, text)

    def mask_query(self, text: str) -> str:
        return self.mask_numbers(self.mask_strings(text))

    def mask_plan(self, text: str) -> str:
        text = self.mask_strings(text)
        return _PLAN_CONDITION_LINE.sub(
            lambda m: m.group("label") + self.mask_numbers(m.group("expr")), text
        )

    def restore(self, text: str) -> str:
        """Puts the real values back into the AI's response. A quoted
        placeholder becomes the original literal exactly (quotes, E prefix
        and all); a bare one — the AI dropped the quotes — just its value."""
        for literal, placeholder in self.strings.items():
            text = text.replace(f"'{placeholder}'", literal)
        for literal, placeholder in self.strings.items():
            inner = literal[literal.index("'") + 1 : -1] if literal.endswith("'") else literal
            text = re.sub(rf"\b{re.escape(placeholder)}\b", lambda _m, value=inner: value, text)
        for literal, placeholder in self.numbers.items():
            text = re.sub(rf"\b{re.escape(placeholder)}\b", literal, text)
        return text


@dataclass
class AnonymizedQuery:
    query: str
    plan: str
    identifiers: dict[str, str]
    literals: LiteralMasker

    def restore(self, text: str) -> str:
        """Reverses both identifier and literal masking in the AI's response."""
        return self.literals.restore(deanonymize_text(text, self.identifiers))

    def stored_map(self) -> dict:
        """What's persisted in query_analyses.anonymization_map — the
        literal values are already stored in original_query anyway."""
        return {
            "identifiers": self.identifiers,
            "strings": {placeholder: literal for literal, placeholder in self.literals.strings.items()},
            "numbers": {placeholder: literal for literal, placeholder in self.literals.numbers.items()},
        }


def anonymize_query_and_plan(cur, query: str, plan_text: str) -> AnonymizedQuery:
    """Masks literals first (so a value can't be mistaken for an identifier,
    or vice versa), then identifiers, in both texts with shared maps."""
    literals = LiteralMasker()
    masked_query = literals.mask_query(query)
    masked_plan = literals.mask_plan(plan_text)
    identifiers = build_identifier_map(cur, masked_query, masked_plan)
    return AnonymizedQuery(
        query=anonymize_text(masked_query, identifiers),
        plan=anonymize_plan_text(masked_plan, identifiers),
        identifiers=identifiers,
        literals=literals,
    )
