"""Which machine integrations are switched on, for the Settings screen's
connection instructions. Never returns the tokens themselves — only
whether each one is set."""

from fastapi import APIRouter

from app.config import settings
from app.mcp_tools import TOOLS
from app.schemas import IntegrationsStatus

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("", response_model=IntegrationsStatus)
def get_integrations():
    return {
        "mcp_enabled": bool(settings.mcp_token),
        "metrics_enabled": bool(settings.metrics_token),
        "public_url": settings.public_url.strip().rstrip("/") or None,
        "mcp_tools": [tool.name for tool in TOOLS],
    }
