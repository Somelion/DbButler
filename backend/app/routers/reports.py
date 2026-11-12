"""Weekly health reports: generated every Monday by the weekly_report job
(scheduler.py) or on demand here, stored in health_reports, and served as
data or as a self-contained HTML page (app/health_report.py).

Store reads only; the target is never contacted.
"""

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from psycopg.types.json import Jsonb

from app.config import settings
from app.db.store import store_conn
from app.health_report import build_report_data, render_report_html, report_summary_lines, report_title
from app.schemas import HealthReportOut, HealthReportsResponse, HealthReportSummary

router = APIRouter(tags=["reports"])

LIST_LIMIT = 60

_SUMMARY_COLUMNS = """
    id, target_id, period_start, period_end, generated_at, trigger, overall_severity,
    coalesce((data->'findings'->'open_by_severity'->>'critical')::int, 0),
    coalesce((data->'findings'->'open_by_severity'->>'attention')::int, 0),
    coalesce((data->'findings'->>'opened')::int, 0),
    coalesce((data->'findings'->>'resolved')::int, 0),
    coalesce((data->'lock_incidents'->>'count')::int, 0)
"""

# The page has no scripts and loads nothing: inline styles only.
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"


def _summary(row) -> dict:
    (
        report_id,
        target_id,
        period_start,
        period_end,
        generated_at,
        trigger,
        severity,
        critical,
        attention,
        opened,
        resolved,
        incidents,
    ) = row
    return {
        "id": report_id,
        "target_id": target_id,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": generated_at,
        "trigger": trigger,
        "overall_severity": severity,
        "open_critical": critical,
        "open_attention": attention,
        "opened": opened,
        "resolved": resolved,
        "lock_incidents": incidents,
    }


def report_link(target_id, report_id) -> str | None:
    """Where the Reports screen shows this report, when PUBLIC_URL says
    where DbButler is reachable."""
    base = settings.public_url.strip().rstrip("/")
    return f"{base}/t/{target_id}/reports?report={report_id}" if base else None


def generate_report(target_id, trigger: str, end: datetime | None = None) -> tuple[dict, dict]:
    """Builds and stores one report; returns (summary, data)."""
    end = end or datetime.now(timezone.utc)
    with store_conn() as conn, conn.cursor() as cur:
        data = build_report_data(cur, target_id, end)
        cur.execute(
            f"""
            INSERT INTO health_reports (target_id, period_start, period_end, trigger, overall_severity, data)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_SUMMARY_COLUMNS}
            """,
            (
                target_id,
                datetime.fromisoformat(data["period"]["start"]),
                end,
                trigger,
                data["overall_severity"],
                Jsonb(data),
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return _summary(row), data


def _load(report_id: uuid.UUID) -> tuple[dict, dict]:
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_SUMMARY_COLUMNS}, data FROM health_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _summary(row[:-1]), row[-1]


def _require_target(target_id: uuid.UUID) -> None:
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM targets WHERE id = %s", (target_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Target not found")


@router.get("/api/targets/{target_id}/reports", response_model=HealthReportsResponse)
def list_reports(target_id: uuid.UUID):
    _require_target(target_id)
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SUMMARY_COLUMNS} FROM health_reports WHERE target_id = %s ORDER BY generated_at DESC LIMIT %s",
            (target_id, LIST_LIMIT),
        )
        reports = [_summary(row) for row in cur.fetchall()]
    return {"reports": reports}


@router.post("/api/targets/{target_id}/reports", response_model=HealthReportSummary)
def create_report(target_id: uuid.UUID):
    """A report for the week ending now. Not posted to the webhook — that's
    the scheduled job's."""
    _require_target(target_id)
    summary, _data = generate_report(target_id, "manual")
    return summary


@router.get("/api/reports/{report_id}", response_model=HealthReportOut)
def get_report(report_id: uuid.UUID):
    summary, data = _load(report_id)
    return {**summary, "title": report_title(data), "summary_lines": report_summary_lines(data), "data": data}


@router.get("/api/reports/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: uuid.UUID, download: bool = Query(False)):
    summary, data = _load(report_id)
    headers = {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"}
    if download:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", data["target"]["name"]).strip("-") or "target"
        headers["Content-Disposition"] = (
            f'attachment; filename="dbbutler-report-{name}-{summary["period_end"]:%Y-%m-%d}.html"'
        )
    return HTMLResponse(render_report_html(data, summary["generated_at"]), headers=headers)
