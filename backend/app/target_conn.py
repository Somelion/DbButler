import uuid

import psycopg
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

from app.crypto import decrypt
from app.db.store import store_conn


def get_target_credentials(target_id: uuid.UUID):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT host, port, dbname, username, encrypted_password, sslmode FROM targets WHERE id = %s",
            (target_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Target not found")
        return row


def connect_to_target(target_id: uuid.UUID, autocommit: bool = False) -> psycopg.Connection:
    # VACUUM refuses to run inside a transaction block, so maintenance
    # actions need autocommit=True; every other caller keeps the default.
    host, port, dbname, username, enc_password, sslmode = get_target_credentials(target_id)
    try:
        password = decrypt(enc_password)
    except InvalidToken as exc:
        # Most common cause: SECRET_KEY changed since this password was
        # encrypted — Fernet keys can't be rotated after the fact, so the
        # fix is re-entering the password (Connections screen), not
        # anything this app can do automatically. Not a psycopg.Error, so
        # every router's existing `except psycopg.Error` leaves this
        # HTTPException to propagate as-is rather than a raw 500.
        raise HTTPException(
            status_code=409,
            detail="This connection's stored password can no longer be decrypted (the server's "
            "encryption key changed since it was saved). Update the password for this connection "
            "on the Connections screen to fix it.",
        ) from exc
    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=username,
        password=password,
        sslmode=sslmode,
        connect_timeout=5,
        autocommit=autocommit,
    )
