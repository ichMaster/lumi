"""v1.6.2 LUMI-199 — the talking register: the voice-mode reply config in core.

One mind, two registers: ``register="voice"`` on a reply turn = the voice tier + no think
directive + native thinking forced off — and NOTHING else changes (emotion contract, memory
writes, isolation). Default ``register=None`` is pinned byte-identical. Mock model / stubbed
Gemini transport — no network, no paid calls.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.agent import Core
from core.clock import fixed_clock
from core.config import DEFAULT_MODEL_PROFILES, ModelProfile, _parse_model_profiles
from core.llm import GeminiClient, MockLLMClient
from state.local_store import JsonRepository

_CLK = fixed_clock(datetime(2026, 7, 29, 12, 0, tzinfo=UTC))
_DIRECTIVE = "THINK-DIRECTIVE-MARKER"


def _core(tmp_path, llm=None, **kw):
    repo = JsonRepository(tmp_path / "store.json")
    core = Core(llm=llm or MockLLMClient("ок"), repository=repo,
                canon="Ти — Лілі.", model="reply-tier", clock=_CLK,
                reasoning_directive=_DIRECTIVE, **kw)
    return core, repo


# --- the voice role resolves like the other tiers --------------------------------------------------
def test_default_profiles_carry_a_voice_tier():
    assert DEFAULT_MODEL_PROFILES["gemini"].voice == "gemini-2.5-flash"      # the probes' floor
    assert DEFAULT_MODEL_PROFILES["anthropic"].voice == "claude-sonnet-5"
    assert DEFAULT_MODEL_PROFILES["openai"].voice == "gpt-5.5-mini"


def test_env_profiles_accept_the_optional_fifth_voice_part():
    out = _parse_model_profiles("x=gemini:r,t,m,h,v")
    assert out["x"].voice == "v"
    out = _parse_model_profiles("x=gemini:r,t,m,h")        # the v0.41 four-part shape still parses
    assert out["x"].voice == ""                             # → falls back to think at resolution


def test_models_file_voice_key_is_optional(tmp_path):
    from core.config import _load_models_file

    p = tmp_path / "models.toml"
    p.write_text(
        '[profiles.a]\nprovider="gemini"\nreply="r"\nthink="t"\nmood="m"\nhousekeeping="h"\n'
        'voice="v"\n'
        '[profiles.b]\nprovider="gemini"\nreply="r"\nthink="t"\nmood="m"\nhousekeeping="h"\n',
        encoding="utf-8",
    )
    _, profiles = _load_models_file(p)
    assert profiles["a"].voice == "v"
    assert profiles["b"].voice == ""                        # absent key parses, resolves via think


def test_model_set_applies_the_voice_tier(tmp_path):
    profiles = {"g": ModelProfile("anthropic", "r", "t", "m", "h", "v"),
                "old": ModelProfile("anthropic", "r2", "t2", "m2", "h2")}  # a 5-field-less profile
    core, _ = _core(tmp_path, llm_factory=lambda p, m: MockLLMClient("ок"),
                    model_profiles=profiles)
    core.switch_profile("g")
    assert core._model_voice == "v"
    core.switch_profile("old")
    assert core._model_voice == "t2"                        # voice-less profile → its think tier


# --- the register switch on the reply turn ---------------------------------------------------------
def test_voice_register_uses_voice_tier_and_drops_the_directive(tmp_path):
    llm = MockLLMClient("Привіт!")
    core, repo = _core(tmp_path, llm, model_voice="voice-tier")
    session = core.start_session()
    state = core.reply("привіт", session, register="voice")
    assert llm.calls[-1]["model"] == "voice-tier"           # the talking register's tier answered
    assert _DIRECTIVE not in core.last_prompt["system"]     # no think directive in the prompt
    assert state.reply                                       # the v0.3 gate validated as always
    core.drain_post()
    assert len(repo.load_messages(session.id)) == 2          # memory writes unchanged (one path)


def test_voice_register_falls_back_think_then_model(tmp_path):
    llm = MockLLMClient("ок")
    core, _ = _core(tmp_path, llm, model_think="think-tier")  # no voice tier configured
    session = core.start_session()
    core.reply("привіт", session, register="voice")
    assert llm.calls[-1]["model"] == "think-tier"
    llm2 = MockLLMClient("ок")
    core2, _ = _core(tmp_path, llm2)                          # neither voice nor think
    core2.reply("привіт", core2.start_session(), register="voice")
    assert llm2.calls[-1]["model"] == "reply-tier"


def test_default_register_is_byte_identical(tmp_path):
    llm = MockLLMClient("ок")
    core, _ = _core(tmp_path, llm, model_voice="voice-tier")
    session = core.start_session()
    core.reply("привіт", session)                            # no register — today's turn exactly
    assert llm.calls[-1]["model"] == "reply-tier"            # the reply tier, not voice
    assert _DIRECTIVE in core.last_prompt["system"]          # the directive rides as before


def test_voice_register_offers_no_tools_even_when_enabled(tmp_path):
    # Live regression: a REAL declared tool (recall/date/…) sitting next to the emotion
    # instructions' "заповни... інструмента set_state" wording drove Gemini to hallucinate a
    # native set_state call on almost every voice turn (confirmed live — near-empty replies,
    # silently absorbed by the client's own degrade). The talking register must not offer any
    # tool at all — automatic RAG memory (a prompt injection, not a tool) is unaffected.
    t = _Transport()
    llm = GeminiClient("k", _transport=t)
    core, _ = _core(tmp_path, llm, model_voice="gemini-2.5-flash", date_tool_enabled=True)
    session = core.start_session()

    core.reply("привіт", session, register="voice")
    assert "tools" not in t.bodies[-1]                    # no tool offered on the talking register

    core.reply("привіт ще раз", session)                  # register=None — the same date tool now rides
    assert "tools" in t.bodies[-1]


def test_voice_register_restores_the_thinking_flags(tmp_path):
    llm = MockLLMClient("ок")
    llm._thinking = True                                     # simulate a thinking-on client
    core, _ = _core(tmp_path, llm)
    core.reply("привіт", core.start_session(), register="voice")
    assert llm._thinking is True                             # restored after the call


# --- the Gemini wire: thinking forced off + safety present (the probes' two hard lessons) ----------
_VALID = '{"reply":"привіт","emotion":"calm","intensity":0.5}'


class _Transport:
    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": _VALID}]}}]}


def test_voice_register_forces_gemini_thinking_budget_zero(tmp_path):
    t = _Transport()
    llm = GeminiClient("k", thinking=True, _transport=t)     # thinking ON in text mode
    core, _ = _core(tmp_path, llm, model_voice="gemini-2.5-flash")
    session = core.start_session()
    state = core.reply("привіт", session, register="voice")
    body = t.bodies[-1]
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}  # explicit OFF
    assert body["safetySettings"]                             # the permissive set rides every call
    assert state.reply == "привіт"
    assert llm._thinking is True and llm._thinking_off is False  # both flags restored


def test_default_register_keeps_gemini_thinking_on(tmp_path):
    t = _Transport()
    llm = GeminiClient("k", thinking=True, _transport=t)
    core, _ = _core(tmp_path, llm, model_voice="gemini-2.5-flash")
    core.reply("привіт", core.start_session())               # no register
    tc = t.bodies[-1]["generationConfig"]["thinkingConfig"]
    assert tc.get("includeThoughts") is True                  # text mode: thinking untouched
