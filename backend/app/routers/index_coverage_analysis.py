import logging
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException
from psycopg import Error as PsycopgError

from app.ai_providers import AiProviderError, call_ai
from app.crypto import decrypt
from app.db.store import store_conn
from app.index_analysis import build_covering_index_ddl, queries_referencing_table
from app.query_anonymizer import anonymize_text, build_identifier_map, deanonymize_text
from app.routers.index_advisor import ALL_INDEX_COLUMNS_QUERY, RECENT_QUERIES_QUERY
from app.schemas import (
    IndexCoverageAnalysesResponse,
    IndexCoverageAnalysisOut,
    IndexCoverageAnalysisRequest,
)
from app.target_conn import connect_to_target

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/targets", tags=["index-coverage-analysis"])

_COLUMNS = (
    "id, target_id, finding_id, schema_name, table_name, index_name, provider, model_name, "
    "status, ai_response, recommended_ddl, error, created_at, completed_at"
)

MAX_SAMPLE_QUERIES = 20

# Deliberately asks the AI to judge which columns to move, never to write the
# DDL itself — this app never trusts an LLM's raw SQL output (call_ai has no
# JSON-mode/structured-output guarantee either); the one required closing
# line is parsed and DDL is built deterministically by
# app/index_analysis.py::build_covering_index_ddl, the same helper the
# synchronous heuristic check uses.
PROMPT_TEMPLATE = """You are a senior PostgreSQL performance engineer reviewing one multi-column \
index for a possible "covering index" restructuring: narrowing its key columns and moving any \
trailing column that's only ever fetched (never filtered or sorted on) into an INCLUDE (...) list, \
so the same index-only-scan benefit is kept with a cheaper-to-maintain key.

Index: {index_name} ON {table} ({columns})
The first column, {leading_column}, is assumed to stay in the key — only judge the remaining columns.

A regex-based heuristic already produced this read from {matched_query_count} recent queries \
against this table (it can be wrong — a column name can coincide with a string literal or another \
table's column of the same name):
{heuristic_summary}

Sample queries against this table (literals already replaced with $1/$2/... by pg_stat_statements \
normalization):
{queries}

Decide which of the non-leading columns ({trailing_columns}) should move to INCLUDE, if any. \
Explain your reasoning in 2-4 sentences, referencing what you see in the sample queries above. Then, \
on its own final line, output exactly one of:
RECOMMENDATION: INCLUDE (col_a, col_b)
RECOMMENDATION: NO_CHANGE
Only ever name columns from the list above — never invent one, and never include the leading \
column, {leading_column}, in the INCLUDE list.
"""

RECOMMENDATION_INCLUDE_PATTERN = re.compile(r"RECOMMENDATION:\s*INCLUDE\s*\(([^)]*)\)", re.IGNORECASE)


def _row_to_out(row) -> IndexCoverageAnalysisOut:
    return IndexCoverageAnalysisOut(
        id=row[0],
        target_id=row[1],
        finding_id=row[2],
        schema_name=row[3],
        table_name=row[4],
        index_name=row[5],
        provider=row[6],
        model_name=row[7],
        status=row[8],
        ai_response=row[9],
        recommended_ddl=row[10],
        error=row[11],
        created_at=row[12],
        completed_at=row[13],
    )


def _find_index(index_rows: list[tuple], finding_id: str) -> tuple[str, str, str] | None:
    """Matches a covering-index-* finding id back to its (schema, bare table,
    index name) by re-deriving every candidate id the same way
    find_covering_index_candidates does, rather than parsing the id string —
    schema/table/index names can themselves contain hyphens, which would
    make splitting the id ambiguous."""
    for schema, table_name, index_name, _method, _is_unique, _columns in index_rows:
        bare_table = table_name.rsplit(".", 1)[-1]
        if f"covering-index-{schema}-{bare_table}-{index_name}" == finding_id:
            return schema, bare_table, index_name
    return None


@router.post(
    "/{target_id}/index-coverage-analyses", response_model=IndexCoverageAnalysisOut, status_code=201
)
def create_index_coverage_analysis(
    target_id: uuid.UUID, payload: IndexCoverageAnalysisRequest, background_tasks: BackgroundTasks
):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute(ALL_INDEX_COLUMNS_QUERY)
            index_rows = [(row[0], row[1], row[2], row[3], row[4], list(row[5])) for row in cur.fetchall()]
    except PsycopgError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    match = _find_index(index_rows, payload.finding_id)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="No matching index found for this finding — it may have changed since Index Advisor last ran.",
        )
    schema, table_name, index_name = match

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO index_coverage_analyses (target_id, finding_id, schema_name, table_name, index_name)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (target_id, payload.finding_id, schema, table_name, index_name),
        )
        row = cur.fetchone()
        conn.commit()

    # Returns immediately; the catalog re-read + AI call happen after the
    # response is sent, same background-task shape as query_analysis.py.
    background_tasks.add_task(_run_covering_analysis, row[0])
    return _row_to_out(row)


@router.get("/{target_id}/index-coverage-analyses", response_model=IndexCoverageAnalysesResponse)
def list_index_coverage_analyses(target_id: uuid.UUID, finding_id: str | None = None):
    query = f"SELECT {_COLUMNS} FROM index_coverage_analyses WHERE target_id = %s"
    params: list = [target_id]
    if finding_id:
        query += " AND finding_id = %s"
        params.append(finding_id)
    query += " ORDER BY created_at DESC"

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return IndexCoverageAnalysesResponse(analyses=[_row_to_out(row) for row in cur.fetchall()])


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
        cur.execute(f"UPDATE index_coverage_analyses SET {', '.join(set_clauses)} WHERE id = %s", values)
        conn.commit()


def _run_covering_analysis(analysis_id: uuid.UUID) -> None:
    """Runs entirely outside the request/response cycle (FastAPI
    BackgroundTasks) — a one-off per user click, not a recurring job. Never
    left stuck 'pending': every exit path records 'done' or 'error'."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT target_id, schema_name, table_name, index_name FROM index_coverage_analyses WHERE id = %s",
            (analysis_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        target_id, schema, table_name, index_name = row

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
        with connect_to_target(target_id) as target_conn, target_conn.cursor() as cur:
            cur.execute(ALL_INDEX_COLUMNS_QUERY)
            index_rows = [(row[0], row[1], row[2], row[3], row[4], list(row[5])) for row in cur.fetchall()]

            columns = next(
                (
                    cols
                    for s, t, i, _m, _u, cols in index_rows
                    if s == schema and t.rsplit(".", 1)[-1] == table_name and i == index_name
                ),
                None,
            )
            if columns is None:
                _record_error(analysis_id, "This index no longer exists on the target.")
                return

            cur.execute(RECENT_QUERIES_QUERY)
            recent_queries = cur.fetchall()
            matched_queries = queries_referencing_table(table_name, recent_queries)[:MAX_SAMPLE_QUERIES]

            # Include the table/column names explicitly so they're anonymized
            # too, not just whatever happens to appear in matched_queries —
            # otherwise the "Index: ... ON ..." line below would leak the
            # real names even with anonymization on.
            mapping = build_identifier_map(cur, table_name, *columns, *matched_queries)
    except Exception as exc:  # noqa: BLE001 — target unreachable, permissions, etc. all land here
        _record_error(analysis_id, f"Could not read this index's current definition from the target: {exc}")
        return

    trailing_columns = columns[1:]
    queries_for_ai = "\n".join(anonymize_text(q, mapping) for q in matched_queries) or "(none found)"
    prompt = PROMPT_TEMPLATE.format(
        index_name=index_name,
        table=anonymize_text(table_name, mapping),
        columns=", ".join(anonymize_text(c, mapping) for c in columns),
        leading_column=anonymize_text(columns[0], mapping),
        matched_query_count=len(matched_queries),
        heuristic_summary=f"{len(trailing_columns)} trailing column(s): "
        f"{', '.join(anonymize_text(c, mapping) for c in trailing_columns)}",
        queries=queries_for_ai,
        trailing_columns=", ".join(anonymize_text(c, mapping) for c in trailing_columns),
    )

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE index_coverage_analyses SET prompt = %s, provider = %s, model_name = %s WHERE id = %s",
            (prompt, provider, model_name, analysis_id),
        )
        conn.commit()

    try:
        raw_response = call_ai(provider, api_key, endpoint_url, model_name, prompt)
    except AiProviderError as exc:
        _record_error(analysis_id, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — must never leave the analysis stuck in "pending" forever
        logger.warning("AI index coverage analysis %s failed unexpectedly: %s", analysis_id, exc)
        _record_error(analysis_id, f"Unexpected error: {exc}")
        return

    # Parsed from the deanonymized text — the prompt above anonymizes table/
    # column names, so the AI's RECOMMENDATION line names placeholders, not
    # real columns; validating against real trailing_columns needs it
    # translated back first.
    final_response = deanonymize_text(raw_response, mapping)

    recommended_ddl = None
    include_match = RECOMMENDATION_INCLUDE_PATTERN.search(final_response)
    if include_match:
        valid_columns = set(trailing_columns)
        include_columns = [c.strip() for c in include_match.group(1).split(",") if c.strip() in valid_columns]
        if include_columns:
            key_columns = [columns[0]] + [c for c in trailing_columns if c not in include_columns]
            recommended_ddl = build_covering_index_ddl(schema, table_name, index_name, key_columns, include_columns)

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE index_coverage_analyses
            SET status = 'done', ai_response = %s, recommended_ddl = %s, completed_at = now()
            WHERE id = %s
            """,
            (final_response, recommended_ddl, analysis_id),
        )
        conn.commit()
