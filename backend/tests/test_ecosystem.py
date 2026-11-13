"""Sprint 8: the MCP server's protocol handling, the integration tokens, the
Prometheus exposition, the weekly report's rendering and webhook message.
Nothing here needs a database: tools are swapped for stubs where a call
would reach the store."""

import base64
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app import mcp_server, mcp_tools
from app.alerting import format_report_payload
from app.config import settings
from app.health_report import overall_severity, render_report_html, report_summary_lines, report_title
from app.main import app
from app.mcp_server import HEADER_MISMATCH, MODERN_VERSION, UNSUPPORTED_VERSION, handle
from app.mcp_tools import TOOLS, Tool, ToolError, validate_arguments
from app.prometheus import Family, render

MCP_TOKEN = "mcp-test-token-" + "1" * 48
METRICS_TOKEN = "metrics-test-token-" + "2" * 48


def legacy(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def modern(method, params=None, request_id=1):
    meta = {
        "io.modelcontextprotocol/protocolVersion": MODERN_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {**(params or {}), "_meta": meta}}


def modern_headers(method, name=None, version=MODERN_VERSION):
    headers = {"mcp-protocol-version": version, "mcp-method": method}
    if name is not None:
        headers["mcp-name"] = name
    return Headers(headers)


@pytest.fixture
def echo_tool(monkeypatch):
    calls = []

    def handler(args):
        calls.append(args)
        if args["n"] == 13:
            raise ToolError("unlucky")
        if args["n"] == 99:
            raise RuntimeError("boom")
        return {"n": args["n"], "at": datetime(2026, 11, 9, tzinfo=timezone.utc)}

    tool = Tool(
        "echo",
        "Echo",
        "test",
        {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0, "maximum": 100}}, "required": ["n"], "additionalProperties": False},
        handler,
    )
    monkeypatch.setitem(mcp_tools.TOOLS_BY_NAME, "echo", tool)
    monkeypatch.setattr(mcp_server, "TOOLS_BY_NAME", mcp_tools.TOOLS_BY_NAME)
    return calls


# --- MCP: legacy era -----------------------------------------------------------


def test_legacy_initialize_negotiates_a_version():
    status, body = handle(legacy("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}), Headers({}))
    assert status == 200
    result = body["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "dbbutler"
    # An unknown version gets our latest legacy one back.
    _status, body = handle(legacy("initialize", {"protocolVersion": "2024-11-05"}), Headers({}))
    assert body["result"]["protocolVersion"] == "2025-11-25"


def test_notifications_and_responses_are_accepted_without_a_body():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, Headers({})) == (202, None)
    assert handle({"jsonrpc": "2.0", "id": 5, "result": {}}, Headers({})) == (202, None)


def test_batches_and_garbage_are_rejected():
    assert handle([legacy("ping")], Headers({}))[0] == 400
    assert handle({"id": 1, "method": "ping"}, Headers({}))[0] == 400


def test_legacy_tools_list_has_every_tool_read_only():
    status, body = handle(legacy("tools/list"), Headers({"mcp-protocol-version": "2025-11-25"}))
    assert status == 200
    tools = body["result"]["tools"]
    assert [t["name"] for t in tools] == [t.name for t in TOOLS]
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
    assert "resultType" not in body["result"]


def test_legacy_unknown_header_version_is_400():
    status, body = handle(legacy("tools/list"), Headers({"mcp-protocol-version": "1999-01-01"}))
    assert status == 400
    assert body["error"]["code"] == UNSUPPORTED_VERSION


def test_tool_call_results(echo_tool):
    _s, body = handle(legacy("tools/call", {"name": "echo", "arguments": {"n": 4}}), Headers({}))
    result = body["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"n": 4, "at": "2026-11-09T00:00:00+00:00"}
    assert '"n": 4' in result["content"][0]["text"]

    _s, body = handle(legacy("tools/call", {"name": "echo", "arguments": {"n": 13}}), Headers({}))
    assert body["result"] == {"content": [{"type": "text", "text": "unlucky"}], "isError": True}

    _s, body = handle(legacy("tools/call", {"name": "echo", "arguments": {"n": 99}}), Headers({}))
    assert body["result"]["isError"] is True
    assert "boom" not in body["result"]["content"][0]["text"]  # internals stay in the log

    _s, body = handle(legacy("tools/call", {"name": "echo", "arguments": {"n": 500}}), Headers({}))
    assert body["result"]["isError"] is True and "between" in body["result"]["content"][0]["text"]

    _s, body = handle(legacy("tools/call", {"name": "nope", "arguments": {}}), Headers({}))
    assert body["error"]["code"] == -32602


# --- MCP: modern era -------------------------------------------------------------


def test_modern_discover():
    status, body = handle(modern("server/discover"), modern_headers("server/discover"))
    assert status == 200
    result = body["result"]
    assert result["resultType"] == "complete"
    assert MODERN_VERSION in result["supportedVersions"] and "2025-11-25" in result["supportedVersions"]
    assert result["capabilities"] == {"tools": {}}
    assert result["cacheScope"] == "private" and result["ttlMs"] > 0


def test_modern_tools_list_and_call(echo_tool):
    _s, body = handle(modern("tools/list"), modern_headers("tools/list"))
    assert body["result"]["resultType"] == "complete"
    assert "ttlMs" in body["result"]
    _s, body = handle(modern("tools/call", {"name": "echo", "arguments": {"n": 1}}), modern_headers("tools/call", "echo"))
    assert body["result"]["resultType"] == "complete"
    assert body["result"]["structuredContent"]["n"] == 1


def test_modern_header_mismatches():
    status, body = handle(modern("tools/list"), modern_headers("tools/call"))
    assert (status, body["error"]["code"]) == (400, HEADER_MISMATCH)
    status, body = handle(modern("tools/list"), Headers({"mcp-method": "tools/list"}))
    assert (status, body["error"]["code"]) == (400, HEADER_MISMATCH)
    status, body = handle(
        modern("tools/call", {"name": "list_targets", "arguments": {}}), modern_headers("tools/call", "get_target_overview")
    )
    assert (status, body["error"]["code"]) == (400, HEADER_MISMATCH)
    # A modern header without the body's _meta is a mismatch too.
    status, body = handle(legacy("tools/list"), modern_headers("tools/list"))
    assert (status, body["error"]["code"]) == (400, HEADER_MISMATCH)


def test_modern_base64_tool_name_header(echo_tool):
    encoded = "=?base64?" + base64.b64encode(b"echo").decode() + "?="
    _s, body = handle(modern("tools/call", {"name": "echo", "arguments": {"n": 2}}), modern_headers("tools/call", encoded))
    assert body["result"]["isError"] is False


def test_modern_unsupported_version_lists_supported():
    message = modern("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2030-01-01"
    status, body = handle(message, modern_headers("tools/list", version="2030-01-01"))
    assert status == 400
    assert body["error"]["code"] == UNSUPPORTED_VERSION
    assert body["error"]["data"]["requested"] == "2030-01-01"
    assert MODERN_VERSION in body["error"]["data"]["supported"]


def test_modern_unknown_method_is_404():
    status, body = handle(modern("resources/list"), modern_headers("resources/list"))
    assert status == 404
    assert body["error"]["code"] == -32601


# --- Tool arguments ----------------------------------------------------------------


def test_validate_arguments_defaults_and_errors():
    schema = TOOLS[2].input_schema  # list_findings
    assert validate_arguments(schema, {"target": "demo"}) == {"target": "demo", "severity": "any", "status": "open", "limit": 25}
    with pytest.raises(ToolError, match="Unknown argument"):
        validate_arguments(schema, {"target": "demo", "sql": "DROP TABLE x"})
    with pytest.raises(ToolError, match="one of"):
        validate_arguments(schema, {"target": "demo", "status": "everything"})
    with pytest.raises(ToolError, match="Missing"):
        validate_arguments(schema, {})
    with pytest.raises(ToolError, match="integer"):
        validate_arguments(schema, {"target": "demo", "limit": True})


def test_no_tool_takes_free_form_sql_except_the_linter():
    for tool in TOOLS:
        properties = tool.input_schema["properties"]
        if tool.name == "lint_migration":
            assert "sql" in properties  # parsed and analysed, never executed
        else:
            assert "sql" not in properties and "query" not in properties, tool.name


class FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.results.pop(0)


def test_resolve_target_by_name_and_errors():
    target_id = "8b0f4c1e-1111-4222-8333-944455556666"
    cur = FakeCursor([[(target_id, "Billing")]])
    assert mcp_tools.resolve_target(cur, "billing") == (target_id, "Billing")
    cur = FakeCursor([[], [], [("Billing",), ("Shop",)]])
    with pytest.raises(ToolError, match="Billing, Shop"):
        mcp_tools.resolve_target(cur, "nope")
    cur = FakeCursor([[(1, "a"), (2, "b")], [("a",), ("b",)]])
    with pytest.raises(ToolError, match="several"):
        mcp_tools.resolve_target(cur, "dup")


# --- HTTP: tokens and the endpoints ------------------------------------------------


@pytest.fixture
def integration_tokens(monkeypatch):
    monkeypatch.setattr(settings, "mcp_token", MCP_TOKEN)
    monkeypatch.setattr(settings, "metrics_token", METRICS_TOKEN)


def test_integration_endpoints_are_off_without_their_token(monkeypatch):
    monkeypatch.setattr(settings, "mcp_token", "")
    monkeypatch.setattr(settings, "metrics_token", "")
    client = TestClient(app)
    assert client.post("/api/mcp", json=legacy("ping")).status_code == 404
    assert client.get("/metrics").status_code == 404


def test_integration_tokens_are_not_interchangeable(integration_tokens):
    import os

    client = TestClient(app)
    auth_token = {"Authorization": f"Bearer {os.environ['AUTH_TOKEN']}"}
    assert client.post("/api/mcp", json=legacy("ping"), headers=auth_token).status_code == 401
    assert client.post("/api/mcp", json=legacy("ping"), headers={"Authorization": f"Bearer {METRICS_TOKEN}"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": f"Bearer {MCP_TOKEN}"}).status_code == 401
    # ... and the MCP token opens nothing else.
    assert client.get("/api/auth/check", headers={"Authorization": f"Bearer {MCP_TOKEN}"}).status_code == 401


def test_mcp_http_round_trip(integration_tokens):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {MCP_TOKEN}"}
    response = client.post("/api/mcp", json=legacy("ping"), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers).status_code == 202
    assert client.get("/api/mcp", headers=headers).status_code == 405
    bad = client.post("/api/mcp", content=b"{not json", headers={**headers, "Content-Type": "application/json"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700


def test_mcp_rejects_foreign_origins(integration_tokens):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {MCP_TOKEN}", "Origin": "https://evil.example"}
    assert client.post("/api/mcp", json=legacy("ping"), headers=headers).status_code == 403
    allowed = settings.cors_allowed_origins.split(",")[0].strip()
    headers["Origin"] = allowed
    assert client.post("/api/mcp", json=legacy("ping"), headers=headers).status_code == 200


def test_mcp_rejects_oversized_and_method_less_messages(integration_tokens):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {MCP_TOKEN}", "Content-Type": "application/json"}
    big = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + b"a" * (1024 * 1024) + b'"}}'
    assert client.post("/api/mcp", content=big, headers=headers).status_code == 413
    status, body = handle({"jsonrpc": "2.0", "id": 1}, Headers({}))
    assert status == 400 and body["error"]["code"] == -32600


# --- Fake store: the store-reading paths without a database ----------------------


class FakeStoreCursor:
    """Answers each execute() with the next canned result set, and records
    the SQL it was given."""

    def __init__(self, results):
        self.results = list(results)
        self.executed = []
        self.current = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self.current = self.results.pop(0) if self.results else []

    def fetchall(self):
        return self.current

    def fetchone(self):
        return self.current[0] if self.current else None


class FakeStoreConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        pass


def fake_store(monkeypatch, module, results):
    cursor = FakeStoreCursor(results)
    monkeypatch.setattr(module, "store_conn", lambda: FakeStoreConn(cursor))
    return cursor


TARGET_ID = "8b0f4c1e-1111-4222-8333-944455556666"

XMIN_FINDING = {
    "id": "xmin-holder-backend-4242",
    "category": "xmin horizon",
    "severity": "critical",
    "title": "Session 4242 is holding back vacuum",
    "summary": "Session 4242 (jane.doe, idle in transaction) has kept the same transaction or snapshot open for 2h.",
    "detail": "pid=4242 database=billing user=jane.doe state=idle in transaction xmin_age=5 held_seconds=7200",
    "suggested_action": "Open Activity to see what it's running.",
}
PREPARED_FINDING = {
    **XMIN_FINDING,
    "id": "xmin-holder-prepared-gx",
    "summary": "A two-phase transaction was prepared 3h ago.",
    "detail": "gid=gx database=billing owner=jane.doe xmin_age=5 held_seconds=9",
    "suggested_action": "connect to database billing as jane.doe (or a superuser) and run ROLLBACK PREPARED 'gx';",
}


def test_session_user_names_never_leave_through_mcp(monkeypatch):
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    fake_store(
        monkeypatch,
        mcp_tools,
        [
            [(TARGET_ID, "Billing")],  # resolve_target
            [(XMIN_FINDING, now, now, None), (PREPARED_FINDING, now, now, None)],
            [("xmin horizon",)],
        ],
    )
    _status, body = handle(legacy("tools/call", {"name": "list_findings", "arguments": {"target": TARGET_ID}}), Headers({}))
    result = body["result"]
    assert result["isError"] is False
    assert "jane.doe" not in result["content"][0]["text"]
    assert "jane.doe" not in str(result["structuredContent"])
    first, second = result["structuredContent"]["findings"]
    assert first["summary"].startswith("Session 4242 has kept")
    assert "user=(hidden)" in first["detail"]
    assert "owner=(hidden)" in second["detail"]
    assert "as its owner (or a superuser)" in second["suggested_action"]


def test_list_findings_filters_are_parameters(monkeypatch):
    cursor = fake_store(monkeypatch, mcp_tools, [[(TARGET_ID, "Billing")], [], []])
    mcp_tools.list_findings({"target": "billing", "severity": "critical", "category": "x' OR 1=1 --", "status": "open", "limit": 5})
    sql, params = cursor.executed[1]
    assert "x' OR 1=1" not in sql
    assert params == (TARGET_ID, "open", "critical", "x' OR 1=1 --", 5)


def test_get_top_queries_masks_literals_and_orders_by_choice(monkeypatch):
    cursor = fake_store(
        monkeypatch,
        mcp_tools,
        [[(TARGET_ID, "Billing")], [(42, "SELECT * FROM t WHERE email = 'jane@example.com'", 10, 50.0, 5.0, 10)]],
    )
    result = mcp_tools.get_top_queries({"target": TARGET_ID, "hours": 24, "order_by": "calls", "limit": 10})
    assert "jane@example.com" not in result["queries"][0]["query"]
    assert result["queries"][0]["queryid"] == "42"
    assert "ORDER BY 3 DESC" in cursor.executed[1][0]


def test_prometheus_families_from_the_store(monkeypatch):
    from app.routers import prometheus

    other = "11111111-2222-4333-8444-555555555555"
    fake_store(
        monkeypatch,
        prometheus,
        [
            [(TARGET_ID, "Billing", "billing", "16.4"), (other, "Shop", "shop", "17.0")],
            [(TARGET_ID, "critical", "locks", 2), (TARGET_ID, "attention", "bloat", 1)],
            [(TARGET_ID, "connection_count", 12.0)],
            [(TARGET_ID, 3, 40)],
            [(TARGET_ID, "collection", True, 1762675200.0, True), (other, "collection", True, 1762670000.0, False)],
        ],
    )
    text = render(prometheus.collect_families())
    assert f'dbbutler_target_up{{target_id="{TARGET_ID}",target="Billing",dbname="billing"}} 1' in text
    # A success older than the up window doesn't count.
    assert f'dbbutler_target_up{{target_id="{other}",target="Shop",dbname="shop"}} 0' in text
    assert f'dbbutler_open_findings_by_severity{{target_id="{other}",target="Shop",dbname="shop",severity="critical"}} 0' in text
    assert 'severity="critical",category="locks"} 2' in text
    assert f'dbbutler_connections{{target_id="{TARGET_ID}",target="Billing",dbname="billing"}} 12' in text
    assert 'dbbutler_lock_blocked_sessions{target_id="' + other + '",target="Shop",dbname="shop"} 0' in text
    assert 'collector="collection"' in text and 'job="' not in text


def test_alert_settings_saves_the_report_switch(monkeypatch):
    from app.routers import alert_settings

    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    cursor = fake_store(monkeypatch, alert_settings, [[], [], [(False, None, "critical", True, now)]])
    out = alert_settings.update_alert_settings(
        alert_settings.AlertSettingsUpdate(enabled=False, min_severity="critical", report_webhook_enabled=True)
    )
    assert out.report_webhook_enabled is True
    assert any("report_webhook_enabled = %s" in sql and params == (True,) for sql, params in cursor.executed)
    # An older client that doesn't send the field leaves it alone.
    cursor = fake_store(monkeypatch, alert_settings, [[], [(False, None, "critical", True, now)]])
    alert_settings.update_alert_settings(alert_settings.AlertSettingsUpdate(enabled=False))
    assert not any("report_webhook_enabled" in sql for sql, _p in cursor.executed[:-1])


# --- Prometheus ----------------------------------------------------------------------


def test_prometheus_rendering_escapes_labels():
    family = Family("dbbutler_open_findings", "Open findings.\nBy severity.")
    family.add({"target": 'we"ird\\name\nx', "severity": "critical"}, 3)
    family.add({"target": "b"}, None)  # skipped
    family.add({"target": "c"}, 0.25)
    empty = Family("dbbutler_lock_max_wait_seconds", "Longest wait.")
    text = render([family, empty])
    assert "# HELP dbbutler_open_findings Open findings.\\nBy severity." in text
    assert "# TYPE dbbutler_open_findings gauge" in text
    assert 'dbbutler_open_findings{target="we\\"ird\\\\name\\nx",severity="critical"} 3\n' in text
    assert 'dbbutler_open_findings{target="c"} 0.25' in text
    assert "# TYPE dbbutler_lock_max_wait_seconds gauge" in text
    assert text.endswith("\n")


# --- Weekly report ---------------------------------------------------------------------


def sample_report():
    return {
        "target": {"id": "t1", "name": "Billing <prod>", "host": "db1", "port": 5432, "dbname": "billing", "pg_version": "16.4"},
        "period": {"start": "2026-11-02T07:00:00+00:00", "end": "2026-11-09T07:00:00+00:00"},
        "findings": {
            "open_by_severity": {"critical": 1, "attention": 2},
            "open_total": 3,
            "open_by_category": [],
            "top_open": [
                {
                    "finding_id": "f1",
                    "category": "locks",
                    "severity": "critical",
                    "title": "<script>alert(1)</script>",
                    "summary": "s",
                    "suggested_action": "Fix it",
                    "first_seen_at": "2026-11-05T10:00:00+00:00",
                }
            ],
            "opened": 4,
            "resolved": 2,
            "new": [],
        },
        "metrics": [
            {"metric": "database_size_bytes", "label": "Database size", "unit": "bytes", "min": 1.0e9, "avg": 1.1e9,
             "max": 1.2e9, "first": 1.0e9, "last": 1.2e9, "previous_avg": 1.0e9},
            {"metric": "cache_hit_ratio", "label": "Cache hit rate", "unit": "%", "min": 91.5, "avg": 98.2,
             "max": 99.9, "first": 98.0, "last": 99.0, "previous_avg": None},
        ],
        "top_queries": [
            {"queryid": "42", "query": "SELECT * FROM orders WHERE id = $1", "calls": 1000, "total_ms": 5000.0,
             "mean_ms": 5.0, "share_pct": 61.0, "previous_total_ms": 2500.0}
        ],
        "total_db_time_ms": 8200.0,
        "plan_changes": 2,
        "plan_regressions": [],
        "lock_incidents": {"count": 1, "longest_wait_seconds": 252, "max_blocked": 3,
                           "top": [{"start": "2026-11-06T12:00:00+00:00", "duration_seconds": 90, "max_blocked": 3, "max_wait_seconds": 252}]},
        "events": {"restarts": ["2026-11-07T03:00:00+00:00"], "stats_resets": [], "config_changes": [{"at": "2026-11-07T03:00:00+00:00", "settings": ["work_mem"]}]},
        "table_growth": [{"table": "public.orders", "start_bytes": 100, "end_bytes": 300, "growth_bytes": 200}],
        "collector_health": [{"job": "collection", "runs": 100, "errors": 1, "error_pct": 1.0}],
    }


def test_report_summary_lines():
    data = sample_report()
    assert overall_severity(data) == "critical"
    lines = report_summary_lines(data)
    assert lines[0] == "Open findings: 1 critical, 2 attention (4 new this week, 2 resolved)"
    assert "Database size: 1.1 GB (+190.7 MB this week)" in lines
    assert "Lock incidents: 1 (longest wait 4m12s, up to 3 sessions blocked)" in lines
    assert "Events: 1 restarts, 1 configuration changes" in lines
    assert report_title(data) == "Weekly health report — Billing <prod> — 2026-11-02 to 2026-11-09"


def test_report_html_is_escaped_and_self_contained():
    page = render_report_html(sample_report(), datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc))
    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Billing &lt;prod&gt;" in page
    assert "http" not in page  # nothing loaded from anywhere
    assert "+100%" in page  # the top query's DB time vs last week
    assert "work_mem" in page


def test_report_webhook_payloads():
    lines = ["Open findings: 1 critical"]
    slack = format_report_payload("slack", "DbButler weekly report — x", lines, "https://d/r", "critical", {})
    assert slack == {"text": "*DbButler weekly report — x*\n• Open findings: 1 critical\nFull report: https://d/r"}
    teams = format_report_payload("teams", "T", lines, "https://d/r", "critical", {})
    assert teams["themeColor"] == "D93F3F"
    assert teams["potentialAction"][0]["targets"][0]["uri"] == "https://d/r"
    generic = format_report_payload("generic", "T", lines, None, "healthy", {"report_id": "r1"})
    assert generic["report"]["report_id"] == "r1" and generic["report"]["link"] is None
    discord = format_report_payload("discord", "T", ["x" * 3000], None, "attention", {})
    assert len(discord["content"]) == 2000


# --- Backup & WAL without pg_monitor ------------------------------------------------


def test_wal_size_is_skipped_without_the_privilege():
    from app.routers.backup_advisor import WAL_DIR_SIZE_QUERY, fetch_wal_dir_bytes

    cursor = FakeStoreCursor([[(False,)]])
    assert fetch_wal_dir_bytes(cursor) is None
    assert all(sql != WAL_DIR_SIZE_QUERY for sql, _p in cursor.executed)  # never attempted
    cursor = FakeStoreCursor([[(True,)], [(123,)]])
    assert fetch_wal_dir_bytes(cursor) == 123


def test_missing_wal_privilege_is_a_notice_not_a_problem():
    from app.backup_advisor import build_backup_status
    from app.severity import worse

    category, findings = build_backup_status("on", 0, None, None, None, None, 1024**3)
    assert category.severity == "healthy"
    assert "needs pg_monitor" in category.detail
    assert [(f["id"], f["severity"]) for f in findings] == [("wal-directory-size-not-visible", "info")]
    assert worse("healthy", "info") == "healthy"


# --- pg_stat_statements in a schema off the search_path -------------------------


def test_pg_stat_statements_in_its_own_schema_is_put_on_the_search_path():
    from app import pg_stat_statements as pss

    cursor = FakeStoreCursor([[("extensions", False, True)], [("$user, public, extensions",)], [("170000",)]])
    assert pss.is_enabled(cursor) is True
    sql, params = cursor.executed[1]
    assert "set_config( 'search_path'" in sql and params == {"schema": "extensions"}

    # Already visible: nothing to change.
    cursor = FakeStoreCursor([[("public", True, True)], [("170000",)]])
    assert pss.is_enabled(cursor) is True
    assert not any("set_config" in sql for sql, _p in cursor.executed)


def test_pg_stat_statements_schema_without_usage_explains_the_grant():
    from app import pg_stat_statements as pss

    cursor = FakeStoreCursor([[("extensions", False, False)]])
    assert pss.is_enabled(cursor) is False
    cursor = FakeStoreCursor([[("extensions", False, False)]])
    assert "GRANT USAGE ON SCHEMA extensions" in pss.unavailable_message(cursor)
    cursor = FakeStoreCursor([[]])
    assert pss.unavailable_message(cursor) == pss.NOT_INSTALLED_MESSAGE


# --- Feature flags ---------------------------------------------------------------


def test_workload_3d_flag_is_off_by_default_and_follows_the_setting(monkeypatch):
    import os

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {os.environ['AUTH_TOKEN']}"}
    assert client.get("/api/features").status_code == 401
    assert client.get("/api/features", headers=auth).json() == {"workload_map_3d": False}
    monkeypatch.setattr(settings, "feature_workload_3d", True)
    assert client.get("/api/features", headers=auth).json() == {"workload_map_3d": True}
