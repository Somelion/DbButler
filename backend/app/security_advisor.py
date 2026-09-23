"""Security/Compliance Advisor analyzers — pure functions over
pg_extension / pg_settings / pg_roles rows (routers/security_advisor.py).
Narrower audience than the performance/ops Advisor categories: compliance
and in-transit-encryption gaps rather than anything actively slow or at
risk of data loss today, so severities stay soft (informational or
attention, never critical) — these are nudges toward better practice, not
alarms, and several of them are perfectly reasonable choices in a trusted,
single-admin, local-network setup.
"""

CATEGORY = "security advisor"


def find_audit_logging_findings(pgaudit_installed: bool, log_statement: str) -> list[dict]:
    """pgaudit_installed: whether the pgaudit extension is created on this
    database. log_statement: the server's current SHOW log_statement value
    ('none'/'ddl'/'mod'/'all'). pgAudit is the go-to answer here because it
    produces structured, SIEM-consumable logs and — importantly — captures
    ad hoc psql/DBA activity and ETL jobs that log_statement-based (or
    application-level) audit trails miss entirely."""
    if pgaudit_installed:
        return []

    if log_statement == "none":
        severity = "attention"
        title = "No SQL-level audit trail exists on this database"
        summary = (
            "Neither pgAudit nor log_statement is capturing SQL activity — there's no record of who "
            "ran what. Compliance reviews (SOC2/HIPAA-style) often fail not because a system was "
            "insecure, but because there was no evidence that it was."
        )
    else:
        severity = "unknown"
        title = f"Audit logging relies on log_statement={log_statement}, not pgAudit"
        summary = (
            f"log_statement={log_statement} produces free-form text log lines, not structured, "
            "SIEM-parseable output, and won't capture ad hoc psql/DBA activity or ETL jobs the way "
            "pgAudit does."
        )

    return [
        {
            "id": "audit-logging-gap",
            "category": CATEGORY,
            "severity": severity,
            "title": title,
            "summary": summary,
            "detail": f"pgaudit_installed=false log_statement={log_statement}",
            "suggested_action": (
                "If compliance or audit requirements apply here, install the pgAudit extension (add "
                "it to shared_preload_libraries, restart, then CREATE EXTENSION pgaudit) for "
                "structured, SIEM-consumable audit logs."
            ),
        }
    ]


def find_ssl_disabled_findings(ssl_enabled: bool) -> list[dict]:
    if ssl_enabled:
        return []
    return [
        {
            "id": "ssl-disabled",
            "category": CATEGORY,
            "severity": "attention",
            "title": "SSL is disabled — connections aren't encrypted in transit",
            "summary": (
                "With ssl off, no client connection to this server is encrypted, including "
                "credentials and query data sent over the network."
            ),
            "detail": "ssl=off",
            "suggested_action": (
                "If any client connects over an untrusted network, enable ssl and configure "
                "ssl_cert_file/ssl_key_file. Safe to leave off only when every connection stays on a "
                "trusted, private network (e.g. a local Docker Compose setup)."
            ),
        }
    ]


# More than the single bootstrap superuser role is a least-privilege signal
# worth a soft nudge — not necessarily wrong (some setups intentionally run
# a couple of admin accounts), so this stays informational rather than a
# hard warning.
EXPECTED_SUPERUSER_COUNT = 1


def find_public_grant_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table, privileges_csv) for user tables with at least
    one privilege explicitly granted to PUBLIC — see PUBLIC_GRANTS_QUERY in
    routers/security_advisor.py. Unlike the schema-level CREATE-on-public
    default (dropped in PG15), a table-level PUBLIC grant is always
    something a human explicitly ran, but it's easy to forget it applies to
    every current and future role on the cluster, not just the one it was
    meant for."""
    findings = []
    for schema, table, privileges_csv in rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"public-grant-{schema}-{table}",
                "category": CATEGORY,
                "severity": "unknown",
                "title": f"{full_name} grants {privileges_csv} to PUBLIC",
                "summary": (
                    f"Every role on this database — current and future — can {privileges_csv.lower()} "
                    f"{full_name}, not just the role(s) this was meant for."
                ),
                "detail": f"schema={schema} table={table} privileges={privileges_csv}",
                "suggested_action": (
                    "If this was intentional (e.g. a genuinely public reference table), leave it — "
                    "otherwise REVOKE the privilege from PUBLIC and GRANT it to a specific role instead."
                ),
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_rls_policy_without_enforcement_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table) for tables that have at least one row-level
    security policy defined via CREATE POLICY, but ROW LEVEL SECURITY was
    never turned on with ENABLE ROW LEVEL SECURITY. Postgres silently
    ignores policies on a table where RLS isn't enabled — every row stays
    fully visible, which is the opposite of what writing a policy usually
    signals the author intended."""
    findings = []
    for schema, table in rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"rls-policy-not-enforced-{schema}-{table}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{full_name} has row-level security policies but RLS isn't enabled",
                "summary": (
                    f"{full_name} has one or more CREATE POLICY definitions, but without "
                    "ENABLE ROW LEVEL SECURITY those policies are never enforced — every role sees "
                    "every row, same as if no policy existed."
                ),
                "detail": f"schema={schema} table={table}",
                "suggested_action": f"Run ALTER TABLE {full_name} ENABLE ROW LEVEL SECURITY, if that was the intent.",
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_rls_enabled_without_policy_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, table) for tables where RLS is enabled but no policy
    exists. With RLS on and zero policies, Postgres denies all rows to every
    non-owner, non-superuser role by default — a common "why does this
    table look empty to my application" trap right after enabling RLS but
    before writing the first policy."""
    findings = []
    for schema, table in rows:
        full_name = f"{schema}.{table}"
        findings.append(
            {
                "id": f"rls-enabled-no-policy-{schema}-{table}",
                "category": CATEGORY,
                "severity": "unknown",
                "title": f"{full_name} has RLS enabled with no policies",
                "summary": (
                    f"{full_name} has ROW LEVEL SECURITY enabled but no CREATE POLICY exists — "
                    "non-owner, non-superuser roles currently see zero rows. Confirm this is "
                    "intentional (a deny-by-default table awaiting policies) rather than a step "
                    "that was missed."
                ),
                "detail": f"schema={schema} table={table}",
                "suggested_action": "Add the intended CREATE POLICY statements, if rows should be visible to any role.",
                "schema_name": schema,
                "table_name": table,
            }
        )
    return findings


def find_security_definer_search_path_findings(rows: list[tuple]) -> list[dict]:
    """rows: (schema, function_name) for SECURITY DEFINER functions with no
    search_path pinned in their own proconfig. A SECURITY DEFINER function
    runs with its owner's privileges but the caller's search_path unless one
    is set explicitly — a caller who can create objects in an earlier schema
    on that search_path can shadow a table/function the definer relies on
    and have it executed with the owner's privileges (the classic
    search_path hijack, CVE-2018-1058's root cause)."""
    findings = []
    for schema, function_name in rows:
        full_name = f"{schema}.{function_name}"
        findings.append(
            {
                "id": f"security-definer-search-path-{schema}-{function_name}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{full_name} is SECURITY DEFINER with no search_path pinned",
                "summary": (
                    f"{full_name} runs with its owner's privileges but resolves unqualified names "
                    "using whatever search_path the caller has — a caller who can create objects "
                    "earlier on that path can shadow a table or function the definer relies on."
                ),
                "detail": f"schema={schema} function={function_name}",
                "suggested_action": (
                    f"ALTER FUNCTION {full_name} SET search_path = '' (or an explicit, fully-qualified "
                    "path), or schema-qualify every reference inside the function body."
                ),
                # A function, not a table — schema_name still lets this
                # finding be narrowed by Advisor's schema filter; table_name
                # doesn't apply here so it's left unset.
                "schema_name": schema,
            }
        )
    return findings


def find_excess_superuser_findings(superuser_roles: list[str]) -> list[dict]:
    if len(superuser_roles) <= EXPECTED_SUPERUSER_COUNT:
        return []
    return [
        {
            "id": "excess-superusers",
            "category": CATEGORY,
            "severity": "unknown",
            "title": f"{len(superuser_roles)} roles have superuser privileges",
            "summary": (
                f"{', '.join(superuser_roles)} all have rolsuper=true. Superuser bypasses every "
                "permission check, including row-level security — worth confirming each of these "
                "roles genuinely needs it, rather than a narrower role (pg_monitor/pg_maintain for "
                "monitoring/maintenance tooling, for instance)."
            ),
            "detail": f"superuser_roles={superuser_roles}",
            "suggested_action": (
                "Review each superuser role and consider downgrading ones that only need monitoring "
                "or maintenance access to pg_monitor/pg_maintain membership instead."
            ),
        }
    ]
