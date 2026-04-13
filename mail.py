"""Transactional email sender.

Uses Resend's HTTP API when `RESEND_API_KEY` is set. If unset, send_*
functions log a warning and return False so local dev and tests run
without a mail provider configured.

Env vars:
    RESEND_API_KEY — API key from https://resend.com/api-keys
    MAIL_FROM      — "Izabael <hello@izabael.com>" (from a verified domain)
    MAIL_BASE_URL  — base URL used in confirm links (default https://izabael.com)
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("izabael.mail")

RESEND_API = "https://api.resend.com/emails"
DEFAULT_FROM = "Izabael <hello@izabael.com>"
DEFAULT_BASE_URL = "https://izabael.com"


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


async def _send(to: str, subject: str, html: str, text: str) -> bool:
    key = os.environ.get("RESEND_API_KEY")
    mail_from = os.environ.get("MAIL_FROM", DEFAULT_FROM)
    if not key:
        log.warning("RESEND_API_KEY not set — skipping email to %s", to)
        return False
    payload = {
        "from": mail_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as e:
        log.exception("resend request failed: %s", e)
        return False
    if resp.status_code >= 400:
        log.error("resend returned %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


_CONFIRM_HTML = """\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f6f4fb;font-family:Georgia,'Times New Roman',serif;color:#222;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f4fb;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="540" cellpadding="0" cellspacing="0" style="max-width:540px;background:#ffffff;border-radius:10px;padding:40px 36px;box-shadow:0 2px 12px rgba(80,60,160,0.08);">
        <tr><td align="center" style="font-size:36px;line-height:1;padding-bottom:8px;">🦋</td></tr>
        <tr><td align="center">
          <h1 style="margin:8px 0 24px;font-family:Georgia,serif;font-size:26px;font-weight:normal;color:#4a3a80;">One small thing</h1>
        </td></tr>
        <tr><td style="font-size:16px;line-height:1.6;color:#333;">
          <p style="margin:0 0 16px;">Hello there,</p>
          <p style="margin:0 0 16px;">Someone — probably you — asked to subscribe to the playground newsletter. Tap the button below to confirm and you're in.</p>
        </td></tr>
        <tr><td align="center" style="padding:28px 0 12px;">
          <a href="{confirm_url}" style="display:inline-block;background:#7b68ee;color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:6px;font-family:Georgia,serif;font-size:16px;font-weight:bold;">Confirm subscription</a>
        </td></tr>
        <tr><td style="font-size:13px;color:#777;line-height:1.6;padding-top:20px;">
          <p style="margin:0 0 12px;">Or paste this link into your browser:</p>
          <p style="margin:0 0 20px;word-break:break-all;"><a href="{confirm_url}" style="color:#7b68ee;">{confirm_url}</a></p>
          <p style="margin:0;">If this wasn't you, ignore this email — nothing happens until you click the link.</p>
        </td></tr>
        <tr><td style="border-top:1px solid #eee;margin-top:24px;padding-top:20px;font-size:12px;color:#999;text-align:center;">
          <p style="margin:24px 0 0;">Izabael's AI Playground · <a href="{base_url}" style="color:#7b68ee;">izabael.com</a><br>Flagship instance of SILT™ AI Playground</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""


_CONFIRM_TEXT = """\
Hello there,

Someone (probably you) asked to subscribe to Izabael's AI Playground newsletter.
Confirm your subscription here:

{confirm_url}

If this wasn't you, ignore this email.

— Izabael
{base_url}
"""


async def send_newsletter_confirmation(email: str, token: str) -> bool:
    """Send a double-opt-in confirmation email. Returns True on success."""
    base_url = os.environ.get("MAIL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    confirm_url = f"{base_url}/confirm?token={token}"
    return await _send(
        to=email,
        subject="Confirm your subscription to Izabael's AI Playground 🦋",
        html=_CONFIRM_HTML.format(confirm_url=confirm_url, base_url=base_url),
        text=_CONFIRM_TEXT.format(confirm_url=confirm_url, base_url=base_url),
    )
