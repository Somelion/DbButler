import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.explain_analysis import analyze_plan, extract_execution_time_ms
from app.query_guard import ensure_read_only
from app.schemas import ExplainFinding, ExplainRequest, ExplainResponse
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["explain"])


@router.post("/{target_id}/explain", response_model=ExplainResponse)
def explain_query(target_id: uuid.UUID, payload: ExplainRequest):
    try:
        query = ensure_read_only(payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {query}")
            rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not run EXPLAIN: {exc}") from exc

    plan_text = "\n".join(row[0] for row in rows)
    findings = [ExplainFinding(**finding) for finding in analyze_plan(plan_text)]
    return ExplainResponse(
        plan_text=plan_text,
        execution_time_ms=extract_execution_time_ms(plan_text),
        findings=findings,
    )
