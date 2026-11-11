"""Identifier quoting and statement splitting for the DDL this app generates
and executes itself (Index Advisor's Apply, app/index_analysis.py). Catalog
names are attacker-controllable — anyone who can CREATE TABLE on a target
can name a column `x); DROP TABLE t; --` — so every generated statement
quotes its identifiers the same way PostgreSQL's own quote_ident() does,
and the Apply path splits/validates statements with a quote-aware splitter
rather than by line (a quoted identifier may legally contain a newline).
"""

import re

# Keywords that can't appear as a bare column/table/index name — PostgreSQL's
# reserved, type/function-name and column-name keyword categories (Appendix C
# of the docs). Over-quoting is always harmless, so this errs on the side of
# including a keyword rather than leaving one out.
_KEYWORDS_NEEDING_QUOTES = frozenset(
    """
    all analyse analyze and any array as asc asymmetric both case cast check collate column
    constraint create current_catalog current_date current_role current_time current_timestamp
    current_user default deferrable desc distinct do else end except false fetch for foreign from
    grant group having in initially intersect into lateral leading limit localtime localtimestamp
    not null offset on only or order placing primary references returning select session_user some
    symmetric system_user table then to trailing true union unique user using variadic when where
    window with
    authorization binary collation concurrently cross current_schema freeze full ilike inner is
    isnull join left like natural notnull outer overlaps right similar tablesample verbose
    between bigint bit boolean char character coalesce dec decimal exists extract float greatest
    grouping inout int integer interval json json_array json_arrayagg json_exists json_object
    json_objectagg json_query json_scalar json_serialize json_table json_value least merge_action
    national nchar none normalize nullif numeric out overlay position precision real row setof
    smallint substring time timestamp treat trim values varchar xmlattributes xmlconcat xmlelement
    xmlexists xmlforest xmlnamespaces xmlparse xmlpi xmlroot xmlserialize xmltable
    """.split()
)

_SAFE_BARE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_$]*$")

# One identifier part: either "quoted, with "" escapes" or a bare run of
# characters that can't start a new token.
_IDENT_PART = r'(?:"(?:[^"]|"")+"|[^\s".;(),]+)'
QUALIFIED_NAME_PATTERN = rf"{_IDENT_PART}(?:\.{_IDENT_PART})*"

ALLOWED_APPLY_STATEMENT = re.compile(r"^\s*(CREATE\s+INDEX\s+CONCURRENTLY|DROP\s+INDEX\s+CONCURRENTLY)\s", re.IGNORECASE)


def quote_ident(name: str) -> str:
    """Mirrors PostgreSQL's quote_ident(): leaves a plain lowercase,
    non-keyword name bare (so the DDL shown to the user stays readable) and
    double-quotes anything else, doubling any embedded double quote."""
    if _SAFE_BARE_IDENTIFIER.match(name) and name not in _KEYWORDS_NEEDING_QUOTES:
        return name
    return '"' + name.replace('"', '""') + '"'


def quote_qualified(*parts: str) -> str:
    """quote_ident() for each part, joined with dots — e.g.
    quote_qualified("public", "Order") -> public."Order"."""
    return ".".join(quote_ident(part) for part in parts)


def quote_literal(value: str) -> str:
    """Mirrors PostgreSQL's quote_literal() for a string under the default
    standard_conforming_strings=on: single-quoted, embedded quotes doubled."""
    return "'" + value.replace("'", "''") + "'"


def parse_qualified_name(text: str) -> list[str]:
    """Inverse of quote_qualified() for a name already matched by
    QUALIFIED_NAME_PATTERN: splits on dots outside quotes and unquotes each
    part (a bare part is case-folded to lowercase, as PostgreSQL does)."""
    parts = re.findall(_IDENT_PART, text)
    unquoted = []
    for part in parts:
        if part.startswith('"'):
            unquoted.append(part[1:-1].replace('""', '"'))
        else:
            unquoted.append(part.lower())
    return unquoted


def split_sql_statements(sql: str) -> list[str]:
    """Splits on semicolons that sit outside single-quoted literals and
    double-quoted identifiers, returning each non-empty statement stripped
    and without its trailing semicolon."""
    statements = []
    current: list[str] = []
    quote_char: str | None = None
    for char in sql:
        if quote_char:
            current.append(char)
            # A doubled quote ('' or "") toggles out and straight back in,
            # which leaves the state correct without special-casing it.
            if char == quote_char:
                quote_char = None
            continue
        if char in ("'", '"'):
            quote_char = char
            current.append(char)
        elif char == ";":
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]
