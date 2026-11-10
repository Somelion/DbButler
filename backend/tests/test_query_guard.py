import pytest

from app.query_guard import ensure_read_only, run_explain_analyze


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1",
        "select * from t where id = 1;",
        "-- comment\nSELECT 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT ';' AS semicolon_in_a_literal",
        "SELECT 'pg_terminate_backend(1)' AS just_text",
        "SELECT '$1' AS not_a_placeholder",
        'SELECT 1 AS "odd;name"',
    ],
)
def test_accepts_single_read_only_statements(query):
    assert ensure_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM t",
        "UPDATE t SET a = 1",
        "SELECT 1; DROP TABLE t",
        "SELECT 1; DELETE FROM t;",
        "SELECT 1 /* ; */ ; DROP TABLE t",
        "SELECT pg_terminate_backend(123)",
        "SELECT PG_CANCEL_BACKEND (123)",
        "SELECT * FROM dblink('host=x', 'DELETE FROM t') AS r(a int)",
        "SELECT set_config('default_transaction_read_only', 'off', false)",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT * FROM t WHERE id = $1",
        "",
    ],
)
def test_rejects_writes_multiple_statements_side_effects_and_placeholders(query):
    with pytest.raises(ValueError):
        ensure_read_only(query)


def test_strips_trailing_semicolon():
    assert ensure_read_only("SELECT 1 ;  ") == "SELECT 1"


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.conn.executed.append((sql, self.conn.read_only))

    def fetchall(self):
        return [("Seq Scan on t",), ("Execution Time: 1.0 ms",)]


class _FakeConn:
    def __init__(self):
        self.read_only = None
        self.executed = []
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rolled_back = True


def test_explain_runs_in_a_read_only_transaction_that_is_always_rolled_back():
    # A data-modifying CTE passes the text check (it starts with WITH) —
    # the READ ONLY transaction is what makes PostgreSQL refuse it.
    conn = _FakeConn()
    plan = run_explain_analyze(conn, "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d")
    assert plan == "Seq Scan on t\nExecution Time: 1.0 ms"
    assert conn.executed[0][1] is True
    assert conn.executed[0][0].startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) WITH d AS")
    assert conn.rolled_back
    assert conn.read_only is None
