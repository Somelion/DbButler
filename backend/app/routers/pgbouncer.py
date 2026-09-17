import uuid

from fastapi import APIRouter, HTTPException

from app.crypto import decrypt, encrypt
from app.db.store import store_conn
from app.pgbouncer_probe import test_pgbouncer_connection
from app.schemas import PgBouncerConnectionCreate, PgBouncerConnectionOut, PgBouncerTestResult

router = APIRouter(prefix="/api/targets", tags=["pgbouncer"])

_COLUMNS = "target_id, host, port, username, sslmode, last_test_ok, last_test_at, last_test_message, created_at"


def _row_to_out(row) -> PgBouncerConnectionOut:
    return PgBouncerConnectionOut(
        target_id=row[0],
        host=row[1],
        port=row[2],
        username=row[3],
        sslmode=row[4],
        last_test_ok=row[5],
        last_test_at=row[6],
        last_test_message=row[7],
        created_at=row[8],
    )


@router.get("/{target_id}/pgbouncer", response_model=PgBouncerConnectionOut | None)
def get_pgbouncer_connection(target_id: uuid.UUID):
    """Returns null (not 404) when nothing is configured — this is a
    normal, common state most targets are in, not an error condition."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM pgbouncer_connections WHERE target_id = %s", (target_id,))
        row = cur.fetchone()
    return _row_to_out(row) if row else None


@router.post("/{target_id}/pgbouncer/test", response_model=PgBouncerTestResult)
def test_candidate_pgbouncer_connection(target_id: uuid.UUID, payload: PgBouncerConnectionCreate):
    """Tests a not-yet-saved config, mirroring routers/targets.py's global
    POST /test — target_id is unused here (the candidate config hasn't been
    persisted against it yet) but kept in the path for a consistent
    /{target_id}/pgbouncer* route family."""
    result = test_pgbouncer_connection(payload.host, payload.port, payload.username, payload.password, payload.sslmode)
    return PgBouncerTestResult(**result)


@router.post("/{target_id}/pgbouncer", response_model=PgBouncerConnectionOut, status_code=201)
def save_pgbouncer_connection(target_id: uuid.UUID, payload: PgBouncerConnectionCreate):
    result = test_pgbouncer_connection(payload.host, payload.port, payload.username, payload.password, payload.sslmode)
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO pgbouncer_connections
                (target_id, host, port, username, encrypted_password, sslmode,
                 last_test_ok, last_test_at, last_test_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
            ON CONFLICT (target_id) DO UPDATE
                SET host = EXCLUDED.host, port = EXCLUDED.port, username = EXCLUDED.username,
                    encrypted_password = EXCLUDED.encrypted_password, sslmode = EXCLUDED.sslmode,
                    last_test_ok = EXCLUDED.last_test_ok, last_test_at = now(),
                    last_test_message = EXCLUDED.last_test_message
            RETURNING {_COLUMNS}
            """,
            (
                target_id,
                payload.host,
                payload.port,
                payload.username,
                encrypt(payload.password),
                payload.sslmode,
                result["ok"],
                result.get("message"),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return _row_to_out(row)


@router.post("/{target_id}/pgbouncer/retest", response_model=PgBouncerTestResult)
def retest_pgbouncer_connection(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host, port, username, encrypted_password, sslmode FROM pgbouncer_connections WHERE target_id = %s",
            (target_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No PgBouncer connection configured for this target.")
        host, port, username, enc_password, sslmode = row
        result = test_pgbouncer_connection(host, port, username, decrypt(enc_password), sslmode)
        cur.execute(
            """
            UPDATE pgbouncer_connections
            SET last_test_ok = %s, last_test_at = now(), last_test_message = %s
            WHERE target_id = %s
            """,
            (result["ok"], result.get("message"), target_id),
        )
        conn.commit()
    return PgBouncerTestResult(**result)


@router.delete("/{target_id}/pgbouncer", status_code=204)
def delete_pgbouncer_connection(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pgbouncer_connections WHERE target_id = %s", (target_id,))
        conn.commit()
