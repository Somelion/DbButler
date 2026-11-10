import importlib

import pytest

# Every module touched by the Sprint 1 hardening — importing them catches a
# wrong cross-module import name, which ruff's per-file checks can't see.
MODULES = [
    "app.ai_providers",
    "app.alerting",
    "app.auth",
    "app.config",
    "app.index_analysis",
    "app.pg_probe",
    "app.query_anonymizer",
    "app.query_guard",
    "app.replication_advisor",
    "app.scheduler",
    "app.severity",
    "app.sql_ident",
    "app.table_health_advisor",
    "app.target_conn",
    "app.url_safety",
    "app.routers.ai_settings",
    "app.routers.alert_settings",
    "app.routers.dashboard",
    "app.routers.explain",
    "app.routers.index_advisor",
    "app.routers.index_coverage_analysis",
    "app.routers.index_testing",
    "app.routers.maintenance",
    "app.routers.query_analysis",
    "app.routers.table_health",
    "app.routers.wait_events",
    "app.main",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def test_index_advisor_queries_render():
    # The catalog queries are built with two .format() passes (internal
    # schema pattern, then the per-request schema filter) — both must leave
    # valid placeholders behind.
    from app.routers import index_advisor
    from app.schema_filter import schema_filter_sql

    for query, column in (
        (index_advisor.FK_COLUMNS_QUERY, "n.nspname"),
        (index_advisor.ALL_INDEX_COLUMNS_QUERY, "n.nspname"),
        (index_advisor.UNUSED_INDEXES_QUERY, "s.schemaname"),
        (index_advisor.INVALID_INDEXES_QUERY, "n.nspname"),
    ):
        rendered = query.format(schema_filter=schema_filter_sql(column, ["public"]))
        assert "{" not in rendered and "}" not in rendered
        assert "_timescaledb" in rendered
        assert f"AND {column} = ANY(%s)" in rendered
