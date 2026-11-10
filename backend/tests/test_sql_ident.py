from app.sql_ident import (
    ALLOWED_APPLY_STATEMENT,
    parse_qualified_name,
    quote_ident,
    quote_qualified,
    split_sql_statements,
)


def test_plain_lowercase_name_stays_bare():
    assert quote_ident("orders") == "orders"
    assert quote_ident("order_items_2") == "order_items_2"


def test_mixed_case_keyword_and_special_names_are_quoted():
    assert quote_ident("Order") == '"Order"'
    assert quote_ident("user") == '"user"'
    assert quote_ident("my table") == '"my table"'
    assert quote_ident("1st") == '"1st"'


def test_embedded_double_quote_is_doubled():
    assert quote_ident('x"); DROP TABLE t; --') == '"x""); DROP TABLE t; --"'


def test_quote_qualified():
    assert quote_qualified("public", "Order") == 'public."Order"'


def test_split_keeps_semicolons_inside_quotes():
    ddl = 'DROP INDEX CONCURRENTLY public."a;b";\nCREATE INDEX CONCURRENTLY i ON t (\'x;y\');'
    assert split_sql_statements(ddl) == [
        'DROP INDEX CONCURRENTLY public."a;b"',
        "CREATE INDEX CONCURRENTLY i ON t ('x;y')",
    ]


def test_split_keeps_a_newline_inside_a_quoted_identifier_in_one_statement():
    # Splitting by line would turn the second half into its own statement.
    hostile_name = 'x\n; DROP TABLE t; --'
    ddl = f"DROP INDEX CONCURRENTLY {quote_qualified('public', hostile_name)};"
    statements = split_sql_statements(ddl)
    assert len(statements) == 1
    assert ALLOWED_APPLY_STATEMENT.match(statements[0])


def test_split_handles_doubled_quotes():
    assert split_sql_statements('DROP INDEX CONCURRENTLY "a"";b";') == ['DROP INDEX CONCURRENTLY "a"";b"']


def test_allowed_apply_statement_only_matches_index_ddl():
    assert ALLOWED_APPLY_STATEMENT.match("CREATE INDEX CONCURRENTLY i ON t (a)")
    assert ALLOWED_APPLY_STATEMENT.match("drop index concurrently public.i")
    assert not ALLOWED_APPLY_STATEMENT.match("DROP TABLE t")
    assert not ALLOWED_APPLY_STATEMENT.match("SELECT 1")


def test_parse_qualified_name_round_trips():
    assert parse_qualified_name(quote_qualified("public", "Order")) == ["public", "Order"]
    assert parse_qualified_name('public."a""b"') == ["public", 'a"b']
    assert parse_qualified_name("Public.Idx") == ["public", "idx"]
