"""Backup & WAL Health Advisor analyzers — pure functions over
pg_stat_archiver / pg_ls_waldir() / GUC values (routers/backup_advisor.py).
Another complete blind spot before this: nothing else in the app reads WAL
archiving status or the WAL directory's own size. Actual restore-testing
automation is deliberately out of scope — this app only ever connects to
the target over SQL, with no filesystem/log access to whatever host runs
it, so it can detect that archiving has stopped or that WAL isn't being
cleaned up, but never whether a backup is actually restorable.
"""

from app.schemas import CategoryStatus
from app.severity import wal_archiving_severity, wal_dir_size_severity, worse

CATEGORY = "backup advisor"


def find_archive_mode_off_findings(archive_mode: str) -> list[dict]:
    """archive_mode: 'off' | 'on' | 'always'. Informational (`unknown`), not
    a hard warning: an external tool (pgBackRest, WAL-G) that manages its
    own WAL archiving via a wrapped archive_command would show archive_mode
    on, so 'off' really does usually mean no WAL-based backup strategy at
    all exists — but this app has no way to fully rule out something
    unusual, so it stays a nudge rather than an alarm."""
    if archive_mode != "off":
        return []
    return [
        {
            "id": "wal-archiving-not-configured",
            "category": CATEGORY,
            "severity": "unknown",
            "title": "WAL archiving is not configured",
            "summary": (
                "archive_mode is off, so there's no point-in-time recovery beyond whatever periodic "
                "base backups exist — a base backup alone can only restore to the moment it was taken."
            ),
            "detail": "archive_mode=off",
            "suggested_action": (
                "If an external tool (pgBackRest, WAL-G, etc.) manages backups and its own WAL "
                "archiving separately, this is expected and can be ignored. Otherwise, consider "
                "enabling archive_mode and setting archive_command to get point-in-time recovery."
            ),
        }
    ]


def find_wal_archiving_failure_findings(
    archive_mode: str, failed_count: int, last_archived_time, last_failed_time, last_failed_wal
) -> list[dict]:
    """Skipped entirely when archiving isn't configured (find_archive_mode_
    off_findings above already covers that case) — a failed_count left over
    from before archiving was disabled would otherwise be misleading."""
    if archive_mode == "off":
        return []
    severity = wal_archiving_severity(failed_count, last_archived_time, last_failed_time)
    if severity == "healthy":
        return []

    currently_stuck = severity == "critical"
    title = (
        f"WAL archiving is currently failing (last failure: {last_failed_wal})"
        if currently_stuck
        else f"WAL archiving has failed {failed_count:,} time{'s' if failed_count != 1 else ''} since the last reset"
    )
    summary = (
        "archive_command is currently failing — WAL keeps piling up on disk until this is fixed, and "
        "there's a growing gap in point-in-time recovery coverage."
        if currently_stuck
        else "archive_command has failed before but has since recovered — worth confirming whatever "
        "caused it (destination full, permissions, network) doesn't recur."
    )
    return [
        {
            "id": "wal-archiving-failures",
            "category": CATEGORY,
            "severity": severity,
            "title": title,
            "summary": summary,
            "detail": (
                f"failed_count={failed_count} "
                f"last_archived_time={last_archived_time.isoformat() if last_archived_time else 'never'} "
                f"last_failed_time={last_failed_time.isoformat() if last_failed_time else 'never'} "
                f"last_failed_wal={last_failed_wal or 'n/a'}"
            ),
            "suggested_action": (
                "Check the archive_command's destination (disk space, permissions, network "
                "reachability) and the server log around the last failure time."
            ),
        }
    ]


def find_wal_dir_size_findings(wal_dir_bytes: int, max_wal_size_bytes: int) -> list[dict]:
    severity = wal_dir_size_severity(wal_dir_bytes, max_wal_size_bytes)
    if severity in ("healthy", "unknown"):
        return []
    wal_dir_mb = wal_dir_bytes / (1024 * 1024)
    max_wal_size_mb = max_wal_size_bytes / (1024 * 1024)
    return [
        {
            "id": "wal-directory-oversized",
            "category": CATEGORY,
            "severity": severity,
            "title": f"WAL directory is {wal_dir_mb / max_wal_size_mb:.1f}x max_wal_size",
            "summary": (
                f"pg_wal is using {wal_dir_mb:,.0f} MB against a configured max_wal_size of "
                f"{max_wal_size_mb:,.0f} MB. Postgres can exceed this target between checkpoints, but a "
                "gap this large usually means something is preventing WAL cleanup — failed archiving, "
                "a stalled or orphaned replication slot, or a stuck replication connection."
            ),
            "detail": f"wal_dir_bytes={wal_dir_bytes} max_wal_size_bytes={max_wal_size_bytes}",
            "suggested_action": (
                "Check this Advisor's WAL archiving findings above and the Replication Advisor tab for "
                "an inactive slot retaining WAL; if neither applies, check for a long-running "
                "replication connection or a checkpoint that isn't completing."
            ),
        }
    ]


def build_backup_status(
    archive_mode: str,
    failed_count: int,
    last_archived_time,
    last_failed_time,
    last_failed_wal,
    wal_dir_bytes: int,
    max_wal_size_bytes: int,
) -> tuple[CategoryStatus, list[dict]]:
    """Rolls all three Backup & WAL Health checks into one Dashboard tile —
    same "reuse the Advisor's own finding functions, never a parallel
    calculation" reasoning as replication_advisor.py::build_replication_status."""
    findings = (
        find_archive_mode_off_findings(archive_mode)
        + find_wal_archiving_failure_findings(archive_mode, failed_count, last_archived_time, last_failed_time, last_failed_wal)
        + find_wal_dir_size_findings(wal_dir_bytes, max_wal_size_bytes)
    )
    severity = "healthy"
    for finding in findings:
        severity = worse(severity, finding["severity"])

    ratio = (wal_dir_bytes / max_wal_size_bytes) if max_wal_size_bytes else None
    value = f"{ratio:.1f}x" if ratio is not None else "—"
    if archive_mode == "off":
        detail = "WAL archiving not configured"
    elif ratio is not None:
        detail = f"WAL dir {ratio:.1f}x max_wal_size"
    else:
        detail = "archiving on"

    return (
        CategoryStatus(key="backup", label="Backup & WAL", severity=severity, value=value, detail=detail),
        findings,
    )
