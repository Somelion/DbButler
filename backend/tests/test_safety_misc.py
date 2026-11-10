import pytest

from app.replication_advisor import find_inactive_slot_findings
from app.severity import replication_slot_severity
from app.url_safety import redact_secret, validate_http_url


def test_slot_with_null_retained_bytes_does_not_crash():
    assert replication_slot_severity(False, "lost", None) == "critical"
    assert replication_slot_severity(False, None, None) == "healthy"


def test_lost_slot_finding_renders_without_retained_bytes():
    [finding] = find_inactive_slot_findings([("old_slot", "logical", False, "lost", None)])
    assert finding["severity"] == "critical"
    assert "unknown amount of WAL" in finding["summary"]


@pytest.mark.parametrize("url", ["https://hooks.slack.com/services/x", "http://ollama:11434/v1/chat/completions"])
def test_http_urls_are_accepted(url):
    assert validate_http_url(f"  {url} ", "URL") == url


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "hooks.slack.com/x", "https://user:pw@host/x", ""])
def test_other_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_http_url(url, "URL")


def test_redact_secret():
    message = "404 Client Error for url: https://hooks.slack.com/services/T0/B0/secret"
    assert redact_secret(message, "https://hooks.slack.com/services/T0/B0/secret", None) == (
        "404 Client Error for url: <redacted>"
    )
