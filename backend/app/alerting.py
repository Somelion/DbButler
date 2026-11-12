"""Outbound delivery for Actionable Alerting — a single webhook POST fired
when a finding newly opens (never seen before, or reopened after being
resolved — see scheduler.py::_reconcile_category_findings) at or above the
configured minimum severity.

One URL field, no separate "which system is this" setting: the receiving
platform is detected from the URL itself (detect_webhook_kind below) and the
payload shaped to match — Slack's {"text": ...}, Discord's {"content": ...},
or a Teams MessageCard. Anything unrecognized (Mattermost — self-hosted, no
fixed domain, but wire-compatible with Slack's format — a generic receiver,
a testing endpoint) gets a payload carrying both a `text` key (so any
Slack-compatible receiver still renders something sensible) and the full
structured finding data, rather than forcing a choice between the two.

Deliberately webhook-only for v1: covers every chat platform reachable via
an incoming-webhook-style POST, without needing SMTP configuration — a
materially bigger, separate piece of infrastructure (host/port/credentials/
from-address/templates) that isn't bundled in here.
"""

import logging
import re

import requests
from cryptography.fernet import InvalidToken

from app.crypto import decrypt
from app.db.store import store_conn
from app.severity import SEVERITY_RANK
from app.url_safety import redact_secret

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10

# Slack and Discord both have one stable, dedicated webhook domain. Teams'
# has churned repeatedly (classic Office 365 Connectors on webhook.office.com
# retired May 2026; Power Automate Workflows that replaced them have since
# moved their own trigger URLs from logic.azure.com to *.powerplatform.com)
# — matching all of them, past and present, costs nothing since a Teams
# MessageCard payload is what Workflows-based webhooks expect regardless of
# which generation issued the URL.
SLACK_URL_PATTERN = re.compile(r"hooks\.slack\.com", re.IGNORECASE)
DISCORD_URL_PATTERN = re.compile(r"discord(?:app)?\.com/api/webhooks", re.IGNORECASE)
TEAMS_URL_PATTERN = re.compile(
    r"webhook\.office\.com|logic\.azure\.com|powerplatform\.com|powerautomate\.com|flow\.microsoft\.com",
    re.IGNORECASE,
)

DISCORD_CONTENT_MAX_LENGTH = 2000

TEAMS_THEME_COLOR = {"critical": "D93F3F", "attention": "E8A33D"}
TEAMS_DEFAULT_THEME_COLOR = "6B7280"


def detect_webhook_kind(url: str) -> str:
    """'slack' | 'discord' | 'teams' | 'generic' — pure function of the URL,
    never persisted, so a URL swap alone changes delivery format on the next
    send with nothing else to update."""
    if SLACK_URL_PATTERN.search(url):
        return "slack"
    if DISCORD_URL_PATTERN.search(url):
        return "discord"
    if TEAMS_URL_PATTERN.search(url):
        return "teams"
    return "generic"


def _finding_lines(target_label: str, findings: list[dict]) -> list[str]:
    count = len(findings)
    lines = [f"PostgreDba — {count} new finding{'s' if count != 1 else ''} on {target_label}"]
    for finding in findings:
        lines.append(f"• [{finding['severity'].upper()}] {finding['title']}")
    return lines


def _format_slack_payload(target_label: str, findings: list[dict]) -> dict:
    lines = _finding_lines(target_label, findings)
    lines[0] = f"*{lines[0]}*"  # Slack mrkdwn bold
    return {"text": "\n".join(lines)}


def _format_discord_payload(target_label: str, findings: list[dict]) -> dict:
    content = "\n".join(_finding_lines(target_label, findings))
    if len(content) > DISCORD_CONTENT_MAX_LENGTH:
        content = content[: DISCORD_CONTENT_MAX_LENGTH - 1] + "…"
    return {"content": content}


def _format_teams_payload(target_label: str, findings: list[dict]) -> dict:
    count = len(findings)
    title = f"PostgreDba — {count} new finding{'s' if count != 1 else ''} on {target_label}"
    worst = max((f["severity"] for f in findings), key=lambda s: SEVERITY_RANK.get(s, 0), default="attention")
    lines = [f"**[{finding['severity'].upper()}]** {finding['title']}" for finding in findings]
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": title,
        "themeColor": TEAMS_THEME_COLOR.get(worst, TEAMS_DEFAULT_THEME_COLOR),
        "title": title,
        "text": "\n\n".join(lines),
    }


def _format_generic_payload(target_id, target_label: str, findings: list[dict]) -> dict:
    return {
        "text": "\n".join(_finding_lines(target_label, findings)),
        "target_id": str(target_id),
        "target_label": target_label,
        "findings": findings,
    }


def _format_payload(kind: str, target_id, target_label: str, findings: list[dict]) -> dict:
    if kind == "slack":
        return _format_slack_payload(target_label, findings)
    if kind == "discord":
        return _format_discord_payload(target_label, findings)
    if kind == "teams":
        return _format_teams_payload(target_label, findings)
    return _format_generic_payload(target_id, target_label, findings)


def _get_alert_settings():
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, encrypted_webhook_url, min_severity FROM alert_settings WHERE id = 1")
        return cur.fetchone()


def _post_webhook(webhook_url: str, payload: dict, target_id) -> None:
    try:
        response = requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        # A delivery failure must never take down the collector cycle that
        # triggered it — log and move on, same reasoning as every other
        # collector's try/except in scheduler.py. The webhook URL is the
        # secret (anyone holding it can post to the channel), and requests
        # puts it in its error text, so it's redacted before logging.
        logger.warning(
            "alert webhook delivery failed for target %s: %s", target_id, redact_secret(str(exc), webhook_url)
        )


def _decrypt_webhook_url(encrypted_webhook_url) -> str | None:
    """None when the stored URL can no longer be decrypted (SECRET_KEY
    changed since it was saved) — the same situation target passwords hit
    (app/target_conn.py); alerting just stays quiet until it's re-entered."""
    try:
        return decrypt(encrypted_webhook_url)
    except InvalidToken:
        logger.warning("stored alert webhook URL can't be decrypted (SECRET_KEY changed?) — re-enter it in Settings")
        return None


def maybe_send_alert(target_id, target_label: str, newly_open_findings: list[dict]) -> None:
    """No-op (never raises) when alerting is disabled, unconfigured, or
    nothing in newly_open_findings meets the configured minimum severity —
    the common case on every single cycle for most targets. Called from
    scheduler.py right after reconciling a batch of findings into
    deep_scan_findings, once per (target, category-group) run — so a
    single call's findings share one webhook message rather than one
    message per finding."""
    if not newly_open_findings:
        return

    row = _get_alert_settings()
    if row is None:
        return
    enabled, encrypted_webhook_url, min_severity = row
    if not enabled or not encrypted_webhook_url:
        return

    threshold = SEVERITY_RANK.get(min_severity, SEVERITY_RANK["critical"])
    qualifying = [f for f in newly_open_findings if SEVERITY_RANK.get(f["severity"], 0) >= threshold]
    if not qualifying:
        return

    webhook_url = _decrypt_webhook_url(encrypted_webhook_url)
    if webhook_url is None:
        return
    payload = _format_payload(detect_webhook_kind(webhook_url), target_id, target_label, qualifying)
    _post_webhook(webhook_url, payload, target_id)


def format_report_payload(kind: str, title: str, lines: list[str], link: str | None, severity: str, extra: dict) -> dict:
    """The weekly report's summary message (app/health_report.py), shaped
    for the receiving platform like the finding alerts above."""
    body = [f"• {line}" for line in lines]
    if link:
        body.append(f"Full report: {link}")
    if kind == "slack":
        return {"text": "\n".join([f"*{title}*", *body])}
    if kind == "discord":
        content = "\n".join([f"**{title}**", *body])
        if len(content) > DISCORD_CONTENT_MAX_LENGTH:
            content = content[: DISCORD_CONTENT_MAX_LENGTH - 1] + "…"
        return {"content": content}
    if kind == "teams":
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": title,
            "themeColor": TEAMS_THEME_COLOR.get(severity, TEAMS_DEFAULT_THEME_COLOR),
            "title": title,
            "text": "\n\n".join(lines),
        }
        if link:
            card["potentialAction"] = [{"@type": "OpenUri", "name": "Open the report", "targets": [{"os": "default", "uri": link}]}]
        return card
    return {"text": "\n".join([title, *body]), "report": {**extra, "lines": lines, "link": link, "severity": severity}}


def send_report_summary(title: str, lines: list[str], link: str | None, severity: str, extra: dict) -> bool:
    """Posts the weekly report summary when Settings has it switched on and
    a webhook URL is saved. Independent of the finding alerts' own on/off
    switch and minimum severity. Never raises; True when it was sent."""
    with store_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT report_webhook_enabled, encrypted_webhook_url FROM alert_settings WHERE id = 1")
        row = cur.fetchone()
    if row is None or not row[0] or not row[1]:
        return False
    webhook_url = _decrypt_webhook_url(row[1])
    if webhook_url is None:
        return False
    payload = format_report_payload(detect_webhook_kind(webhook_url), title, lines, link, severity, extra)
    try:
        response = requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("weekly report webhook delivery failed: %s", redact_secret(str(exc), webhook_url))
        return False
    return True


def send_test_webhook() -> dict:
    """Explicit user action from Settings' "Send test alert" button —
    bypasses the enabled/min_severity gating maybe_send_alert applies,
    since the user is deliberately testing right now rather than waiting
    for a real finding. Returns {"ok": bool, "message": str | None},
    never raises."""
    row = _get_alert_settings()
    if row is None or not row[1]:
        return {"ok": False, "message": "No webhook URL is configured yet."}

    _enabled, encrypted_webhook_url, _min_severity = row
    webhook_url = _decrypt_webhook_url(encrypted_webhook_url)
    if webhook_url is None:
        return {
            "ok": False,
            "message": "The saved webhook URL can no longer be decrypted (the server's encryption key "
            "changed). Enter the URL again and save.",
        }
    kind = detect_webhook_kind(webhook_url)
    message = "PostgreDba test alert — if you can see this, your webhook is configured correctly."
    if kind == "slack":
        payload = {"text": message}
    elif kind == "discord":
        payload = {"content": message}
    elif kind == "teams":
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": "PostgreDba test alert",
            "themeColor": TEAMS_DEFAULT_THEME_COLOR,
            "title": "PostgreDba test alert",
            "text": message,
        }
    else:
        payload = {"text": message, "test": True}
    try:
        response = requests.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        return {"ok": False, "message": redact_secret(str(exc), webhook_url)}
    return {"ok": True, "message": None}
