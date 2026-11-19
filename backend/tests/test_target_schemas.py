"""Schema listing for the Connections screen's schema picker: every user
schema with its table count, from a saved connection or from the
add-connection form's credentials. Fake connections stand in for the
database."""

import uuid

import psycopg
import pytest
from fastapi import HTTPException

from app.routers import targets
from app.schemas import ConnectionTestRequest

TARGET_ID = uuid.UUID("8b0f4c1e-1111-4222-8333-944455556666")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


ROWS = [("billing", 12), ("public", 3), ("staging", 0)]


def test_saved_connection_lists_every_schema(monkeypatch):
    conn = FakeConn(ROWS)
    monkeypatch.setattr(targets, "connect_to_target", lambda target_id: conn)

    result = targets.list_target_schemas(TARGET_ID)

    assert [(s.name, s.table_count) for s in result.schemas] == ROWS
    # The allowlist is chosen from this list, so it must not be filtered by it.
    assert "allowed_schemas" not in conn.cur.executed[0]


def test_new_connection_uses_the_form_credentials(monkeypatch):
    calls = []

    def fake_open(*args):
        calls.append(args)
        return FakeConn(ROWS[:1])

    monkeypatch.setattr(targets, "open_target_connection", fake_open)
    payload = ConnectionTestRequest(host="db1", port=5433, dbname="billing", username="monitor", password="pw")

    result = targets.list_new_connection_schemas(payload)

    assert calls == [("db1", 5433, "billing", "monitor", "pw", "prefer")]
    assert [s.name for s in result.schemas] == ["billing"]


def test_unreachable_database_is_a_502(monkeypatch):
    def refuse(target_id):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(targets, "connect_to_target", refuse)

    with pytest.raises(HTTPException) as caught:
        targets.list_target_schemas(TARGET_ID)
    assert caught.value.status_code == 502
