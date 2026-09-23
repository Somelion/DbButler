import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.routers.advisor_archive import get_archived_finding_ids
from app.schema_filter import get_allowed_schemas, schema_filter_params, schema_filter_sql
from app.schemas import IndexFinding, SecurityAdvisorResponse
from app.security_advisor import (
    find_audit_logging_findings,
    find_excess_superuser_findings,
    find_public_grant_findings,
    find_rls_enabled_without_policy_findings,
    find_rls_policy_without_enforcement_findings,
    find_security_definer_search_path_findings,
    find_ssl_disabled_findings,
)
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["security-advisor"])

PGAUDIT_INSTALLED_QUERY = "SELECT 1 FROM pg_extension WHERE extname = 'pgaudit'"
SUPERUSER_ROLES_QUERY = "SELECT rolname FROM pg_roles WHERE rolsuper"

# Only explicit table-level grants to PUBLIC — the implicit schema-level
# CREATE-on-public default (dropped in PG15 anyway) is a different, noisier
# signal not worth conflating with this.
# {schema_filter} — see app/schema_filter.py::schema_filter_sql.
PUBLIC_GRANTS_QUERY = """
    SELECT table_schema, table_name, string_agg(DISTINCT privilege_type, ', ' ORDER BY privilege_type)
    FROM information_schema.table_privileges
    WHERE grantee = 'PUBLIC' AND table_schema NOT IN ('pg_catalog', 'information_schema')
      {schema_filter}
    GROUP BY table_schema, table_name
    ORDER BY table_schema, table_name
"""

RLS_POLICY_WITHOUT_ENFORCEMENT_QUERY = """
    SELECT DISTINCT p.schemaname, p.tablename
    FROM pg_policies p
    JOIN pg_namespace n ON n.nspname = p.schemaname
    JOIN pg_class c ON c.relname = p.tablename AND c.relnamespace = n.oid
    WHERE NOT c.relrowsecurity
      {schema_filter}
    ORDER BY 1, 2
"""

RLS_ENABLED_WITHOUT_POLICY_QUERY = """
    SELECT n.nspname, c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND c.relrowsecurity
      AND NOT EXISTS (
          SELECT 1 FROM pg_policies p WHERE p.schemaname = n.nspname AND p.tablename = c.relname
      )
      {schema_filter}
    ORDER BY 1, 2
"""

SECURITY_DEFINER_SEARCH_PATH_QUERY = """
    SELECT n.nspname, p.proname
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prosecdef
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND NOT EXISTS (
          SELECT 1 FROM unnest(COALESCE(p.proconfig, ARRAY[]::text[])) cfg WHERE cfg LIKE 'search_path=%%'
      )
      {schema_filter}
    ORDER BY 1, 2
"""


@router.get("/{target_id}/security-advisor", response_model=SecurityAdvisorResponse)
def get_security_advisor(target_id: uuid.UUID):
    allowed_schemas = get_allowed_schemas(target_id)
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_security_advisor_findings(cur, allowed_schemas)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return SecurityAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_security_advisor_findings(cur, allowed_schemas: list[str] | None = None) -> list[dict]:
    """Runs all Security/Compliance Advisor checks against an already-open
    target cursor and returns raw finding dicts, unfiltered by archive
    state. Shared by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart.
    allowed_schemas (app/schema_filter.py) narrows the four table/policy/
    function checks — audit logging, SSL, and excess-superuser are
    server-wide, not schema-scoped."""
    schema_params = schema_filter_params(allowed_schemas)

    cur.execute(PGAUDIT_INSTALLED_QUERY)
    pgaudit_installed = cur.fetchone() is not None

    cur.execute("SHOW log_statement")
    log_statement = cur.fetchone()[0]

    cur.execute("SHOW ssl")
    ssl_enabled = cur.fetchone()[0] == "on"

    cur.execute(SUPERUSER_ROLES_QUERY)
    superuser_roles = [row[0] for row in cur.fetchall()]

    cur.execute(PUBLIC_GRANTS_QUERY.format(schema_filter=schema_filter_sql("table_schema", allowed_schemas)), schema_params)
    public_grant_rows = cur.fetchall()

    cur.execute(
        RLS_POLICY_WITHOUT_ENFORCEMENT_QUERY.format(schema_filter=schema_filter_sql("p.schemaname", allowed_schemas)),
        schema_params,
    )
    rls_policy_without_enforcement_rows = cur.fetchall()

    cur.execute(
        RLS_ENABLED_WITHOUT_POLICY_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)),
        schema_params,
    )
    rls_enabled_without_policy_rows = cur.fetchall()

    cur.execute(
        SECURITY_DEFINER_SEARCH_PATH_QUERY.format(schema_filter=schema_filter_sql("n.nspname", allowed_schemas)),
        schema_params,
    )
    security_definer_rows = cur.fetchall()

    return (
        find_audit_logging_findings(pgaudit_installed, log_statement)
        + find_ssl_disabled_findings(ssl_enabled)
        + find_excess_superuser_findings(superuser_roles)
        + find_public_grant_findings(public_grant_rows)
        + find_rls_policy_without_enforcement_findings(rls_policy_without_enforcement_rows)
        + find_rls_enabled_without_policy_findings(rls_enabled_without_policy_rows)
        + find_security_definer_search_path_findings(security_definer_rows)
    )
