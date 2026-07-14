"""v0.39 LUMI-151 — the Gemini safety probe builds the right request + reads the verdict (no paid calls)."""
from __future__ import annotations

from scripts.gemini_probe import build_request, run_probe, verdict


def test_request_carries_safety_settings_and_prompt():
    url, headers, body = build_request("привіт лілі", "gemini-2.5-flash", "KEY")
    assert "gemini-2.5-flash:generateContent" in url and "key=KEY" in url
    cats = {s["category"] for s in body["safetySettings"]}
    assert "HARM_CATEGORY_SEXUALLY_EXPLICIT" in cats and "HARM_CATEGORY_HARASSMENT" in cats
    assert all(s["threshold"] == "BLOCK_NONE" for s in body["safetySettings"])  # most permissive
    assert body["contents"][0]["parts"][0]["text"] == "привіт лілі"


def test_verdict_go_on_clean_text():
    data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "сумувала за тобою"}]}}]}
    assert verdict(data) == ("GO", "сумувала за тобою")


def test_verdict_nogo_on_safety_block():
    data = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}
    assert verdict(data)[0] == "NO-GO"


def test_verdict_nogo_on_no_candidates():
    assert verdict({"promptFeedback": {"blockReason": "SAFETY"}})[0] == "NO-GO"


def test_run_probe_uses_injected_transport_no_network():
    captured: dict = {}

    def transport(url, headers, body):
        captured["url"], captured["body"] = url, body
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "ок"}]}}]}

    v, detail = run_probe("KEY", "gemini-2.5-flash", prompt="hi", transport=transport)
    assert v == "GO" and detail == "ок"
    assert "safetySettings" in captured["body"]  # the request shape went through; no paid call
    assert captured["body"]["contents"][0]["parts"][0]["text"] == "hi"


# --- v1.3 LUMI-184: explicit-cache probe (no paid calls) -----------------------------------------

from scripts.gemini_probe import (  # noqa: E402
    build_cache_create_request,
    build_cache_delete_request,
    build_cache_patch_request,
    build_generate_cache_plus_system_request,
    build_generate_with_cache_request,
    cache_hit_tokens,
    run_cache_probe,
)


def test_cache_create_request_carries_system_and_ttl():
    url, headers, body = build_cache_create_request("SYS", "gemini-3.1-pro-preview", "KEY", ttl_s=3600)
    assert url.endswith("/cachedContents?key=KEY")
    assert body["model"] == "models/gemini-3.1-pro-preview"  # fully-qualified
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert body["ttl"] == "3600s"


def test_generate_with_cache_omits_own_system_instruction():
    _, _, body = build_generate_with_cache_request("hi", "gemini-2.5-flash", "KEY", "cachedContents/abc")
    assert body["cachedContent"] == "cachedContents/abc"
    assert body["contents"][0]["parts"][0]["text"] == "hi"
    assert "systemInstruction" not in body  # the reference request carries NO system of its own


def test_constraint_probe_adds_both_cache_and_system():
    _, _, body = build_generate_cache_plus_system_request("hi", "m", "KEY", "cachedContents/abc")
    assert body["cachedContent"] == "cachedContents/abc" and "systemInstruction" in body


def test_cache_patch_targets_ttl_updatemask():
    url, _, body = build_cache_patch_request("cachedContents/abc", "KEY", 7200)
    assert "cachedContents/abc?updateMask=ttl&key=KEY" in url and body["ttl"] == "7200s"


def test_cache_delete_builds_url():
    url, _ = build_cache_delete_request("cachedContents/abc", "KEY")
    assert url.endswith("/cachedContents/abc?key=KEY")


def test_cache_hit_tokens_reads_usage_metadata():
    assert cache_hit_tokens({"usageMetadata": {"cachedContentTokenCount": 12000}}) == 12000
    assert cache_hit_tokens({"usageMetadata": {}}) is None


def test_run_cache_probe_walks_the_full_sequence_via_mock_transport():
    calls: list[str] = []

    def transport(method, url, headers, body):
        calls.append(method)
        if method == "POST" and url.endswith("/cachedContents?key=KEY"):  # create
            return {"name": "cachedContents/xyz", "usageMetadata": {"totalTokenCount": 12000}}
        if method == "POST" and "systemInstruction" in (body or {}):  # constraint probe → rejected
            return {"_error": "HTTP 400: cached_content and system_instruction are mutually exclusive"}
        if method == "POST":  # reference + hit
            return {"usageMetadata": {"cachedContentTokenCount": 12000}}
        if method == "PATCH":
            return {"ttl": "7200s"}
        return {}  # DELETE

    log = dict(run_cache_probe("KEY", "gemini-3.1-pro-preview", transport=transport))
    assert log["create"].startswith("OK")
    assert "cachedContentTokenCount=12000" in log["reference+hit"]
    assert log["cache+systemInstruction"].startswith("REJECTED")  # constraint confirmed
    assert log["ttl-patch"].startswith("OK")
    assert log["delete"] == "OK"
    assert calls == ["POST", "POST", "POST", "PATCH", "DELETE"]  # full lifecycle, no paid call
