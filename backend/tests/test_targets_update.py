"""PATCH /api/targets/{id}: changing the login (user and/or password) is
tested first and only saved when the test passes. A fake store and a stubbed
connection test stand in for the databases."""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.crypto import encrypt
from app.routers import targets
from app.schemas import TargetUpdate

TARGET_ID = uuid.UUID("8b0f4c1e-1111-4222-8333-944455556666")
NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


def returned_row(username):
    return (TARGET_ID, "Billing", "db1", 5432, "billing", username, "prefer", "17.2", True, NOW, None, True, NOW, None)


class FakeCursor:
    def __init__(self, stored_password="old-secret", stored_user="monitor"):
        self.stored = ("db1", 5432, "billing", stored_user, encrypt(stored_password), "prefer")
        self.executed = []
        self.current = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        if sql.lstrip().startswith("SELECT host"):
            self.current = self.stored
        else:
            self.current = returned_row(params[3])

    def fetchone(self):
        return self.current


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


@pytest.fixture
def store(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConn(cursor)
    monkeypatch.setattr(targets, "store_conn", lambda: conn)
    return cursor, conn


def stub_test(monkeypatch, ok=True, message=None):
    calls = []

    def fake_test(host, port, dbname, username, password, sslmode):
        calls.append((username, password))
        return {"ok": ok, "pg_version": "17.2" if ok else None, "message": message}

    monkeypatch.setattr(targets, "test_connection", fake_test)
    return calls


def test_new_user_keeps_the_stored_password(store, monkeypatch):
    cursor, conn = store
    calls = stub_test(monkeypatch)
    out = targets.update_target(TARGET_ID, TargetUpdate(username="  dbbutler_ro  "))
    assert calls == [("dbbutler_ro", "old-secret")]  # tested with the stored password
    assert out.username == "dbbutler_ro"
    update_sql, params = cursor.executed[-1]
    assert "username = %s" in update_sql and params[3] == "dbbutler_ro"
    assert conn.committed


def test_new_user_and_password_together(store, monkeypatch):
    calls = stub_test(monkeypatch)
    targets.update_target(TARGET_ID, TargetUpdate(username="reporter", password="new-secret"))
    assert calls == [("reporter", "new-secret")]


def test_password_only_keeps_the_user(store, monkeypatch):
    cursor, _conn = store
    calls = stub_test(monkeypatch)
    targets.update_target(TARGET_ID, TargetUpdate(password="new-secret"))
    assert calls == [("monitor", "new-secret")]
    assert cursor.executed[-1][1][3] == "monitor"


def test_a_failing_login_is_not_saved(store, monkeypatch):
    cursor, conn = store
    stub_test(monkeypatch, ok=False, message='password authentication failed for user "typo"')
    with pytest.raises(HTTPException) as refused:
        targets.update_target(TARGET_ID, TargetUpdate(username="typo"))
    assert refused.value.status_code == 422
    assert "typo" in refused.value.detail and "Nothing was changed" in refused.value.detail
    assert not any(sql.startswith("UPDATE") for sql, _p in cursor.executed)
    assert not conn.committed


def test_empty_user_is_refused(store, monkeypatch):
    stub_test(monkeypatch)
    with pytest.raises(HTTPException) as refused:
        targets.update_target(TARGET_ID, TargetUpdate(username="   "))
    assert refused.value.status_code == 400


def test_undecryptable_password_needs_a_new_one_with_the_new_user(store, monkeypatch):
    cursor, _conn = store
    cursor.stored = ("db1", 5432, "billing", "monitor", "not-a-fernet-token", "prefer")
    stub_test(monkeypatch)
    with pytest.raises(HTTPException) as refused:
        targets.update_target(TARGET_ID, TargetUpdate(username="reporter"))
    assert refused.value.status_code == 409
