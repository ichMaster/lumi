"""v0.33 LUMI-126 — the think-path tool-loop (text terminal, never set_state) + table-driven Directive.

No paid calls: the Anthropic loop is driven by a fake client; the rest via the mock.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.llm import AnthropicClient, MockLLMClient
from core.thoughts import THINK, WONDER, Directive


# --- the table-driven Directive record ------------------------------------------------------------
def test_directive_table_fields_default_tool_less():
    assert THINK.tools == () and WONDER.tools == ()          # v0.12 directives stay tool-less
    d = Directive("x", "muse", tools=("wiki_search",), cap=2, surface="open", instruction_from_topic=True)
    assert d.tools == ("wiki_search",) and d.cap == 2
    assert d.surface == "open" and d.instruction_from_topic is True
    assert THINK.cap == 4 and THINK.surface == "silent" and THINK.instruction_from_topic is False


# --- the mock seam: reply() runs the tool script, then returns the text thought --------------------
def test_mock_reply_runs_the_tool_script_then_returns_text():
    calls = []

    def execu(name, inp):
        calls.append((name, inp))
        return "результат пошуку"

    mock = MockLLMClient("Цікаво…\nЕМОЦІЯ: thoughtful", tool_script=[("wiki_search", {"q": "x"})])
    out = mock.reply(system="s", messages=[], model="m",
                     tools=[{"name": "wiki_search"}], tool_executor=execu, max_steps=4)
    assert calls == [("wiki_search", {"q": "x"})]            # the tool ran in the think loop
    assert out == "Цікаво…\nЕМОЦІЯ: thoughtful"              # then the text thought (no set_state)
    assert mock.tool_calls[0][0] == "wiki_search"


def test_mock_reply_without_tools_is_a_plain_call():
    mock = MockLLMClient("думка\nЕМОЦІЯ: calm", tool_script=[("wiki_search", {})])
    out = mock.reply(system="s", messages=[], model="m")     # no tool_executor → script NOT run
    assert out == "думка\nЕМОЦІЯ: calm" and mock.tool_calls == []


# --- the Anthropic seam: a real text-tool-loop over a fake client ---------------------------------
def test_anthropic_text_tool_loop_feeds_untrusted_then_terminates_on_text():
    tool_use = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="wiki_read", input={"id": "1"})], usage=None)
    final = SimpleNamespace(content=[SimpleNamespace(type="text", text="думка\nЕМОЦІЯ: calm")], usage=None)

    class _M:
        def __init__(self):
            self._q = [tool_use, final]
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            return self._q.pop(0)

    fake = SimpleNamespace(messages=_M())
    client = AnthropicClient("sk-test", _client=fake)
    seen = []

    def execu(name, inp):
        seen.append(name)
        return "екстракт"

    out = client.reply("sys", [], "m", tools=[{"name": "wiki_read"}], tool_executor=execu, max_steps=4)
    assert seen == ["wiki_read"]                              # the tool was executed
    assert out == "думка\nЕМОЦІЯ: calm"                      # terminated on the text thought

    # the tool result was fed back as an untrusted tool_result...
    fed = fake.messages.calls[1]["messages"][-1]["content"][0]
    assert fed["type"] == "tool_result" and "екстракт" in fed["content"]
    # ...and NO set_state tool was ever offered (the thought terminal, not the reply terminal)
    offered = [t["name"] for kw in fake.messages.calls for t in kw.get("tools", [])]
    assert "set_state" not in offered and "wiki_read" in offered
