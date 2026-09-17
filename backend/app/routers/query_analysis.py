import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb

from app.ai_providers import AiProviderError, call_ai
from app.crypto import decrypt
from app.db.store import store_conn
from app.query_anonymizer import anonymize_text, build_identifier_map, deanonymize_text
from app.query_guard import ensure_read_only
from app.schemas import (
    AnonymizePreviewRequest,
    AnonymizePreviewResponse,
    QueryAnalysesResponse,
    QueryAnalysisOut,
    QueryAnalysisRequest,
)
from app.target_conn import connect_to_target

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/targets", tags=["query-analysis"])

_COLUMNS = (
    "id, target_id, original_query, anonymize, anonymized_query, explain_plan, "
    "provider, model_name, status, ai_response, error, created_at, completed_at"
)

PROMPT_TEMPLATE = """You are a senior PostgreSQL performance engineer. Analyze the query below \
together with its EXPLAIN (ANALYZE, BUFFERS) plan.

Structure your response in exactly two parts:

## What's wrong
Plain-language diagnosis. Be specific — reference the actual plan nodes, estimated vs. actual row \
counts, and timings you see in the plan above, not generic advice. If the query is already \
well-optimized given this plan, say so plainly instead of inventing problems.

## Suggested rewrites
2-3 concrete alternative versions of this query that address the issues you found above (e.g. \
narrowing a WHERE clause, rewriting a correlated subquery as a JOIN, restructuring to allow an \
index scan, adding a covering or partial index). For each one:
- Show the rewritten SQL in a ```sql code block.
- Follow it with one sentence explaining what changed and why it should be faster.
Only reference table/column names that actually appear in the query or plan above — never invent \
one that isn't there. If no rewrite would meaningfully help, say so instead of forcing examples.

Query:
{query}

EXPLAIN (ANALYZE, BUFFERS) output:
{plan}
"""


def _row_to_out(row) -> QueryAnalysisOut:
    return QueryAnalysisOut(
        id=row[0],
        target_id=row[1],
        original_query=row[2],
        anonymize=row[3],
        anonymized_query=row[4],
        explain_plan=row[5],
        provider=row[6],
        model_name=row[7],
        status=row[8],
        ai_response=row[9],
        error=row[10],
        created_at=row[11],
        completed_at=row[12],
    )


@router.post("/{target_id}/anonymize-preview", response_model=AnonymizePreviewResponse)
def anonymize_preview(target_id: uuid.UUID, payload: AnonymizePreviewRequest):
    """Stateless preview for the modal's Anonymize toggle — shows the user
    what would be sent before they commit to Run Analysis. Nothing here is
    persisted; the actual run re-derives its own mapping independently (see
    _run_analysis below), so skipping this preview changes nothing about
    what a real analysis does."""
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            mapping = build_identifier_map(cur, payload.query)
    except PsycopgError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    return AnonymizePreviewResponse(anonymized_query=anonymize_text(payload.query, mapping))


@router.post("/{target_id}/query-analyses", response_model=QueryAnalysisOut, status_code=201)
def create_query_analysis(
    target_id: uuid.UUID, payload: QueryAnalysisRequest, background_tasks: BackgroundTasks
):
    try:
        ensure_read_only(payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO query_analyses (target_id, original_query, anonymize)
            VALUES (%s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (target_id, payload.query, payload.anonymize),
        )
        row = cur.fetchone()
        conn.commit()

    # Returns immediately; the EXPLAIN + AI call happen after the response is
    # sent, so the caller isn't blocked on however long the AI provider takes.
    background_tasks.add_task(_run_analysis, row[0])
    return _row_to_out(row)


@router.get("/{target_id}/query-analyses", response_model=QueryAnalysesResponse)
def list_query_analyses(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM query_analyses WHERE target_id = %s ORDER BY created_at DESC",
            (target_id,),
        )
        return QueryAnalysesResponse(analyses=[_row_to_out(row) for row in cur.fetchall()])


def _record_error(analysis_id: uuid.UUID, message: str, **extra_fields) -> None:
    fields = {"status": "error", "error": message, "completed_at": "now()", **extra_fields}
    set_clauses = []
    values = []
    for column, value in fields.items():
        if value == "now()":
            set_clauses.append(f"{column} = now()")
        else:
            set_clauses.append(f"{column} = %s")
            values.append(value)
    values.append(analysis_id)

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE query_analyses SET {', '.join(set_clauses)} WHERE id = %s", values)
        conn.commit()


def _run_analysis(analysis_id: uuid.UUID) -> None:
    """Runs entirely outside the request/response cycle (FastAPI
    BackgroundTasks) — a one-off per user click, not a recurring job, so
    this doesn't belong in scheduler.py alongside the interval/cron jobs."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT target_id, original_query, anonymize FROM query_analyses WHERE id = %s",
            (analysis_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        target_id, original_query, anonymize = row

        cur.execute("SELECT provider, endpoint_url, model_name, encrypted_api_key FROM ai_settings WHERE id = 1")
        provider, endpoint_url, model_name, encrypted_api_key = cur.fetchone()

    api_key = decrypt(encrypted_api_key) if encrypted_api_key else None
    if provider == "gemini" and not api_key:
        _record_error(analysis_id, "No Gemini API key is configured. Add one in Settings.")
        return
    if provider == "local" and not endpoint_url:
        _record_error(analysis_id, "No local model endpoint URL is configured. Add one in Settings.")
        return

    try:
        query = ensure_read_only(original_query)
    except ValueError as exc:
        _record_error(analysis_id, str(exc))
        return

    try:
        with connect_to_target(target_id) as target_conn, target_conn.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {query}")
            plan_text = "\n".join(r[0] for r in cur.fetchall())

            mapping: dict[str, str] = {}
            if anonymize:
                mapping = build_identifier_map(cur, query, plan_text)
    except Exception as exc:  # noqa: BLE001 — target unreachable, permissions, bad query, etc. all land here
        _record_error(analysis_id, f"Could not run EXPLAIN against the target: {exc}")
        return

    query_for_ai = anonymize_text(query, mapping) if anonymize else query
    plan_for_ai = anonymize_text(plan_text, mapping) if anonymize else plan_text
    prompt = PROMPT_TEMPLATE.format(query=query_for_ai, plan=plan_for_ai)

    try:
        raw_response = call_ai(provider, api_key, endpoint_url, model_name, prompt)
    except AiProviderError as exc:
        _record_error(
            analysis_id,
            str(exc),
            anonymized_query=query_for_ai if anonymize else None,
            explain_plan=plan_text,
            prompt=prompt,
            provider=provider,
            model_name=model_name,
        )
        return
    except Exception as exc:  # noqa: BLE001 — must never leave the analysis stuck in "pending" forever
        logger.warning("AI query analysis %s failed unexpectedly: %s", analysis_id, exc)
        _record_error(analysis_id, f"Unexpected error: {exc}")
        return

    final_response = deanonymize_text(raw_response, mapping) if anonymize else raw_response

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE query_analyses
            SET anonymized_query = %s, anonymization_map = %s, explain_plan = %s, prompt = %s,
                provider = %s, model_name = %s, status = 'done', ai_response = %s, completed_at = now()
            WHERE id = %s
            """,
            (
                query_for_ai if anonymize else None,
                Jsonb(mapping) if anonymize else None,
                plan_text,
                prompt,
                provider,
                model_name,
                final_response,
                analysis_id,
            ),
        )
        conn.commit()
