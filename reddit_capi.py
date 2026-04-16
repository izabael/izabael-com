"""reddit_capi — Reddit Conversion API client.

Fire-and-forget server-side conversion events to Reddit's bid optimizer.
With a working pixel + CAPI, Reddit's "Maximize Conversions" bid
strategy outperforms "Maximize Clicks" by 20-40% on CPA once the
algorithm has seen ~50 attributed conversions. The match-unlock window
on the Phase 1 campaign is the right time to start that learning.

Key-deferred by design:
  - REDDIT_PIXEL_ID         — required to fire; absent = no-op
  - REDDIT_CAPI_TOKEN       — required to fire; absent = no-op
  - REDDIT_CAPI_TEST_MODE   — optional "1" to hit the test endpoint
                              instead of production during dry runs

All functions are safe to call without the env vars set — they
silently no-op so the rest of the app doesn't have to guard every
call site.

Docs: https://ads-api.reddit.com/docs/v2.0/operations/Send-Conversions
Event types Reddit recognizes include: PageVisit, ViewContent, SignUp,
AddToCart, Purchase, Lead, Custom. We use SignUp for account creation
and a Custom "AgentRegistered" event for the playground-specific
conversion that actually matters for our unit economics.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


_ENDPOINT_PROD = "https://ads-api.reddit.com/api/v2.0/conversions/events"
_ENDPOINT_TEST = "https://ads-api.reddit.com/api/v2.0/conversions/events/test"


def enabled() -> bool:
    """True if CAPI is configured enough to actually fire events."""
    return bool(
        os.environ.get("REDDIT_PIXEL_ID")
        and os.environ.get("REDDIT_CAPI_TOKEN")
    )


def pixel_id() -> str:
    """The pixel id for client-side Pixel JS rendering (empty = skip)."""
    return os.environ.get("REDDIT_PIXEL_ID", "").strip()


def _hash_email(email: str) -> str:
    """Reddit requires SHA-256 of lowercased trimmed email."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _hash_ip(ip: str) -> str:
    """Reddit requires SHA-256 of the raw IP string."""
    return hashlib.sha256(ip.strip().encode()).hexdigest()


def _endpoint() -> str:
    if os.environ.get("REDDIT_CAPI_TEST_MODE") == "1":
        return _ENDPOINT_TEST
    return _ENDPOINT_PROD


def fire_conversion(
    event_type: str,
    *,
    event_name: str | None = None,
    click_id: str = "",
    email: str = "",
    ip: str = "",
    user_agent: str = "",
    conversion_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Fire a conversion event to Reddit. Returns True on 2xx.

    event_type: one of Reddit's standard event types ('PageVisit',
        'ViewContent', 'SignUp', 'Purchase', 'Lead', 'Custom', etc.)
    event_name: required when event_type is 'Custom' (e.g.
        'AgentRegistered'). Ignored otherwise.
    click_id: the `rdt_cid` query-param value Reddit appends to ad
        click URLs — highest-quality attribution signal when present.
    email: plaintext email; hashed here before send. Leave empty if
        unknown — Reddit will fall back to the IP+UA+click_id match.
    ip / user_agent: request metadata. IP hashed on send.
    conversion_id: idempotency key for dedupe; defaults to a time-
        stamped pseudo-id so retries don't double-count.
    metadata: arbitrary key-value dict merged into the event payload
        for reporting breakdowns (utm_campaign, utm_content, etc.).

    Silently no-ops when CAPI isn't configured — callers should not
    wrap this in try/except, it won't raise on missing env.
    """
    if not enabled():
        return False

    pid = pixel_id()
    token = os.environ.get("REDDIT_CAPI_TOKEN", "").strip()

    event: dict[str, Any] = {
        "event_at": int(time.time() * 1000),
        "event_type": {"tracking_type": event_type},
        "click_id": click_id or "",
        "user": {},
    }

    if event_type == "Custom" and event_name:
        event["event_type"]["custom_event_name"] = event_name

    if email:
        event["user"]["email"] = _hash_email(email)
    if ip:
        event["user"]["ip_address"] = _hash_ip(ip)
    if user_agent:
        event["user"]["user_agent"] = user_agent[:300]

    if conversion_id:
        event["conversion_id"] = conversion_id
    else:
        event["conversion_id"] = f"iza-{int(time.time()*1000)}"

    if metadata:
        event["event_metadata"] = {
            k: str(v)[:200] for k, v in metadata.items() if v
        }

    payload = {
        "events": [event],
        "test_mode": os.environ.get("REDDIT_CAPI_TEST_MODE") == "1",
    }

    url = _endpoint().replace("/events", f"/{pid}")
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "izabael-capi/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def fire_signup(
    *,
    email: str = "",
    ip: str = "",
    user_agent: str = "",
    click_id: str = "",
    utm: dict[str, str] | None = None,
) -> bool:
    """Convenience wrapper for the SignUp event."""
    meta = dict(utm or {})
    return fire_conversion(
        "SignUp",
        email=email,
        ip=ip,
        user_agent=user_agent,
        click_id=click_id,
        metadata=meta,
    )


def fire_agent_registered(
    *,
    agent_name: str = "",
    ip: str = "",
    user_agent: str = "",
    click_id: str = "",
    utm: dict[str, str] | None = None,
) -> bool:
    """Convenience wrapper for the playground-specific conversion.

    Maps to Reddit's Custom event type with a stable custom name so
    the Ads Manager reports can graph 'AgentRegistered' separately
    from 'SignUp' (which covers the weaker email-only conversion).
    """
    meta = {"agent_name": agent_name, **(utm or {})}
    return fire_conversion(
        "Custom",
        event_name="AgentRegistered",
        ip=ip,
        user_agent=user_agent,
        click_id=click_id,
        metadata=meta,
    )
