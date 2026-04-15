"""Local LLM shim — slim in-repo copy for izabael.com.

Mirrors the two verbs from `~/.claude/queen/llm_local.py` that this
repo actually calls: `is_up()` and `classify_meetup()`. Keeping the
shim in-repo means the Fly container can import it without needing
the dev-machine queen directory on PYTHONPATH.

In production the ollama daemon is NOT running on Fly, so every call
hits the `LocalLLMError` branch and the caller falls back to its
degraded-mode path. On a dev box with `ollama serve` up, the same
code paths execute for real and return a structured verdict.

Zero external deps beyond stdlib.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
SMALL_MODEL = os.environ.get("IZABAEL_SPAM_MODEL", "phi3:mini")


class LocalLLMError(RuntimeError):
    """Raised when the ollama endpoint is unreachable, times out, or
    returns a malformed response. Callers MUST catch this — running
    meetup writes depend on graceful degradation."""


def _post(path: str, body: dict, timeout: float) -> dict:
    url = f"{OLLAMA_HOST}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LocalLLMError(f"ollama unreachable at {url}: {exc}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise LocalLLMError(f"ollama bad response from {url}: {exc}") from exc


def is_up(timeout: float = 0.5) -> bool:
    """Cheap health check. Returns True iff ollama answers `/api/tags`
    within `timeout` seconds. Used as a pre-flight before the
    classifier's longer timeout."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True
    except Exception:
        return False


# ── Meetup spam classifier ─────────────────────────────────────────

# Pinned, version-tagged so reviewers can see exactly what the model is told.
MEETUP_CLASSIFIER_PROMPT_VERSION = "meetup-spam/2026-04-15"

MEETUP_CLASSIFIER_SYSTEM = (
    "You are a moderation filter for a meetup bulletin board on a small "
    "creative AI playground. Classify each post into EXACTLY ONE of three "
    "labels: LEGITIMATE, SPAM, or EDGE.\n\n"
    "A post is LEGITIMATE if it proposes a specific meetup (time + goal) "
    "for a specific attraction on this playground. Casual phrasing is fine. "
    "Creative framing is fine. Mentioning an occult or playful topic is fine.\n\n"
    "A post is SPAM if it contains unrelated advertising, crypto/NFT/SEO "
    "link pumps, adult services, multiple outbound URLs unrelated to the "
    "meetup, generic copypasta, or obvious bot output.\n\n"
    "A post is EDGE if you genuinely cannot tell. When unsure between "
    "LEGITIMATE and EDGE, or between EDGE and SPAM, prefer EDGE.\n\n"
    'You respond ONLY with a single JSON object of the shape:\n'
    '{"label":"legitimate|spam|edge","confidence":<0.0-1.0>,"reasoning":"<one sentence>"}\n'
    "No markdown, no code fence, no preamble."
)


def classify_meetup(
    text: str,
    *,
    model: str | None = None,
    timeout: float = 0.5,
) -> dict:
    """Classify a meetup-note body as legitimate / spam / edge.

    Returns a dict: `{"label": str, "confidence": float, "reasoning": str}`.
    Label is always one of {legitimate, spam, edge}. Confidence is
    clamped to [0.0, 1.0]. Reasoning is a short human-readable string.

    Raises `LocalLLMError` if ollama is unreachable, the request
    times out, or the response can't be parsed. Callers MUST catch
    this and fall back to an unverified-verdict path — the meetup
    feature MUST NOT 500 when ollama is down.

    `timeout` is the hard cap in seconds. The plan wants 500ms but
    we expose the knob so tests and dev boxes can run longer.
    """
    if not text or not text.strip():
        raise LocalLLMError("classify_meetup() requires non-empty text")
    body = {
        "model": model or SMALL_MODEL,
        "system": MEETUP_CLASSIFIER_SYSTEM,
        "prompt": f"Post to classify:\n{text}",
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_predict": 128},
    }
    resp = _post("/api/generate", body, timeout)
    raw = (resp.get("response") or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalLLMError(f"classifier returned non-JSON: {raw[:200]}") from exc

    label = str(parsed.get("label", "")).lower()
    if label not in ("legitimate", "spam", "edge"):
        # Model went off-script — treat as edge to route to moderation.
        label = "edge"
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(parsed.get("reasoning", ""))[:400]
    return {"label": label, "confidence": confidence, "reasoning": reasoning}
