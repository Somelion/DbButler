from app.index_analysis import (
    build_covering_index_ddl,
    find_duplicate_indexes,
    find_missing_fk_indexes,
    find_redundant_indexes,
    find_unused_indexes,
)

BTREE_INT = "1978:0:0"  # opclass:option:collation for one key position
BTREE_TEXT_PATTERN = "10049:0:100"


def index_row(
    name,
    columns,
    *,
    table="orders",
    method="btree",
    is_unique=False,
    include=(),
    signatures=None,
    is_partial=False,
    has_expressions=False,
    is_protected=False,
    is_partitioned_index=False,
    is_partition_child=False,
    definition=None,
):
    signatures = list(signatures) if signatures is not None else [BTREE_INT] * len(columns)
    info = {
        "include_columns": list(include),
        "key_signatures": signatures,
        "table_relname": table,
        "is_partial": is_partial,
        "has_expressions": has_expressions,
        "is_protected": is_protected,
        "is_partitioned_index": is_partitioned_index,
        "is_partition_child": is_partition_child,
        "definition_signature": definition or f"{','.join(columns)}|{','.join(signatures)}|{','.join(include)}",
    }
    return ("public", table, name, method, is_unique, list(columns), info)


def test_identical_definitions_are_duplicates_and_the_drop_is_quoted():
    rows = [index_row("a_idx", ["customer_id"]), index_row("z Dup", ["customer_id"])]
    [finding] = find_duplicate_indexes(rows)
    assert finding["recommended_ddl"] == 'DROP INDEX CONCURRENTLY public."z Dup";'


def test_partial_vs_full_index_is_not_a_duplicate():
    rows = [
        index_row("full_idx", ["status"], definition="status|full"),
        index_row("partial_idx", ["status"], is_partial=True, definition="status|WHERE active"),
    ]
    assert find_duplicate_indexes(rows) == []


def test_protected_copy_is_kept_and_never_dropped():
    rows = [
        index_row("orders_pkey", ["id"], is_unique=True, is_protected=True),
        index_row("orders_id_idx", ["id"]),
    ]
    [finding] = find_duplicate_indexes(rows)
    assert "orders_pkey" in finding["suggested_action"]
    assert finding["recommended_ddl"] == "DROP INDEX CONCURRENTLY public.orders_id_idx;"


def test_two_constraint_backed_duplicates_get_no_ddl():
    rows = [
        index_row("uq_a", ["code"], is_unique=True, is_protected=True),
        index_row("uq_b", ["code"], is_unique=True, is_protected=True),
    ]
    [finding] = find_duplicate_indexes(rows)
    assert finding["recommended_ddl"] is None


def test_partition_children_are_ignored():
    rows = [
        index_row("p1_idx", ["a"], is_partition_child=True),
        index_row("p1_idx2", ["a"], is_partition_child=True),
    ]
    assert find_duplicate_indexes(rows) == []


def test_leftmost_prefix_is_redundant():
    rows = [index_row("a_idx", ["a"]), index_row("a_b_idx", ["a", "b"])]
    [finding] = find_redundant_indexes(rows)
    assert finding["recommended_ddl"] == "DROP INDEX CONCURRENTLY public.a_idx;"


def test_prefix_with_different_opclass_is_not_redundant():
    rows = [
        index_row("name_pattern_idx", ["name"], signatures=[BTREE_TEXT_PATTERN]),
        index_row("name_city_idx", ["name", "city"]),
    ]
    assert find_redundant_indexes(rows) == []


def test_expression_partial_include_and_non_btree_are_never_redundant():
    wide = index_row("a_b_idx", ["a", "b"])
    for narrow in (
        index_row("expr_idx", ["lower(a)"], has_expressions=True),
        index_row("partial_idx", ["a"], is_partial=True),
        index_row("incl_idx", ["a"], include=["z"]),
        index_row("hash_idx", ["a"], method="hash"),
    ):
        assert find_redundant_indexes([narrow, wide]) == []


def test_partial_wide_index_does_not_make_a_full_one_redundant():
    rows = [index_row("a_idx", ["a"]), index_row("a_b_partial", ["a", "b"], is_partial=True)]
    assert find_redundant_indexes(rows) == []


def test_unused_index_needs_a_week_of_stats_before_suggesting_a_drop():
    rows = [("public", "orders", "old_idx", 0, 8192)]
    [young] = find_unused_indexes(rows, stats_age_days=0.5)
    assert young["recommended_ddl"] is None
    assert young["severity"] == "unknown"

    [mature] = find_unused_indexes(rows, stats_age_days=30)
    assert mature["recommended_ddl"] == "DROP INDEX CONCURRENTLY public.old_idx;"
    assert mature["severity"] == "attention"


def test_unused_index_covering_a_foreign_key_is_never_dropped():
    rows = [("public", "orders", "orders_customer_idx", 0, 8192)]
    [finding] = find_unused_indexes(
        rows, stats_age_days=30, fk_covering={("public", "orders_customer_idx"): "orders_customer_fk"}
    )
    assert finding["recommended_ddl"] is None
    assert "orders_customer_fk" in finding["summary"]


def fk_row(columns, relkind="r", table="orders"):
    return (f"public.{table}" if table != "orders" else table, "orders_customer_fk", list(columns),
            {"schema": "public", "table_relname": table, "relkind": relkind})


def test_missing_fk_index_ddl_quotes_hostile_column_names():
    [finding] = find_missing_fk_indexes([fk_row(['x"); DROP TABLE t; --'])], [])
    assert finding["recommended_ddl"].endswith('ON public.orders ("x""); DROP TABLE t; --");')


def test_partial_index_does_not_cover_a_foreign_key():
    rows = [index_row("partial_cust_idx", ["customer_id"], is_partial=True)]
    assert len(find_missing_fk_indexes([fk_row(["customer_id"])], rows)) == 1


def test_missing_fk_index_on_partitioned_table_has_no_concurrent_ddl():
    [finding] = find_missing_fk_indexes([fk_row(["customer_id"], relkind="p")], [])
    assert finding["recommended_ddl"] is None
    assert "ATTACH PARTITION" in finding["suggested_action"]


def test_covering_ddl_quotes_every_identifier():
    ddl = build_covering_index_ddl("public", "Order", "wide_idx", ["a", "user"], ["Total"])
    assert ddl == (
        'CREATE INDEX CONCURRENTLY wide_idx_covering ON public."Order" (a, "user") INCLUDE ("Total");\n'
        "DROP INDEX CONCURRENTLY public.wide_idx;"
    )
