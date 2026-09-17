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

    expected = f"Bearer {settings.auth_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing auth token.")
