"""Best-effort anonymization for the AI Query Analysis feature
(routers/query_analysis.py) — replaces schema/table/column names with
generic placeholders before a query and its EXPLAIN plan are sent to an
external AI provider. Note this only redacts identifiers, not data: literal
values in the query text are already replaced with $1/$2/... by
pg_stat_statements normalization before a query ever reaches this app (see
docs/DATA_MODEL.md), which is the source every analyzable query comes from.
Index names in the EXPLAIN plan are not anonymized — a known limitation, not
yet worth the extra pg_indexes round-trip for a first version."""

import re

# Not narrowed by a target's schema allowlist (app/schema_filter.py) —
# skipped deliberately: this only builds a candidate identifier pool that
# still gets filtered down to whatever literally appears in the query/plan
# text below, so an out-of-allowlist name would need to already be part of
# a query the user is actively analyzing to matter at all.
IDENTIFIER_QUERY = """
    SELECT DISTINCT table_schema, table_name, column_name
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
"""


def _word_pattern(identifier: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)


def build_identifier_map(cur, *texts: str) -> dict[str, str]:
    """Looks up every schema/table/column name in the target's own catalog
    and returns a mapping from each one that actually appears (as a whole
    word, case-insensitive) in any of the given texts to a stable, generic
    placeholder (schema_N/table_N/column_N). Only identifiers actually
    referenced end up in the map, so it stays small.

    A name is claimed by the first category that matches it (schema, then
    table, then column) — the rare case of a column sharing a name with an
    unrelated table gets one consistent placeholder rather than two, which
    is a fine simplification for this feature's purpose."""
    cur.execute(IDENTIFIER_QUERY)
    rows = cur.fetchall()

    schemas: set[str] = set()
    tables: set[str] = set()
    columns: set[str] = set()
    for schema_name, table_name, column_name in rows:
        schemas.add(schema_name)
        tables.add(table_name)
        columns.add(column_name)

    combined_text = "\n".join(texts)
    mapping: dict[str, str] = {}

    for prefix, names in (("schema", schemas), ("table", tables), ("column", columns)):
        counter = 1
        for name in sorted(names, key=len, reverse=True):
            if name.lower() in mapping:
                continue
            if _word_pattern(name).search(combined_text):
                mapping[name.lower()] = f"{prefix}_{counter}"
                counter += 1

    return mapping


def anonymize_text(text: str, mapping: dict[str, str]) -> str:
    """Applies an identifier map built by build_identifier_map. Longer
    identifiers are substituted first so one name can't clobber a longer
    name that contains it as a substring before the longer match runs."""
    for original, placeholder in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        text = _word_pattern(original).sub(placeholder, text)
    return text


def deanonymize_text(text: str, mapping: dict[str, str]) -> str:
    """Reverses anonymize_text for display — swaps each placeholder back to
    its real identifier so the AI's recommendations read naturally, even
    though the AI itself never saw the real names."""
    for original, placeholder in mapping.items():
        text = re.sub(rf"\b{re.escape(placeholder)}\b", original, text, flags=re.IGNORECASE)
    return text
