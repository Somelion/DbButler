"""Dashboard session findings: built from ACTIVITY_SUMMARY_QUERY rows
(pid, state, duration, blocked_by, application_name). The rows grew an
application_name column and the findings' 4-way unpacking crashed the whole
Dashboard (500) as soon as a session was idle in a transaction or a query
ran long."""

from app.routers.dashboard import _idle_in_transaction_finding, _long_running_query_finding
from app.severity import IDLE_IN_TX_CRITICAL_SECONDS, LONG_RUNNING_QUERY_CRITICAL_SECONDS


def test_idle_in_transaction_finding_from_a_full_row():
    row = (4242, "idle in transaction", IDLE_IN_TX_CRITICAL_SECONDS + 5, [], "billing-api")

    finding = _idle_in_transaction_finding(row)

    assert finding.id == "idle-tx-4242"
    assert finding.severity == "critical"
    assert f"{IDLE_IN_TX_CRITICAL_SECONDS + 5}s" in finding.summary


def test_long_running_query_finding_from_a_full_row():
    row = (77, "active", LONG_RUNNING_QUERY_CRITICAL_SECONDS + 1, [12], "TimescaleDB Background Worker")

    finding = _long_running_query_finding(row)

    assert finding.id == "long-running-query-77"
    assert finding.severity == "critical"
