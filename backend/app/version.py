"""The app's release version, read from the repo-root `.version` file — the
one place it's bumped. The file sits two levels above this module in a
checkout (repo/.version vs repo/backend/app/version.py) but one level above
in the backend image (/app/.version vs /app/app/version.py, see
backend/Dockerfile), so the nearest ancestor holding one wins."""

from pathlib import Path

UNKNOWN_VERSION = "unknown"


def _read_version() -> str:
    for directory in Path(__file__).resolve().parents:
        candidate = directory / ".version"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or UNKNOWN_VERSION
    return UNKNOWN_VERSION


APP_VERSION = _read_version()
