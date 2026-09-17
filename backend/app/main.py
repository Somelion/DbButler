import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_auth
from app.config import settings
from app.db.migrate import run_migrations
from app.routers.activity import router as activity_router
from app.routers.advisor_archive import router as advisor_archive_router
from app.routers.ai_settings import router as ai_settings_router
from app.routers.alert_settings import router as alert_settings_router
from app.routers.backup_advisor import router as backup_advisor_router
from app.routers.config_advisor import router as config_advisor_router
from app.routers.config_tuning import router as config_tuning_router
from app.routers.dashboard import router as dashboard_router
from app.routers.dashboard_settings import router as dashboard_settings_router
from app.routers.diagnose import router as diagnose_router
from app.routers.explain import router as explain_router
from app.routers.extension_advisor import router as extension_advisor_router
from app.routers.hardware_profile import router as hardware_profile_router
from app.routers.index_advisor import router as index_advisor_router
from app.routers.index_coverage_analysis import router as index_coverage_analysis_router
from app.routers.index_testing import router as index_testing_router
from app.routers.maintenance import router as maintenance_router
from app.routers.metrics import router as metrics_router
from app.routers.pgbouncer import router as pgbouncer_router
from app.routers.plan_regressions import router as plan_regressions_router
from app.routers.pre_upgrade_advisor import router as pre_upgrade_advisor_router
from app.routers.query_analysis import router as query_analysis_router
from app.routers.query_history import router as query_history_router
from app.routers.query_stats import router as query_stats_router
from app.routers.replication_advisor import router as replication_advisor_router
from app.routers.schema_lint import router as schema_lint_router
from app.routers.scheduler_settings import router as scheduler_settings_router
from app.routers.security_advisor import router as security_advisor_router
from app.routers.table_health import router as table_health_router
from app.routers.targets import router as targets_router
from app.routers.wait_events import router as wait_events_router
from app.scheduler import start_scheduler


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(title="PostgreDba API", lifespan=lifespan)

# Every /api/* route requires the shared AUTH_TOKEN (app/auth.py) — /health
# and /health/store stay open since they carry no data and the Docker
# healthcheck itself has no token to present.
_AUTH = [Depends(require_auth)]
app.include_router(targets_router, dependencies=_AUTH)
app.include_router(metrics_router, dependencies=_AUTH)
app.include_router(activity_router, dependencies=_AUTH)
app.include_router(table_health_router, dependencies=_AUTH)
app.include_router(dashboard_router, dependencies=_AUTH)
app.include_router(dashboard_settings_router, dependencies=_AUTH)
app.include_router(query_stats_router, dependencies=_AUTH)
app.include_router(explain_router, dependencies=_AUTH)
app.include_router(diagnose_router, dependencies=_AUTH)
app.include_router(index_advisor_router, dependencies=_AUTH)
app.include_router(index_coverage_analysis_router, dependencies=_AUTH)
app.include_router(index_testing_router, dependencies=_AUTH)
app.include_router(schema_lint_router, dependencies=_AUTH)
app.include_router(advisor_archive_router, dependencies=_AUTH)
app.include_router(config_advisor_router, dependencies=_AUTH)
app.include_router(replication_advisor_router, dependencies=_AUTH)
app.include_router(backup_advisor_router, dependencies=_AUTH)
app.include_router(pre_upgrade_advisor_router, dependencies=_AUTH)
app.include_router(security_advisor_router, dependencies=_AUTH)
app.include_router(extension_advisor_router, dependencies=_AUTH)
app.include_router(hardware_profile_router, dependencies=_AUTH)
app.include_router(config_tuning_router, dependencies=_AUTH)
app.include_router(maintenance_router, dependencies=_AUTH)
app.include_router(scheduler_settings_router, dependencies=_AUTH)
app.include_router(ai_settings_router, dependencies=_AUTH)
app.include_router(alert_settings_router, dependencies=_AUTH)
app.include_router(query_analysis_router, dependencies=_AUTH)
app.include_router(query_history_router, dependencies=_AUTH)
app.include_router(plan_regressions_router, dependencies=_AUTH)
app.include_router(pgbouncer_router, dependencies=_AUTH)
app.include_router(wait_events_router, dependencies=_AUTH)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Confirms the backend process itself is up. Unauthenticated: used by
    the Docker healthcheck, which has no token to present."""
    return {"status": "ok"}


@app.get("/health/store")
def health_store():
    """Confirms the backend can reach its own history/metrics database
    (pgdba-store). Unauthenticated, same reasoning as /health."""
    with psycopg.connect(settings.store_database_url, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return {"status": "ok"}


@app.get("/api/auth/check", dependencies=_AUTH)
def auth_check():
    """The frontend calls this once to validate a token before trusting it —
    reaching this point means require_auth already accepted it."""
    return {"ok": True}
