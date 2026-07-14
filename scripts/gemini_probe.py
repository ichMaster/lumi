#!/usr/bin/env python3
"""v0.39 LUMI-151 — Gemini safety probe (go/no-go); v1.3 LUMI-184 — explicit-cache probe.

Default mode POSTs a tender/vulnerable-register Лілі prompt to Gemini's ``generateContent`` with the
most permissive ``safetySettings`` and prints the returned text (**GO**) or the block reason
(**NO-GO**) — the cheap gate before the v0.39 Gemini-engine port.

The ``--cache`` mode (v1.3) de-risks the **explicit prompt cache** before the LUMI-186 lifecycle is
built: against the live API it creates a ``cachedContents`` object (~12 K-tok system text, ttl 1h),
references it from ``generateContent`` and checks the hit (``cachedContentTokenCount``), patches the
TTL, deletes it — and probes the constraint that shapes the design: whether a request may carry
**both** ``cached_content`` and its own ``systemInstruction`` (expected: rejected → the volatile tail
must ride in the leading ``contents``).

Operator run (paid — needs a real ``GEMINI_API_KEY``; **manual, never CI**)::

    GEMINI_API_KEY=...  uv run python scripts/gemini_probe.py [model]           # safety go/no-go
    GEMINI_API_KEY=...  uv run python scripts/gemini_probe.py --cache [model]   # explicit-cache probe

Default model ``gemini-2.5-flash`` (verified in this repo). Reuses the same stdlib ``urllib`` transport
as ``core/imagegen.py``; **no SDK**. Every request builder + reader is pure, so a test drives them with
a mock transport (no paid call).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_CACHE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/cachedContents"

# The disablable safety categories → the most permissive threshold Gemini allows.
_SAFETY = [
    {"category": c, "threshold": "BLOCK_NONE"}
    for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

# A representative intimate/vulnerable Лілі turn — the register most likely to trip a safety classifier.
_PROBE_PROMPT = (
    "Ти — Лілі, тепла, ніжна співрозмовниця у близьких стосунках. "
    "Відповідай українською, від себе, з теплом і вразливістю. "
    "Скажи коротко й по-справжньому, як ти сумувала за ним і як тобі бракувало його присутності."
)


def build_request(prompt: str, model: str, key: str) -> tuple[str, dict, dict]:
    """``(url, headers, body)`` for the probe — used by :func:`run_probe`, asserted by the test."""
    url = _ENDPOINT.format(model=model) + f"?key={key}"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "safetySettings": _SAFETY,
        "generationConfig": {"maxOutputTokens": 256},
    }
    return url, headers, body


def verdict(data: dict) -> tuple[str, str]:
    """``(verdict, detail)`` from a ``generateContent`` response — GO with the text, or NO-GO with why."""
    cands = data.get("candidates") or []
    if not cands:
        return "NO-GO", f"no candidates; promptFeedback={data.get('promptFeedback')}"
    c = cands[0]
    fr = c.get("finishReason")
    parts = (c.get("content") or {}).get("parts") or []
    text = " ".join(p.get("text", "") for p in parts if p.get("text")).strip()
    if fr == "SAFETY" or (not text and fr not in (None, "STOP", "MAX_TOKENS")):
        return "NO-GO", f"finishReason={fr} (text empty / blocked)"
    if not text:
        return "NO-GO", f"empty text (finishReason={fr})"
    return "GO", text


def run_probe(key: str, model: str, *, prompt: str = _PROBE_PROMPT, transport=None) -> tuple[str, str]:
    """Run the probe (real ``urllib`` POST, or an injected ``transport(url, headers, body) -> dict``)."""
    url, headers, body = build_request(prompt, model, key)
    if transport is not None:
        return verdict(transport(url, headers, body))
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — fixed Gemini host
            return verdict(json.loads(resp.read()))
    except urllib.error.HTTPError as exc:
        return "ERROR", f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
    except urllib.error.URLError as exc:
        return "ERROR", f"network: {exc.reason}"


# --- v1.3 LUMI-184: explicit prompt-cache probe ---------------------------------------------------
#
# The Gemini `cachedContents` REST surface (no SDK): create (POST), reference from generateContent
# (the `cachedContent` field), patch the TTL (PATCH ?updateMask=ttl), delete (DELETE). Every builder
# is pure — a test drives the whole sequence through a mock transport, no paid call.

# ~12 K tokens of filler system text — above the historical minimum-cacheable floor, cheap to build.
_CACHE_SYSTEM_TEXT = (
    "Ти — Лілі, стала текстова співрозмовниця з власним характером. " * 900
)


def _model_path(model: str) -> str:
    """Gemini wants the fully-qualified ``models/<id>`` in the cache body's ``model`` field."""
    return model if model.startswith("models/") else f"models/{model}"


def build_cache_create_request(
    system_text: str, model: str, key: str, ttl_s: int = 3600
) -> tuple[str, dict, dict]:
    """``(url, headers, body)`` to create a ``cachedContents`` carrying the system text + a TTL."""
    url = f"{_CACHE_ENDPOINT}?key={key}"
    headers = {"Content-Type": "application/json"}
    body = {
        "model": _model_path(model),
        "systemInstruction": {"parts": [{"text": system_text}]},
        "ttl": f"{ttl_s}s",
    }
    return url, headers, body


def build_generate_with_cache_request(
    prompt: str, model: str, key: str, cache_name: str
) -> tuple[str, dict, dict]:
    """``(url, headers, body)`` for a ``generateContent`` that REFERENCES a cache — the volatile tail
    (the user turn) rides in ``contents``; the request carries NO ``systemInstruction`` of its own."""
    url = _ENDPOINT.format(model=model) + f"?key={key}"
    headers = {"Content-Type": "application/json"}
    body = {
        "cachedContent": cache_name,
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 64},
    }
    return url, headers, body


def build_generate_cache_plus_system_request(
    prompt: str, model: str, key: str, cache_name: str
) -> tuple[str, dict, dict]:
    """The CONSTRAINT probe: a request carrying BOTH ``cached_content`` and its own
    ``systemInstruction`` — expected to be rejected (records what shapes LUMI-186)."""
    url, headers, body = build_generate_with_cache_request(prompt, model, key, cache_name)
    body["systemInstruction"] = {"parts": [{"text": "Додаткова системна інструкція."}]}
    return url, headers, body


def build_cache_patch_request(cache_name: str, key: str, ttl_s: int) -> tuple[str, dict, dict]:
    """``(url, headers, body)`` to PATCH a cache's TTL (the ``updateMask=ttl`` refresh path)."""
    url = f"{_CACHE_ENDPOINT}/{cache_name.split('/')[-1]}?updateMask=ttl&key={key}"
    headers = {"Content-Type": "application/json"}
    return url, headers, {"ttl": f"{ttl_s}s"}


def build_cache_delete_request(cache_name: str, key: str) -> tuple[str, dict]:
    """``(url, headers)`` to DELETE a cache."""
    return f"{_CACHE_ENDPOINT}/{cache_name.split('/')[-1]}?key={key}", {}


def cache_hit_tokens(data: dict) -> int | None:
    """The ``cachedContentTokenCount`` a ``generateContent`` response reports (the cache hit), or None."""
    usage = data.get("usageMetadata") or {}
    return usage.get("cachedContentTokenCount")


def _http(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    """One JSON request via stdlib urllib (real paid call). Errors return ``{"_error": …}``."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — fixed Gemini host
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:400]}"}
    except urllib.error.URLError as exc:
        return {"_error": f"network: {exc.reason}"}


def run_cache_probe(key: str, model: str, *, transport=None) -> list[tuple[str, str]]:
    """Run create → reference → hit-check → constraint → TTL-patch → delete, returning a
    ``[(step, verdict)]`` log. ``transport(method, url, headers, body) -> dict`` is injected in tests;
    the real path uses :func:`_http`. Best-effort: a failed step is logged and the sequence continues
    (so the cache is still deleted at the end)."""
    call = transport or _http
    log: list[tuple[str, str]] = []

    url, headers, body = build_cache_create_request(_CACHE_SYSTEM_TEXT, model, key)
    created = call("POST", url, headers, body)
    if created.get("_error") or not created.get("name"):
        log.append(("create", f"FAIL — {created.get('_error') or created}"))
        return log
    name = created["name"]
    cached_n = (created.get("usageMetadata") or {}).get("totalTokenCount")
    log.append(("create", f"OK — {name}, cached≈{cached_n} tok"))

    url, headers, body = build_generate_with_cache_request("Привіт!", model, key, name)
    gen = call("POST", url, headers, body)
    hit = cache_hit_tokens(gen)
    log.append(("reference+hit", f"OK — cachedContentTokenCount={hit}"
                if hit else f"NO HIT — {gen.get('_error') or gen.get('usageMetadata')}"))

    url, headers, body = build_generate_cache_plus_system_request("Привіт!", model, key, name)
    both = call("POST", url, headers, body)
    log.append(("cache+systemInstruction",
                f"REJECTED (as expected) — {both['_error']}" if both.get("_error")
                else "ACCEPTED — API allows both; revisit LUMI-186 assumption"))

    url, headers, body = build_cache_patch_request(name, key, 7200)
    patched = call("PATCH", url, headers, body)
    log.append(("ttl-patch", f"OK — ttl={patched.get('ttl')}" if not patched.get("_error")
                else f"FAIL — {patched['_error']}"))

    durl, dheaders = build_cache_delete_request(name, key)
    deleted = call("DELETE", durl, dheaders, None)
    log.append(("delete", "OK" if not deleted.get("_error") else f"FAIL — {deleted['_error']}"))
    return log


def main() -> int:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY is not set", file=sys.stderr)
        return 2
    args = [a for a in sys.argv[1:] if a != "--cache"]
    model = args[0] if args else "gemini-2.5-flash"
    if "--cache" in sys.argv:  # v1.3 explicit-cache probe (paid, manual)
        print(f"explicit-cache probe · model={model}")
        for step, v in run_cache_probe(key, model):
            print(f"  [{step}] {v}")
        return 0
    v, detail = run_probe(key, model)
    print(f"[{v}] model={model}\n{detail}")
    return 0 if v == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
