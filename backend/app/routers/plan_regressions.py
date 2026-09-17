import uuid

from fastapi import APIRouter

from app.db.store import store_conn
from app.routers.advisor_archive import get_archived_finding_ids
from app.schemas import IndexFinding, PlanRegressionsResponse
from app.severity import plan_regression_severity

router = APIRouter(prefix="/api/targets", tags=["plan-regressions"])

# How many query_stat_snapshots rows (each one roughly query_history_cycle's
# cadence apart, 5 minutes by default) to average on either side of a plan
# change before trusting the comparison — one sample per side is too easy for
# a single unusually slow/fast run to look like a trend.
BASELINE_SAMPLE_COUNT = 5
CURRENT_SAMPLE_COUNT = 3
MIN_SAMPLES_EACH_SIDE = 2


def _truncate(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@router.get("/{target_id}/plan-regressions", response_model=PlanRegressionsResponse)
def get_plan_regressions(target_id: uuid.UUID):
    """Compares each tracked query's most recent plan-fingerprint change
    (app/plan_fingerprint.py, captured by scheduler.py::run_plan_regression_
    cycle into query_plan_snapshots) against its own latency history
    (query_stat_snapshots) immediately before and after that change. Computed
    fresh on every request rather than persisted — same "recompute, don't
    store findings" pattern as Index Advisor/Schema Lint/Configuration
    Advisor, just reading this app's own store instead of the target, so
    there's no separate reconcile/resolve job to keep in sync."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, queryid, query_text FROM tracked_queries WHERE target_id = %s",
            (target_id,),
        )
        tracked = cur.fetchall()

        findings = []
        for tracked_id, queryid, query_text in tracked:
            cur.execute(
                """
                SELECT collected_at, plan_summary
                FROM query_plan_snapshots
                WHERE tracked_query_id = %s
                ORDER BY collected_at DESC
                LIMIT 2
                """,
                (tracked_id,),
            )
            snaps = cur.fetchall()
            if len(snaps) < 2:
                # Either never captured, or only ever seen one plan shape —
                # nothing to compare a "before" against.
                continue
            (changed_at, new_summary), (_prev_at, old_summary) = snaps

            cur.execute(
                """
                SELECT mean_exec_ms FROM query_stat_snapshots
                WHERE tracked_query_id = %s AND collected_at < %s
                ORDER BY collected_at DESC LIMIT %s
                """,
                (tracked_id, changed_at, BASELINE_SAMPLE_COUNT),
            )
            baseline_samples = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                SELECT mean_exec_ms FROM query_stat_snapshots
                WHERE tracked_query_id = %s AND collected_at >= %s
                ORDER BY collected_at ASC LIMIT %s
                """,
                (tracked_id, changed_at, CURRENT_SAMPLE_COUNT),
            )
            current_samples = [row[0] for row in cur.fetchall()]

            if len(baseline_samples) < MIN_SAMPLES_EACH_SIDE or len(current_samples) < MIN_SAMPLES_EACH_SIDE:
                continue

            baseline_ms = sum(baseline_samples) / len(baseline_samples)
            current_ms = sum(current_samples) / len(current_samples)
            if baseline_ms <= 0:
                continue

            ratio = current_ms / baseline_ms
            severity = plan_regression_severity(ratio)
            if severity not in ("attention", "critical"):
                # Plan changed but didn't get meaningfully slower (or got
                # faster) — not something that needs a DBA's attention, so no
                # finding, matching this app's "don't add noise" convention.
                continue

            findings.append(
                {
                    "id": f"plan-regression-{queryid}",
                    "category": "plan regression",
                    "severity": severity,
                    "title": f"Query plan changed and got {ratio:.1f}x slower",
                    "summary": (
                        f"This query's EXPLAIN plan changed at {changed_at.isoformat()}. Mean latency "
                        f"went from ~{baseline_ms:.1f}ms to ~{current_ms:.1f}ms since."
                    ),
                    "detail": (
                        f"Before: {old_summary}\nAfter: {new_summary}\n\nQuery: {_truncate(query_text)}"
                    ),
                    "suggested_action": (
                        "Open this query in Explain to see the current full plan. Common causes: a "
                        "recent ANALYZE/autoanalyze changed the planner's row estimates, an index this "
                        "query relied on was dropped or is now bloated, or the data behind the filtered "
                        "columns shifted enough to flip the planner's choice."
                    ),
                }
            )

    archived_ids = get_archived_finding_ids(target_id)
    findings = [finding for finding in findings if finding["id"] not in archived_ids]
    return PlanRegressionsResponse(findings=[IndexFinding(**finding) for finding in findings])
