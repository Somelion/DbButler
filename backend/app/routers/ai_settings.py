from fastapi import APIRouter, HTTPException

from app.crypto import encrypt
from app.db.store import store_conn
from app.schemas import AiSettingsOut, AiSettingsUpdate
from app.url_safety import validate_http_url

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
    endpoint_url = payload.endpoint_url
    if payload.provider == "local":
        try:
            endpoint_url = validate_http_url(payload.endpoint_url, "The local model endpoint URL")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT provider, endpoint_url FROM ai_settings WHERE id = 1")
        current_provider, current_endpoint_url = cur.fetchone()
        # The stored key is sent as a credential to wherever the provider/
        # endpoint points, so "keep the existing key" only holds while that
        # destination stays the same — otherwise anyone who can change the
        # endpoint could have the saved key delivered to a server of their
        # choosing. A changed destination clears it unless a new one is given.
        destination_changed = payload.provider != current_provider or (
            payload.provider == "local" and endpoint_url != current_endpoint_url
        )
        api_key = payload.api_key
        if api_key is None and destination_changed:
            api_key = ""

        if api_key is None:
            # Leave whatever key is already stored untouched.
            cur.execute(
                """
                UPDATE ai_settings
                SET provider = %s, endpoint_url = %s, model_name = %s, updated_at = now()
                WHERE id = 1
                """,
                (payload.provider, endpoint_url, payload.model_name),
            )
        else:
            # "" clears the stored key; anything else replaces it, encrypted.
            encrypted = encrypt(api_key) if api_key else None
            cur.execute(
                """
                UPDATE ai_settings
                SET provider = %s, endpoint_url = %s, model_name = %s,
                    encrypted_api_key = %s, updated_at = now()
                WHERE id = 1
                """,
                (payload.provider, endpoint_url, payload.model_name, encrypted),
            )
        conn.commit()

        cur.execute(f"SELECT {_COLUMNS} FROM ai_settings WHERE id = 1")
        return _row_to_out(cur.fetchone())
