from fastapi import APIRouter, HTTPException

from app.crypto import encrypt
from app.db.store import store_conn
from app.schemas import AiSettingsOut, AiSettingsUpdate

router = APIRouter(prefix="/api/ai", tags=["ai-settings"])

_COLUMNS = "provider, endpoint_url, model_name, encrypted_api_key, updated_at"


def _row_to_out(row) -> AiSettingsOut:
    provider, endpoint_url, model_name, encrypted_api_key, updated_at = row
    return AiSettingsOut(
        provider=provider,
        endpoint_url=endpoint_url,
        model_name=model_name,
        has_api_key=bool(encrypted_api_key),
        updated_at=updated_at,
    )


@router.get("/settings", response_model=AiSettingsOut)
def get_ai_settings():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM ai_settings WHERE id = 1")
        row = cur.fetchone()
        return _row_to_out(row)


@router.put("/settings", response_model=AiSettingsOut)
def update_ai_settings(payload: AiSettingsUpdate):
    if payload.provider not in ("gemini", "local"):
        raise HTTPException(status_code=400, detail="provider must be 'gemini' or 'local'.")
    if payload.provider == "local" and not payload.endpoint_url:
        raise HTTPException(status_code=400, detail="endpoint_url is required for the local provider.")

    with store_conn() as conn, conn.cursor() as cur:
        if payload.api_key is None:
            # Leave whatever key is already stored untouched.
            cur.execute(
                """
                UPDATE ai_settings
                SET provider = %s, endpoint_url = %s, model_name = %s, updated_at = now()
                WHERE id = 1
                """,
                (payload.provider, payload.endpoint_url, payload.model_name),
            )
        else:
            # "" clears the stored key; anything else replaces it, encrypted.
            encrypted = encrypt(payload.api_key) if payload.api_key else None
            cur.execute(
                """
                UPDATE ai_settings
                SET provider = %s, endpoint_url = %s, model_name = %s,
                    encrypted_api_key = %s, updated_at = now()
                WHERE id = 1
                """,
                (payload.provider, payload.endpoint_url, payload.model_name, encrypted),
            )
        conn.commit()

        cur.execute(f"SELECT {_COLUMNS} FROM ai_settings WHERE id = 1")
        return _row_to_out(cur.fetchone())
