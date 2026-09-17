import uuid

from fastapi import APIRouter, HTTPException, Query

from apscheduler.triggers.cron import CronTrigger

from app import scheduler
from app.db.store import store_conn
from app.schemas import (
    DeepScanFindingOut,
    DeepScanFindingsResponse,
    IndexFinding,
    SchedulerJobOut,
    SchedulerJobsResponse,
    SchedulerJobUpdate,
)

router = APIRouter(tags=["scheduler-settings"])

_JOB_COLUMNS = "job_name, job_kind, interval_seconds, cron_expr, enabled, updated_at"


def _row_to_job(row) -> SchedulerJobOut:
    return SchedulerJobOut(
        job_name=row[0],
        job_kind=row[1],
        interval_seconds=row[2],
        cron_expr=row[3],
        enabled=row[4],
        updated_at=row[5],
    )


@router.get("/api/scheduler/jobs", response_model=SchedulerJobsResponse)
def list_scheduler_jobs():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_JOB_COLUMNS} FROM scheduler_jobs ORDER BY job_name")
        return SchedulerJobsResponse(jobs=[_row_to_job(row) for row in cur.fetchall()])


@router.put("/api/scheduler/jobs/{job_name}", response_model=SchedulerJobOut)
def update_scheduler_job(job_name: str, payload: SchedulerJobUpdate):
    if job_name not in scheduler.KNOWN_JOB_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_name}")

    if payload.job_kind == "interval":
        if not payload.interval_seconds or payload.interval_seconds <= 0:
            raise HTTPException(status_code=400, detail="interval_seconds must be a positive number.")
    elif payload.job_kind == "cron":
        if not payload.cron_expr:
            raise HTTPException(status_code=400, detail="cron_expr is required for a cron job.")
        try:
            CronTrigger.from_crontab(payload.cron_expr)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {exc}") from exc
    else:
        raise HTTPException(status_code=400, detail="job_kind must be 'interval' or 'cron'.")

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE scheduler_jobs
            SET job_kind = %s, interval_seconds = %s, cron_expr = %s, enabled = %s, updated_at = now()
            WHERE job_name = %s
            RETURNING {_JOB_COLUMNS}
            """,
            (payload.job_kind, payload.interval_seconds, payload.cron_expr, payload.enabled, job_name),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_name}")
        conn.commit()

    # The hotswap: apply the just-written config to the live APScheduler
    # instance before returning, so the caller's next request already sees
    # the new cadence in effect — no backend restart involved.
    scheduler.reconcile()

    return _row_to_job(row)


@router.get("/api/targets/{target_id}/deep-scan/findings", response_model=DeepScanFindingsResponse)
def list_deep_scan_findings(target_id: uuid.UUID, status: str | None = Query(default=None)):
    """status: optional 'open' | 'resolved' filter. Mainly for
    ui/src/useFirstSeenMap.js, which only ever wants the currently-open set
    to badge live findings with "first seen N days ago" — resolved rows
    have no bound on how many years of history accumulate (no retention
    job prunes this table), so an unfiltered fetch only makes sense for
    someone deliberately inspecting the full history."""
    query = "SELECT id, target_id, finding_id, category, finding, status, first_seen_at, last_seen_at, resolved_at FROM deep_scan_findings WHERE target_id = %s"
    params = [target_id]
    if status is not None:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY first_seen_at DESC"

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return DeepScanFindingsResponse(
        findings=[
            DeepScanFindingOut(
                id=row[0],
                target_id=row[1],
                finding_id=row[2],
                category=row[3],
                finding=IndexFinding(**row[4]),
                status=row[5],
                first_seen_at=row[6],
                last_seen_at=row[7],
                resolved_at=row[8],
            )
            for row in rows
        ]
    )
