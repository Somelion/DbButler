"""Logging style (app/logging_setup.py): recurring problems logged once,
the healthcheck kept out of the access log, audit lines without secrets,
and the scheduler's job-failure routing."""

import logging

import psycopg

from app import scheduler
from app.logging_setup import ProblemLog, _QuietPathsFilter, audit, first_line

TARGET = "11111111-1111-1111-1111-111111111111"


def _levels(caplog, name):
    return [r.levelname for r in caplog.records if r.name == name]


def test_problem_log_warns_once_then_debug_then_info_on_resolve(caplog):
    logger = logging.getLogger("test.problems")
    problems = ProblemLog(logger)
    caplog.set_level(logging.DEBUG, logger="test.problems")

    problems.resolved(("t", "job"), "works again")  # never failed: silent
    for _ in range(3):
        problems.problem(("t", "job"), "job failed: %s", "boom")
    problems.problem(("t", "job"), "job failed: %s", "other reason")
    problems.resolved(("t", "job"), "works again")
    problems.resolved(("t", "job"), "works again")

    assert _levels(caplog, "test.problems") == ["WARNING", "DEBUG", "DEBUG", "WARNING", "INFO"]


def test_problem_log_keys_are_independent(caplog):
    logger = logging.getLogger("test.problems.keys")
    problems = ProblemLog(logger)
    caplog.set_level(logging.DEBUG, logger="test.problems.keys")

    problems.problem(("a",), "down")
    problems.problem(("b",), "down")

    assert _levels(caplog, "test.problems.keys") == ["WARNING", "WARNING"]


def _access_record(path, status):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", status), None,
    )


def test_quiet_paths_filter_drops_successful_healthchecks_only():
    quiet = _QuietPathsFilter()
    assert quiet.filter(_access_record("/health", 200)) is False
    assert quiet.filter(_access_record("/health?x=1", 200)) is False
    assert quiet.filter(_access_record("/health/store", 503)) is True
    assert quiet.filter(_access_record("/api/targets", 200)) is True


def test_audit_success_and_failure(caplog):
    caplog.set_level(logging.INFO, logger="app.audit")

    audit("cancel query", TARGET, "pid 42")
    audit("drop tested index", TARGET, "public.ix", RuntimeError("lock timeout\nDETAIL: more"))
    audit("weekly report", None)

    lines = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "app.audit"]
    assert lines == [
        ("INFO", f"target {TARGET}: cancel query pid 42"),
        ("WARNING", f"target {TARGET}: drop tested index public.ix failed: lock timeout"),
        ("INFO", "dbbutler: weekly report"),
    ]


def test_first_line_falls_back_to_type_name():
    assert first_line(ValueError("")) == "ValueError"
    assert first_line(ValueError("a\nb")) == "a"


def test_job_failed_routes_by_error_kind(caplog, monkeypatch):
    monkeypatch.setattr(scheduler, "_problems", ProblemLog(scheduler.logger))
    caplog.set_level(logging.DEBUG, logger=scheduler.logger.name)

    try:
        raise scheduler.TargetUnreachable("connection refused")
    except psycopg.Error as exc:
        scheduler._job_failed("metrics collection", TARGET, exc)
    assert caplog.records == []  # _target_session already logged it

    for _ in range(2):
        try:
            raise psycopg.errors.InsufficientPrivilege("permission denied for function pg_ls_waldir")
        except psycopg.Error as exc:
            scheduler._job_failed("live findings", TARGET, exc)
    scheduler._job_ok("live findings", TARGET)

    try:
        raise KeyError("bug")
    except KeyError as exc:
        scheduler._job_failed("plan capture", TARGET, exc)

    records = [(r.levelname, r.exc_info is not None) for r in caplog.records]
    assert records == [("WARNING", False), ("DEBUG", False), ("INFO", False), ("ERROR", True)]


class _StoreSpy:
    def __init__(self):
        self.updates = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.updates.append(params[:2])

    def commit(self):
        pass


def test_target_status_saved_only_when_it_changes(monkeypatch):
    store = _StoreSpy()
    monkeypatch.setattr(scheduler, "store_conn", store)
    monkeypatch.setattr(scheduler, "_reachability", {})
    monkeypatch.setattr(scheduler, "_problems", ProblemLog(scheduler.logger))

    def connect_fails(target_row):
        raise psycopg.OperationalError("connection refused\nIs the server running?")

    monkeypatch.setattr(scheduler, "_connect_target", connect_fails)
    for _ in range(3):
        try:
            with scheduler._target_session((TARGET,)):
                pass
        except scheduler.TargetUnreachable:
            pass

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(scheduler, "_connect_target", lambda target_row: FakeConn())
    for _ in range(2):
        with scheduler._target_session((TARGET,)):
            pass

    assert store.updates == [(False, "connection refused"), (True, None)]
