import uuid

import pytest
from cryptography.fernet import InvalidToken

from app import alerting
from app.crypto import decrypt, encrypt
from app.pgbouncer_status import build_pooler_status

FINDINGS = [
    {"severity": "attention", "title": "Table bloat"},
    {"severity": "critical", "title": "Wraparound"},
]


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://hooks.slack.com/services/T/B/x", "slack"),
        ("https://discord.com/api/webhooks/1/x", "discord"),
        ("https://discordapp.com/api/webhooks/1/x", "discord"),
        ("https://example.webhook.office.com/webhookb2/x", "teams"),
        ("https://mattermost.example.com/hooks/x", "generic"),
    ],
)
def test_detect_webhook_kind(url, kind):
    assert alerting.detect_webhook_kind(url) == kind


def test_slack_payload_is_mrkdwn_text():
    payload = alerting._format_payload("slack", uuid.uuid4(), "db1/app", FINDINGS)
    assert payload["text"].startswith("*")
    assert "[CRITICAL] Wraparound" in payload["text"]


def test_discord_payload_is_truncated_to_the_api_limit():
    many = [{"severity": "attention", "title": "x" * 100}] * 50
    payload = alerting._format_payload("discord", uuid.uuid4(), "db1/app", many)
    assert len(payload["content"]) == alerting.DISCORD_CONTENT_MAX_LENGTH
    assert payload["content"].endswith("…")


def test_teams_card_takes_the_worst_severity_color():
    payload = alerting._format_payload("teams", uuid.uuid4(), "db1/app", FINDINGS)
    assert payload["themeColor"] == alerting.TEAMS_THEME_COLOR["critical"]
    assert payload["@type"] == "MessageCard"


def test_generic_payload_carries_structured_findings_and_slack_text():
    target_id = uuid.uuid4()
    payload = alerting._format_payload("generic", target_id, "db1/app", FINDINGS)
    assert payload["target_id"] == str(target_id)
    assert payload["findings"] == FINDINGS
    assert "text" in payload


def test_crypto_round_trip_and_tamper_detection():
    token = encrypt("s3cret-password")
    assert token != "s3cret-password"
    assert decrypt(token) == "s3cret-password"
    assert encrypt("same") != encrypt("same")  # random IV per value
    with pytest.raises(InvalidToken):
        decrypt(token[:-4] + "AAAA")


def test_pooler_status_only_counts_this_targets_database():
    rows = [
        {"database": "app", "cl_active": 5, "cl_waiting": 0, "sv_active": 2, "sv_idle": 1, "maxwait": 0, "pool_mode": "transaction"},
        {"database": "other", "cl_active": 1, "cl_waiting": 9, "sv_active": 0, "sv_idle": 0, "maxwait": 30},
    ]
    category, finding = build_pooler_status(rows, "app")
    assert category.severity == "healthy"
    assert category.value == "5 active"
    assert finding is None
    assert build_pooler_status(rows, "missing") == (None, None)


def test_pooler_status_flags_waiting_clients():
    rows = [{"database": "app", "cl_active": 5, "cl_waiting": 3, "sv_active": 10, "sv_idle": 0, "maxwait": 7}]
    category, finding = build_pooler_status(rows, "app")
    assert category.severity == "critical"
    assert finding.id == "pooler-clients-waiting"
