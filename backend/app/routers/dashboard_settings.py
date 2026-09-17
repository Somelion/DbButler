from fastapi import APIRouter

from app.db.store import store_conn
from app.schemas import DashboardCategoryInfo, DashboardSettingsOut, DashboardSettingsUpdate

router = APIRouter(prefix="/api/dashboard-settings", tags=["dashboard-settings"])

# Every tile/widget the Dashboard can show, independent of whether this
# particular target currently has data for it — the Customize panel lists
# all of these, and the Dashboard itself filters CategoryStatus.key /
# these widget keys against the hidden set a user has saved. Adding a new
# Dashboard tile in the future is: add its CategoryStatus in
# routers/dashboard.py, then add one entry here so it's toggleable too.
DASHBOARD_CATEGORIES = [
    DashboardCategoryInfo(key="connections", label="Connections", description="Active sessions and idle-in-transaction time."),
    DashboardCategoryInfo(key="locks", label="Locks", description="Sessions currently blocked waiting on another session."),
    DashboardCategoryInfo(key="bloat", label="Bloat", description="The worst table's dead-row percentage."),
    DashboardCategoryInfo(key="cache", label="Cache Hit Rate", description="Share of reads served from memory vs. disk."),
    DashboardCategoryInfo(key="checkpoints", label="Checkpoints", description="Scheduled vs. forced checkpoints."),
    DashboardCategoryInfo(key="wraparound", label="Wraparound", description="The oldest table's transaction ID age."),
    DashboardCategoryInfo(
        key="autovacuum",
        label="Autovacuum",
        description="Whether autovacuum is currently running and how stale the least-recently-vacuumed table is.",
    ),
    DashboardCategoryInfo(
        key="replication",
        label="Replication",
        description="Connected replicas' lag and inactive replication slots retaining WAL.",
    ),
    DashboardCategoryInfo(
        key="backup", label="Backup & WAL", description="WAL archiving failures and WAL directory growth."
    ),
    DashboardCategoryInfo(
        key="pooler", label="Pooler", description="PgBouncer pool wait times — only shown when a PgBouncer connection is configured."
    ),
    DashboardCategoryInfo(
        key="wait_events",
        label="Active Sessions by Wait State",
        description="A live breakdown of currently-active sessions by what they're waiting on (CPU, lock, I/O, ...).",
    ),
    DashboardCategoryInfo(
        key="cache_trend", label="Cache Hit Rate Trend", description="A live trend chart of cache hit rate."
    ),
]


@router.get("", response_model=DashboardSettingsOut)
def get_dashboard_settings():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT hidden_categories, updated_at FROM dashboard_settings WHERE id = 1")
        hidden_categories, updated_at = cur.fetchone()
    return DashboardSettingsOut(
        categories=DASHBOARD_CATEGORIES, hidden_categories=list(hidden_categories or []), updated_at=updated_at
    )


@router.put("", response_model=DashboardSettingsOut)
def update_dashboard_settings(payload: DashboardSettingsUpdate):
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE dashboard_settings SET hidden_categories = %s, updated_at = now() WHERE id = 1",
            (payload.hidden_categories,),
        )
        conn.commit()
        cur.execute("SELECT hidden_categories, updated_at FROM dashboard_settings WHERE id = 1")
        hidden_categories, updated_at = cur.fetchone()
    return DashboardSettingsOut(
        categories=DASHBOARD_CATEGORIES, hidden_categories=list(hidden_categories or []), updated_at=updated_at
    )
