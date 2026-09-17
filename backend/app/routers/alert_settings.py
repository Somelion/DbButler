from cryptography.fernet import InvalidToken
from fastapi import APIRouter

from app.alerting import detect_webhook_kind, send_test_webhook
from app.crypto import decrypt, encrypt
from app.db.store import store_conn
from app.schemas import AlertSettingsOut, AlertSettingsUpdate, AlertTestResult

router = APIRouter(prefix="/api/alerts", tags=["alert-settings"])

_COLUMNS = "enabled, encrypted_webhook_url, min_severity, updated_at"


def _row_to_out(row) -> AlertSettingsOut:
    enabled, encrypted_webhook_url, min_severity, updated_at = row
    webhook_kind = None
    if encrypted_webhook_url:
        try:
            webhook_kind = detect_webhook_kind(decrypt(encrypted_webhook_url))
        except InvalidToken:
            # SECRET_KEY rotated since this URL was saved — same situation
            # target passwords hit (app/target_conn.py); nothing to detect
            # until the user re-enters the URL, not worth a 500 over.
            webhook_kind = None
    return AlertSettingsOut(
        enabled=enabled,
        has_webhook_url=bool(encrypted_webhook_url),
        webhook_kind=webhook_kind,
        min_severity=min_severity,
        updated_at=updated_at,
    )


@router.get("/settings", response_model=AlertSettingsOut)
def get_alert_settings():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM alert_settings WHERE id = 1")
        return _row_to_out(cur.fetchone())


@router.put("/settings", response_model=AlertSettingsOut)
def update_alert_settings(payload: AlertSettingsUpdate):
    with store_conn() as conn, conn.cursor() as cur:
        if payload.webhook_url is None:
            # Leave whatever webhook URL is already stored untouched.
            cur.execute(
                "UPDATE alert_settings SET enabled = %s, min_severity = %s, updated_at = now() WHERE id = 1",
                (payload.enabled, payload.min_severity),
            )
        else:
            # "" clears the stored URL; anything else replaces it, encrypted.
            encrypted = encrypt(payload.webhook_url) if payload.webhook_url else None
            cur.execute(
                """
                UPDATE alert_settings
                SET enabled = %s, min_severity = %s, encrypted_webhook_url = %s, updated_at = now()
                WHERE id = 1
                """,
                (payload.enabled, payload.min_severity, encrypted),
            )
        conn.commit()

        cur.execute(f"SELECT {_COLUMNS} FROM alert_settings WHERE id = 1")
        return _row_to_out(cur.fetchone())


@router.post("/test", response_model=AlertTestResult)
def test_alert_webhook():
    return AlertTestResult(**send_test_webhook())
