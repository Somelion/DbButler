import uuid

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException

from app.crypto import decrypt, encrypt
from app.db.store import store_conn
from app.pg_probe import test_connection
from app.schemas import ConnectionTestRequest, ConnectionTestResult, TargetCreate, TargetOut, TargetUpdate

router = APIRouter(prefix="/api/targets", tags=["targets"])

_COLUMNS = (
    "id, name, host, port, dbname, username, sslmode, detected_pg_version, "
    "last_test_ok, last_test_at, last_test_message, is_active, created_at, allowed_schemas"
)


def _row_to_target(row) -> TargetOut:
    return TargetOut(
        id=row[0],
        name=row[1],
        host=row[2],
        port=row[3],
        dbname=row[4],
        username=row[5],
        sslmode=row[6],
        detected_pg_version=row[7],
        last_test_ok=row[8],
        last_test_at=row[9],
        last_test_message=row[10],
        is_active=row[11],
        created_at=row[12],
        allowed_schemas=row[13],
    )


@router.get("", response_model=list[TargetOut])
def list_targets():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM targets ORDER BY created_at")
        return [_row_to_target(row) for row in cur.fetchall()]


@router.post("", response_model=TargetOut, status_code=201)
def create_target(payload: TargetCreate):
    result = test_connection(
        payload.host, payload.port, payload.dbname, payload.username, payload.password, payload.sslmode
    )
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO targets
                (name, host, port, dbname, username, encrypted_password, sslmode,
                 detected_pg_version, last_test_ok, last_test_at, last_test_message, allowed_schemas)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
            RETURNING {_COLUMNS}
            """,
            (
                payload.name,
                payload.host,
                payload.port,
                payload.dbname,
                payload.username,
                encrypt(payload.password),
                payload.sslmode,
                result.get("pg_version"),
                result["ok"],
                result.get("message"),
                payload.allowed_schemas or None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _row_to_target(row)


@router.patch("/{target_id}", response_model=TargetOut)
def update_target(target_id: uuid.UUID, payload: TargetUpdate):
    """Rename, pause-resume (is_active), and/or replace the stored password
    for a saved connection — deliberately separate from deleting it. Pausing
    stops the scheduler's background collection/deep-scan/alerting for this
    one target (scheduler.py::_active_targets filters on is_active) without
    losing its saved credentials or history. COALESCE leaves whichever
    field wasn't sent untouched, so a rename-only or pause-only call
    doesn't need to resend the others. A password update re-tests
    immediately with it (same reasoning as retest_target below) so
    last_test_ok reflects reality right away rather than waiting for a
    separate manual "Test again" click — this is also the fix for a target
    whose stored password can no longer be decrypted (see
    target_conn.py::connect_to_target's InvalidToken handling).

    allowed_schemas follows the same COALESCE convention: omitted/null
    leaves the existing allowlist untouched, an empty list clears it back
    to "no filter" (app/schema_filter.py)."""
    if (
        payload.name is None
        and payload.is_active is None
        and not payload.password
        and payload.allowed_schemas is None
    ):
        raise HTTPException(
            status_code=400, detail="Nothing to update — send name, is_active, password, and/or allowed_schemas."
        )

    with store_conn() as conn, conn.cursor() as cur:
        if payload.password:
            cur.execute("SELECT host, port, dbname, username, sslmode FROM targets WHERE id = %s", (target_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Target not found")
            host, port, dbname, username, sslmode = row
            result = test_connection(host, port, dbname, username, payload.password, sslmode)
            cur.execute(
                f"""
                UPDATE targets
                SET name = COALESCE(%s, name),
                    is_active = COALESCE(%s, is_active),
                    allowed_schemas = COALESCE(%s, allowed_schemas),
                    encrypted_password = %s,
                    detected_pg_version = %s,
                    last_test_ok = %s,
                    last_test_at = now(),
                    last_test_message = %s
                WHERE id = %s
                RETURNING {_COLUMNS}
                """,
                (
                    payload.name,
                    payload.is_active,
                    payload.allowed_schemas,
                    encrypt(payload.password),
                    result.get("pg_version"),
                    result["ok"],
                    result.get("message"),
                    target_id,
                ),
            )
        else:
            cur.execute(
                f"""
                UPDATE targets
                SET name = COALESCE(%s, name),
                    is_active = COALESCE(%s, is_active),
                    allowed_schemas = COALESCE(%s, allowed_schemas)
                WHERE id = %s
                RETURNING {_COLUMNS}
                """,
                (payload.name, payload.is_active, payload.allowed_schemas, target_id),
            )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Target not found")
        conn.commit()
        return _row_to_target(row)


@router.delete("/{target_id}", status_code=204)
def delete_target(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM targets WHERE id = %s", (target_id,))
        conn.commit()


@router.post("/test", response_model=ConnectionTestResult)
def test_new_connection(payload: ConnectionTestRequest):
    result = test_connection(
        payload.host, payload.port, payload.dbname, payload.username, payload.password, payload.sslmode
    )
    return ConnectionTestResult(**result)


@router.post("/{target_id}/test", response_model=ConnectionTestResult)
def retest_target(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host, port, dbname, username, encrypted_password, sslmode FROM targets WHERE id = %s",
            (target_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Target not found")
        host, port, dbname, username, enc_password, sslmode = row
        try:
            password = decrypt(enc_password)
        except InvalidToken:
            # Same cause as target_conn.py::connect_to_target's InvalidToken
            # handling (the server's encryption key changed since this
            # password was saved) — surfaced as a normal failed-test result
            # here rather than a raised error, so it shows up in the
            # Connections card's existing "Not connected" state like any
            # other failure, with the fix spelled out in the message.
            result = {
                "ok": False,
                "pg_version": None,
                "uptime_seconds": None,
                "uptime_human": None,
                "message": "Stored password can no longer be decrypted (the server's encryption key "
                "changed since it was saved) — update the password for this connection to fix it.",
            }
        else:
            result = test_connection(host, port, dbname, username, password, sslmode)
        cur.execute(
            """
            UPDATE targets
            SET detected_pg_version = %s, last_test_ok = %s, last_test_at = now(), last_test_message = %s
            WHERE id = %s
            """,
            (result.get("pg_version"), result["ok"], result.get("message"), target_id),
        )
        conn.commit()
    return ConnectionTestResult(**result)
