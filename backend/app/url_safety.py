"""Validation and redaction for the two user-configured outbound URLs — the
local AI model endpoint (routers/ai_settings.py) and the alert webhook
(routers/alert_settings.py). Both are effectively credentials (a Slack/Teams
webhook URL *is* the secret), so they're never echoed back in logs or error
messages, and only plain http(s) URLs are accepted — no file://, gopher://
or other schemes `requests` adapters might understand."""

from urllib.parse import urlsplit


def validate_http_url(url: str, field_label: str) -> str:
    """Returns the stripped URL, or raises ValueError with a user-facing
    message naming field_label."""
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{field_label} must be a full http:// or https:// URL.")
    if parts.username or parts.password:
        raise ValueError(f"{field_label} must not contain a username or password.")
    return cleaned


def redact_secret(text: str, *secrets: str | None) -> str:
    """Removes every occurrence of each secret (e.g. a full webhook URL or
    an API key) from an error message before it's logged or stored."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text
