"""The backend's logging: one format for every logger, the level from
LOG_LEVEL, the optional file copy (LOG_TO_FILE), and two noise filters.

Style (keep new log calls to it):
- INFO: lifecycle and changes — startup, migrations, schedule changes, a
  target becoming unreachable or reachable again, a problem clearing.
- WARNING: something outside DbButler is wrong (a target query fails, a
  webhook can't be delivered, a stored secret can't be decrypted). For a
  condition that repeats every cycle, report it through ProblemLog below:
  once when it starts or changes, INFO when it clears — never every cycle.
- ERROR (logger.exception): an unexpected failure, i.e. probably a bug. Always
  with the traceback.
- Messages: lowercase, no trailing period, %-style arguments (not f-strings),
  name the target as "target <id>".
- Never log secrets (passwords, tokens, webhook URLs, API keys), query text,
  literal values or row data. Error text from requests/psycopg that can
  carry a secret goes through url_safety.redact_secret first.
"""

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Requests that are only liveness probes: the Docker healthcheck calls
# /health every 5 seconds. Left out of the access log.
QUIET_PATHS = frozenset({"/health", "/health/store", "/version", "/api/version"})

# Third-party loggers that are chatty at INFO. APScheduler logs "Running
# job" and "executed successfully" for every run of every job (the lock
# snapshot runs every 15 seconds); its warnings (a missed or overlapping
# run) still come through.
QUIET_LOGGERS = ("apscheduler", "psycopg.pool")


class _QuietPathsFilter(logging.Filter):
    """Drops uvicorn access-log records for QUIET_PATHS that succeeded."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn.access: (client_addr, method, full_path, http_version, status_code)
        if isinstance(args, tuple) and len(args) == 5:
            path = str(args[2]).split("?", 1)[0]
            if path in QUIET_PATHS and isinstance(args[4], int) and args[4] < 400:
                return False
        return True


def configure_logging(level_name: str, log_to_file: bool, log_dir: str) -> None:
    level = logging.getLevelName(level_name.upper())
    if not isinstance(level, int):
        level = logging.INFO
    formatter = logging.Formatter(LOG_FORMAT)

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(formatter)
    root.setLevel(level)

    # uvicorn sets up its own loggers before importing the app: give them
    # the same format and level, and filter the healthcheck out of access.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        for handler in logger.handlers:
            handler.setFormatter(formatter)
    logging.getLogger("uvicorn.access").addFilter(_QuietPathsFilter())

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    if log_to_file:
        _enable_file_logging(formatter, log_dir)


def _enable_file_logging(formatter: logging.Formatter, log_dir: str) -> None:
    """Mirrors every log line into {log_dir}/backend.log (rotating). The
    "uvicorn" and "uvicorn.access" loggers don't propagate to the root
    logger, so they get the handler directly — without that, a request's 500
    traceback (logged by uvicorn.error, which stops at "uvicorn") never
    reached the file. Failure to open the file never stops the app."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, "backend.log"), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
    except OSError:
        logging.getLogger(__name__).exception("LOG_TO_FILE is on but the log file can't be opened in %s", log_dir)
        return
    handler.setFormatter(formatter)
    for name in ("", "uvicorn", "uvicorn.access"):
        logging.getLogger(name).addHandler(handler)


_audit_logger = logging.getLogger("app.audit")


def audit(action: str, target, detail: str = "", error: BaseException | str | None = None) -> None:
    """One line per action someone took that changes a monitored database
    or DbButler's own setup (VACUUM, cancel a query, apply an index, add a
    connection, ...): INFO when it worked, WARNING when it failed. `detail`
    names what was acted on (schema.table, pid, index name) — never SQL
    text, credentials or data."""
    where = f"target {target}" if target is not None else "dbbutler"
    subject = f" {detail}" if detail else ""
    if error is None:
        _audit_logger.info("%s: %s%s", where, action, subject)
    else:
        reason = first_line(error) if isinstance(error, BaseException) else error
        _audit_logger.warning("%s: %s%s failed: %s", where, action, subject, reason)


def first_line(exc: BaseException, limit: int = 300) -> str:
    """An exception's first message line, for a one-line log message (a
    psycopg error's text continues with LINE/HINT/query context)."""
    text = str(exc).strip().splitlines()
    return text[0][:limit] if text else type(exc).__name__


class ProblemLog:
    """Logs a recurring problem once instead of every cycle.

    problem(key, msg, *args) logs a WARNING the first time a key fails, and
    again only when its message changes; while it keeps failing the same way
    it logs at DEBUG. resolved(key, msg, *args) logs one INFO when a key
    that was failing works again, and nothing otherwise. Thread-safe: the
    scheduler's per-target workers share one instance."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._active: dict[tuple, str] = {}
        self._lock = threading.Lock()

    def problem(self, key: tuple, msg: str, *args) -> None:
        text = msg % args if args else msg
        with self._lock:
            previous = self._active.get(key)
            self._active[key] = text
        if previous == text:
            self._logger.debug(msg, *args)
        else:
            self._logger.warning(msg, *args)

    def resolved(self, key: tuple, msg: str, *args) -> None:
        with self._lock:
            was_failing = self._active.pop(key, None) is not None
        if was_failing:
            self._logger.info(msg, *args)
