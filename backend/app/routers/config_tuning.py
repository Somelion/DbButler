import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.routers.hardware_profile import get_latest_profile
from app.schemas import ConfigDiffResponse, ConfigDiffRow
from app.target_conn import connect_to_target
from app.tuning_engine import RESTART_REQUIRED, recommend

router = APIRouter(prefix="/api/targets", tags=["config-tuning"])


@router.get("/{target_id}/config-tuning", response_model=ConfigDiffResponse)
def get_config_tuning(target_id: uuid.UUID):
    profile = get_latest_profile(target_id)
    if profile is None:
        raise HTTPException(
            status_code=400, detail="Save a Hardware Profile first — recommendations are sized from it."
        )

    recommendations = recommend(
        profile.confirmed_ram_mb, profile.confirmed_cpu_cores, profile.storage_type, profile.workload_type
    )
    names = [r["name"] for r in recommendations]

    try:
        with connect_to_target(target_id) as conn, conn.cursor() as cur:
            cur.execute("SELECT name, current_setting(name) FROM pg_settings WHERE name = ANY(%s)", (names,))
            current_values = dict(cur.fetchall())
    except psycopg.Error as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach target: {exc}") from exc

    rows = [
        ConfigDiffRow(
            name=r["name"],
            current_value=current_values.get(r["name"], "—"),
            recommended_value=r["recommended_display"],
            why=r["why"],
            requires_restart=r["name"] in RESTART_REQUIRED,
        )
        for r in recommendations
    ]
    return ConfigDiffResponse(hardware_profile=profile, rows=rows)
