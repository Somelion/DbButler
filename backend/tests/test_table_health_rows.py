"""Table Health's live/dead counts after a statistics reset
(app/live_tuples.py): the SQL fallback to pg_class.reltuples, the estimate
flag through the row builders, and the TimescaleDB rollup keeping the
16-column row shape."""

from datetime import datetime, timezone

from app.collectors import WORST_TABLE_DEAD_PCT
from app.live_tuples import DEAD_PCT_SQL, LIVE_TUPLES_ESTIMATED_SQL, LIVE_TUPLES_SQL
from app.routers.table_health import TABLE_HEALTH_QUERY, TABLE_HEALTH_SINGLE_QUERY, _build_table_health_row
from app.scheduler import TABLE_METRICS_QUERY
from app.timescaledb_health import aggregate_hypertable_rows


def health_row(schema, table, live, dead, estimated=False, vacuumed=None):
    return (
        schema,
        table,
        live,
        dead,
        vacuumed,
        None,
        None,
        None,
        1000,
        None,
        None,
        1,
        0,
        0,
        0,
        estimated,
    )


def test_every_live_count_query_uses_the_reset_safe_estimate():
    for sql in (TABLE_HEALTH_QUERY, TABLE_HEALTH_SINGLE_QUERY):
        assert LIVE_TUPLES_SQL in sql
        assert LIVE_TUPLES_ESTIMATED_SQL in sql
        assert "{" not in sql  # the format placeholder was filled in
    assert DEAD_PCT_SQL in TABLE_METRICS_QUERY
    assert DEAD_PCT_SQL in WORST_TABLE_DEAD_PCT.query
    assert "c.reltuples" in LIVE_TUPLES_SQL
    assert "last_autoanalyze" in LIVE_TUPLES_SQL


def test_estimated_row_is_flagged_and_not_a_false_bloat_alarm():
    # 2M rows from the catalog estimate, 10 dead since the reset: ~0% dead.
    row = _build_table_health_row(health_row("public", "orders", 2_000_000, 10, estimated=True), {})
    assert row.live_tuples_estimated is True
    assert row.dead_pct < 0.01
    assert row.dead_pct_severity == "healthy"
    plain = _build_table_health_row(health_row("public", "t", 90, 10), {})
    assert plain.live_tuples_estimated is False
    assert plain.dead_pct == 10.0


class ChunkCursor:
    def __init__(self, chunks):
        self.chunks = chunks

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return self.chunks


def test_hypertable_rollup_keeps_the_estimate_flag():
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = [
        health_row("public", "metrics", 0, 0),
        health_row("_timescaledb_internal", "_hyper_1_1_chunk", 500, 5, estimated=True),
        health_row("_timescaledb_internal", "_hyper_1_2_chunk", 700, 7, vacuumed=now),
        health_row("public", "plain", 3, 0),
    ]
    cursor = ChunkCursor(
        [
            ("public", "metrics", "_timescaledb_internal", "_hyper_1_1_chunk"),
            ("public", "metrics", "_timescaledb_internal", "_hyper_1_2_chunk"),
        ]
    )
    out = {(r[0], r[1]): r for r in aggregate_hypertable_rows(rows, cursor)}
    hypertable = out[("public", "metrics")]
    assert len(hypertable) == 16
    assert hypertable[2] == 1200 and hypertable[3] == 12
    assert hypertable[15] is True
    assert out[("public", "plain")][15] is False


# --- Analyze all (routers/maintenance.py) ------------------------------------------


class _Diag:
    def __init__(self, message):
        self.message_primary = message


class FakeAnalyzeConn:
    """ANALYZE on "orders" warns it's skipping (not the owner); on "locked"
    it fails with a lock timeout; everything else just works."""

    broken = False

    def __init__(self):
        self.handler = None
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_notice_handler(self, handler):
        self.handler = handler

    def cursor(self):
        return self

    def execute(self, statement, params=None):
        import psycopg

        text = statement.as_string(None) if hasattr(statement, "as_string") else statement
        self.statements.append(text)
        if '"orders"' in text:
            self.handler(_Diag('permission denied to analyze "orders", skipping it'))
        if '"locked"' in text:
            raise psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")


def test_analyze_schema_job_reports_analyzed_skipped_and_failed(monkeypatch):
    from app.routers import maintenance

    conn = FakeAnalyzeConn()
    monkeypatch.setattr(maintenance, "connect_to_target", lambda *a, **k: conn)
    job_id = "job1"
    now = datetime.now(timezone.utc)
    maintenance._analyze_jobs[job_id] = {
        "job_id": job_id, "target_id": "t", "schema_name": "Sales", "status": "running", "total": 3, "done": 0,
        "analyzed": 0, "current": None, "skipped": [], "failed": [], "error": None, "started_at": now, "finished_at": None,
    }
    try:
        maintenance._run_analyze_schema(job_id, "t", "Sales", ["customers", "orders", "locked"])
        job = maintenance._analyze_jobs[job_id]
        assert job["status"] == "done" and job["done"] == 3
        assert job["analyzed"] == 1
        assert job["skipped"] == ["orders"]
        assert [f["table"] for f in job["failed"]] == ["locked"]
        assert "lock timeout" in job["failed"][0]["error"]
        # Identifiers are quoted: a mixed-case schema survives.
        assert conn.statements[0] == 'ANALYZE "Sales"."customers"'
    finally:
        maintenance._analyze_jobs.pop(job_id, None)
