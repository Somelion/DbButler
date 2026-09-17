import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.pre_upgrade_advisor import (
    REMOVED_SETTING_NAMES,
    UNSUPPORTED_REG_TYPES,
    find_reg_type_column_findings,
    find_removed_setting_findings,
)
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import IndexFinding, PreUpgradeAdvisorResponse
from app.target_conn import connect_to_target

router = APIRouter(prefix="/api/targets", tags=["pre-upgrade-advisor"])

REMOVED_SETTINGS_QUERY = "SELECT name, setting, source FROM pg_settings WHERE name = ANY(%s)"

REG_TYPE_COLUMNS_QUERY = """
    SELECT n.nspname, c.relname, a.attname, t.typname
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_type t ON t.oid = a.atttypid
    WHERE c.relkind = 'r'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND a.attnum > 0 AND NOT a.attisdropped
      AND t.typname = ANY(%s)
"""


@router.get("/{target_id}/pre-upgrade-advisor", response_model=PreUpgradeAdvisorResponse)
def get_pre_upgrade_advisor(target_id: uuid.UUID):
    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            findings = compute_pre_upgrade_advisor_findings(cur)
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return PreUpgradeAdvisorResponse(findings=[IndexFinding(**finding) for finding in findings])


def compute_pre_upgrade_advisor_findings(cur) -> list[dict]:
    """Runs both Pre-Upgrade Compatibility checks against an already-open
    target cursor and returns raw finding dicts, unfiltered by archive
    state. Shared by the on-demand endpoint above and the nightly deep scan
    (scheduler.py::run_deep_scan_cycle) so the two never drift apart."""
    cur.execute(REMOVED_SETTINGS_QUERY, (REMOVED_SETTING_NAMES,))
    setting_rows = cur.fetchall()

    cur.execute(REG_TYPE_COLUMNS_QUERY, (list(UNSUPPORTED_REG_TYPES),))
    reg_type_rows = cur.fetchall()

    return find_removed_setting_findings(setting_rows) + find_reg_type_column_findings(reg_type_rows)
