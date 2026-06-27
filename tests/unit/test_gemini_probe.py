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
