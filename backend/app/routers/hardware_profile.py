import uuid

import psycopg
from fastapi import APIRouter, HTTPException

from app.db.store import store_conn
from app.hardware_detect import detect_hardware
from app.schemas import HardwareDetection, HardwareProfileCreate, HardwareProfileOut

router = APIRouter(prefix="/api/targets", tags=["hardware-profile"])

_COLUMNS = (
    "id, target_id, detected_ram_mb, detected_cpu_cores, confirmed_ram_mb, "
    "confirmed_cpu_cores, storage_type, workload_type, created_at"
)


def _row_to_profile(row) -> HardwareProfileOut:
    return HardwareProfileOut(
        id=row[0],
        target_id=row[1],
        detected_ram_mb=row[2],
        detected_cpu_cores=row[3],
        confirmed_ram_mb=row[4],
        confirmed_cpu_cores=row[5],
        storage_type=row[6],
        workload_type=row[7],
        created_at=row[8],
    )


@router.get("/{target_id}/hardware-profile/detect", response_model=HardwareDetection)
def detect(target_id: uuid.UUID):
    return HardwareDetection(**detect_hardware())


def get_latest_profile(target_id: uuid.UUID) -> HardwareProfileOut | None:
    """Shared by this router's own GET and the config-tuning router, which
    needs the saved profile to size its recommendations from."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNS} FROM hardware_profiles WHERE target_id = %s ORDER BY created_at DESC LIMIT 1",
            (target_id,),
        )
        row = cur.fetchone()
    return _row_to_profile(row) if row else None


@router.get("/{target_id}/hardware-profile", response_model=HardwareProfileOut | None)
def get_profile(target_id: uuid.UUID):
    return get_latest_profile(target_id)


@router.post("/{target_id}/hardware-profile", response_model=HardwareProfileOut, status_code=201)
def save_profile(target_id: uuid.UUID, payload: HardwareProfileCreate):
    # Re-detected server-side rather than trusting a client-supplied
    # "detected" value — detection stays authoritative.
    detected = detect_hardware()
    try:
        with store_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO hardware_profiles
                    (target_id, detected_ram_mb, detected_cpu_cores, confirmed_ram_mb,
                     confirmed_cpu_cores, storage_type, workload_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {_COLUMNS}
                """,
                (
                    target_id,
                    detected["detected_ram_mb"],
                    detected["detected_cpu_cores"],
                    payload.confirmed_ram_mb,
                    payload.confirmed_cpu_cores,
                    payload.storage_type,
                    payload.workload_type,
                ),
            )
            row = cur.fetchone()
            conn.commit()
    except psycopg.errors.ForeignKeyViolation as exc:
        raise HTTPException(status_code=404, detail="Target not found") from exc

    return _row_to_profile(row)
