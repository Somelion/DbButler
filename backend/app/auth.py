"""Shared-secret auth gate for the API.

Local-only test-phase default was no auth at all — this is the minimum viable
gate for letting anyone other than one developer on one machine reach the
app: a single token, set once at deploy time (AUTH_TOKEN), required on every
/api/* request. Deliberately not per-user login/RBAC — see
docs/ARCHITECTURE.md for why that's a later decision, not a v1 requirement.
"""

import secrets

from fastapi import Header, HTTPException, status

from app.config import settings


def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not settings.auth_token:
        # An unset token is a misconfiguration, not "auth disabled by
        # choice" — refuse to serve rather than silently letting every
        # request through.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server auth token is not configured. Set AUTH_TOKEN and restart the backend.",
        )

    if not _bearer_matches(authorization, settings.auth_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing auth token.")


def _bearer_matches(authorization: str | None, token: str) -> bool:
    # Compared as bytes: compare_digest raises TypeError (a 500) on a str
    # holding non-ASCII characters, which any client can send in a header.
    expected = f"Bearer {token}".encode()
    return bool(authorization) and secrets.compare_digest(authorization.encode("utf-8", "surrogateescape"), expected)


def _require_integration_token(token: str, authorization: str | None) -> None:
    """An integration endpoint whose token isn't configured doesn't exist
    (404) — it's opt-in, unlike the main API whose missing token is a
    misconfiguration (503 above)."""
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    if not _bearer_matches(authorization, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token.")


def require_mcp_token(authorization: str | None = Header(default=None)) -> None:
    """MCP_TOKEN, for the read-only MCP endpoint only (routers/mcp.py)."""
    _require_integration_token(settings.mcp_token, authorization)


def require_metrics_token(authorization: str | None = Header(default=None)) -> None:
    """METRICS_TOKEN, for the Prometheus endpoint only (routers/prometheus.py)."""
    _require_integration_token(settings.metrics_token, authorization)
