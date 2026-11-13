"""Feature flags for the UI. The UI is a static build served by nginx, so it
can't read .env itself; it asks here once per session. Set in .env as
APP_FEATURE_* (docker-compose.yml passes them on as FEATURE_*)."""

from fastapi import APIRouter

from app.config import settings
from app.schemas import FeatureFlags

router = APIRouter(prefix="/api/features", tags=["features"])


@router.get("", response_model=FeatureFlags)
def get_features():
    return {"workload_map_3d": settings.feature_workload_3d}
