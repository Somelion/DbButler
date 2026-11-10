from app.query_anonymizer import (
    LiteralMasker,
    anonymize_plan_text,
    anonymize_query_and_plan,
    build_identifier_map,
)

PLAN = """Index Scan using customers_email_idx on customers c  (cost=0.29..8.31 rows=1 width=40) (actual time=0.010..0.011 rows=1 loops=1)
  Index Cond: (email = 'alice@example.com'::text)
  Filter: (rows > 42)
  Rows Removed by Filter: 7
  Buffers: shared hit=3
Planning Time: 0.120 ms
Execution Time: 0.030 ms"""

QUERY = "SELECT c.rows FROM customers c WHERE c.email = 'alice@example.com' AND c.rows > 42"


class _FakeCursor:
    """Returns IDENTIFIER_QUERY rows: (schema, relname, relkind, attname)."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql):
        pass

    def fetchall(self):
        return self.rows


CATALOG = [
    ("public", "customers", "r", "email"),
    ("public", "customers", "r", "rows"),
    ("public", "customers_email_idx", "i", None),
]


def test_literals_are_masked_consistently_in_query_and_plan():
    masker = LiteralMasker()
    query = masker.mask_query(QUERY)
    plan = masker.mask_plan(PLAN)
    assert "alice@example.com" not in query and "alice@example.com" not in plan
    assert "'str_1'" in query and "'str_1'::text" in plan
    assert "> num_1" in query and "> num_1" in plan


def test_plan_numbers_outside_conditions_are_kept():
    plan = LiteralMasker().mask_plan(PLAN)
    assert "rows=1 width=40" in plan
    assert "Rows Removed by Filter: 7" in plan
    assert "shared hit=3" in plan
    assert "Execution Time: 0.030 ms" in plan


def test_identifier_map_includes_index_names():
    mapping = build_identifier_map(_FakeCursor(CATALOG), QUERY, PLAN)
    assert mapping["customers"].startswith("table_")
    assert mapping["customers_email_idx"].startswith("index_")
    assert mapping["email"].startswith("column_")


def test_plan_vocabulary_survives_a_column_named_rows():
    mapping = build_identifier_map(_FakeCursor(CATALOG), PLAN)
    anonymized = anonymize_plan_text(PLAN, mapping)
    assert "rows=1 width=40" in anonymized
    assert "Rows Removed by Filter: 7" in anonymized
    assert "Index Scan using " in anonymized
    assert "customers" not in anonymized
    assert f"({mapping['rows']} > 42)" in anonymized


def test_end_to_end_masks_and_restores():
    result = anonymize_query_and_plan(_FakeCursor(CATALOG), QUERY, PLAN)
    for secret in ("alice@example.com", "customers", "email", "customers_email_idx"):
        assert secret not in result.query
        assert secret not in result.plan

    ai_answer = f"Try: SELECT * FROM {result.identifiers['customers']} WHERE {result.identifiers['email']} = 'str_1'"
    restored = result.restore(ai_answer)
    assert restored == "Try: SELECT * FROM customers WHERE email = 'alice@example.com'"


def test_stored_map_shape():
    result = anonymize_query_and_plan(_FakeCursor(CATALOG), QUERY, PLAN)
    stored = result.stored_map()
    assert stored["strings"] == {"str_1": "'alice@example.com'"}
    assert stored["numbers"] == {"num_1": "42"}
