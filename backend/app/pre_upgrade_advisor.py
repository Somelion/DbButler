"""Pre-Upgrade Compatibility Scan analyzers — pure functions over
pg_settings / pg_attribute rows (routers/pre_upgrade_advisor.py).
Deliberately narrow: two checks this app can verify with high confidence
from a single SQL connection to the *current* (pre-upgrade) server, sourced
from PostgreSQL's own release notes and pg_upgrade's own documented
pre-checks — rather than attempting to replicate pg_upgrade --check's full
scope, which needs the new version's binaries and catalog, not just a SQL
connection to the old one.
"""

CATEGORY = "pre-upgrade advisor"

# (setting_name, removed_in_major_version, note) — sourced from each
# version's official "Migration to Version N" release notes. Explicitly
# configuring one of these (pg_settings.source != 'default') means the
# *new* server refuses to start with "unrecognized configuration
# parameter" until it's removed from the config — a one-line fix, but easy
# to forget before an upgrade, and the server won't come back up until it's
# found. A setting removed at or before the version this app is currently
# connected to simply won't appear in pg_settings at all, so no separate
# version gate is needed here — the query naturally comes back empty for it.
REMOVED_SETTINGS = [
    ("operator_precedence_warning", 14, "removed outright in PostgreSQL 14"),
    (
        "vacuum_cleanup_index_scale_factor",
        14,
        "removed outright in PostgreSQL 14 (had already been ignored since 13.3)",
    ),
    ("stats_temp_directory", 15, "removed outright in PostgreSQL 15 — statistics moved into shared memory"),
    ("force_parallel_mode", 16, "renamed to debug_parallel_query in PostgreSQL 16"),
    ("vacuum_defer_cleanup_age", 16, "removed outright in PostgreSQL 16"),
    (
        "promote_trigger_file",
        16,
        "removed outright in PostgreSQL 16 — use pg_ctl promote or pg_promote() instead",
    ),
    ("old_snapshot_threshold", 17, "removed outright in PostgreSQL 17"),
    ("db_user_namespace", 17, "removed outright in PostgreSQL 17"),
    ("trace_recovery_messages", 17, "removed outright in PostgreSQL 17"),
]
REMOVED_SETTING_NAMES = [name for name, _, _ in REMOVED_SETTINGS]
_REMOVED_SETTINGS_BY_NAME = {name: (version, note) for name, version, note in REMOVED_SETTINGS}


def find_removed_setting_findings(rows) -> list[dict]:
    """rows: (name, setting, source) from pg_settings, already narrowed to
    REMOVED_SETTING_NAMES by the caller's query."""
    findings = []
    for name, setting, source in rows:
        if source == "default":
            continue
        removed_in_version, note = _REMOVED_SETTINGS_BY_NAME[name]
        findings.append(
            {
                "id": f"pre-upgrade-removed-setting-{name}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{name} is explicitly set but removed in PostgreSQL {removed_in_version}",
                "summary": (
                    f"{name} is currently set to {setting!r} ({source}) — {note}. Upgrading to "
                    f"PostgreSQL {removed_in_version} or later with this still in the config will fail "
                    'to start with "unrecognized configuration parameter".'
                ),
                "detail": f"name={name} setting={setting} source={source} removed_in_version={removed_in_version}",
                "suggested_action": (
                    f"Remove {name} from postgresql.conf/postgresql.auto.conf before upgrading to "
                    f"PostgreSQL {removed_in_version} or later."
                ),
            }
        )
    return findings


# pg_upgrade's own pre-check hard-fails an upgrade over these — they store
# a bare internal object OID that isn't guaranteed to still point at the
# same object after a major-version upgrade. regclass/regrole/regtype are
# excluded: pg_upgrade documents those three specifically as safe to carry
# across, unlike the rest of the reg* family.
UNSUPPORTED_REG_TYPES = (
    "regproc",
    "regprocedure",
    "regoper",
    "regoperator",
    "regconfig",
    "regdictionary",
    "regnamespace",
    "regcollation",
)


def find_reg_type_column_findings(rows) -> list[dict]:
    """rows: (schema_name, table_name, column_name, type_name) for every
    user-table column already narrowed to UNSUPPORTED_REG_TYPES by the
    caller's query."""
    findings = []
    for schema_name, table_name, column_name, type_name in rows:
        findings.append(
            {
                "id": f"pre-upgrade-reg-type-{schema_name}-{table_name}-{column_name}",
                "category": CATEGORY,
                "severity": "attention",
                "title": f"{schema_name}.{table_name}.{column_name} is type {type_name}, which blocks pg_upgrade",
                "summary": (
                    f"pg_upgrade refuses to run while any user table has a {type_name} column — this "
                    "type stores a bare internal object ID that isn't guaranteed to still point at the "
                    "same object after a major-version upgrade."
                ),
                "detail": f"schema={schema_name} table={table_name} column={column_name} type={type_name}",
                "suggested_action": (
                    "Before running pg_upgrade, either drop this column or convert it to text — "
                    "pg_upgrade's own --check reports the exact list of offending columns if you run it "
                    "first."
                ),
                "recommended_ddl": (
                    f"ALTER TABLE {schema_name}.{table_name} ALTER COLUMN {column_name} TYPE text;"
                ),
            }
        )
    return findings
