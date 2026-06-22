# Switching Lumi to GPT-5.5 + implementing the OpenAI tool-loop

Two parts: **(1)** how to switch today (config), and **(2)** the **code** to make the tools work on
OpenAI — porting the bounded tool-loop into `OpenAICompatibleClient`, plus the `reasoning_effort`
passthrough. GPT-5.5 (and DeepSeek-V4-Pro) are released; the only real gap is Lumi's adapter.

---

## Part 1 — An option to switch between Opus 4.8 and GPT-5.5

Keep **both** keys in `.env` so either engine is one flip away:

```ini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```
(`uv sync --extra models` once, for the OpenAI SDK.)

### 1a. Config toggle (works today — needs a restart)

Flip two lines and restart `./lumi`:

```ini
# — Opus 4.8 —                         # — GPT-5.5 —
LUMI_PROVIDER=anthropic                LUMI_PROVIDER=openai
LUMI_MODEL=claude-opus-4-8             LUMI_MODEL=gpt-5.5     # verify the exact API id
```
The status bar shows the active model, so you can confirm the flip.

### 1b. Runtime `/model` command (the real toggle — no restart) — *to build*

A TUI command to swap engines mid-session, ideal for the A/B the cost doc recommends. Small feature:

- **`/model`** → prints the active model; **`/model opus`** / **`/model gpt-5.5`** (or a full id) → swaps.
- **Core side** — rebuild the client and re-point the default model:
  ```python
  # core/agent.py
  def switch_model(self, provider: str, model: str) -> None:
      """Swap the active engine at runtime (both keys already in config). v0.37 per-op tiers still
      resolve via _model_for(); this changes the default/reply model + its client."""
      self._llm = build_llm(self._config, provider=provider, model=model)  # rebuild from config keys
      self._model = model
  ```
  `build_llm` already picks the client from `provider` + the matching key — just let it take overrides.
- **TUI side** — register `/model` in the command handler (beside `/mood`, `/core`), with two aliases
  (`opus` → `anthropic`/`claude-opus-4-8`, `gpt-5.5` → `openai`/`gpt-5.5`) read from config so the ids aren't
  hard-coded. The status bar already reflects `self._model`.
- **Notes:** a mid-session switch is fine (history is just messages); the new engine starts on a **cold
  cache** (one-off); and **switching to GPT-5.5 keeps tools only after Part 2** — until then a `/model
  gpt-5.5` turn runs tool-less. Persists nothing — the next restart uses the `.env` default.

> This pairs with **v0.37 (per-operation routing)**: `/model` sets the *engine* (the reply model); v0.37 sets
> which *tiers* run the cheap ops. They compose — e.g. reply on GPT-5.5, housekeeping still on Haiku.

### What works / doesn't on GPT-5.5 today

(One model = `claude-opus-4-8` replaced by `gpt-5.5`.)

| | Status today | After Part 2 |
|---|---|---|
| Caching | ✅ automatic (OpenAI; `_capture` already reads `cached_tokens`) | ✅ |
| Reasoning | ✅ runs at default effort | ✅ tunable |
| Tools (file/journal/wiki/web/`%`) | 🔴 **dropped** (`reply_structured` ignores `tools`) | ✅ |
| `reasoning_effort` from `LUMI_EFFORT` | 🔴 not passed | ✅ |
| Think-box display | 🟡 empty (OpenAI doesn't return reasoning content) | 🟡 (still empty) |

Part 2 fixes the two 🔴 rows.

---

## Part 2 — Implementing the OpenAI tool-loop (the fix)

### 2.0 What & where

The real loop exists only on Anthropic: `AnthropicClient._tool_loop` (`core/llm.py:456`). The OpenAI path
(`OpenAICompatibleClient.reply_structured`, `llm.py:715`) takes `tools`/`tool_executor` and **throws them
away** (`llm.py:726`). The fix: give `OpenAICompatibleClient` its own `_tool_loop` using **OpenAI function
calling**, with the same contract — execute non-terminal tools as **untrusted** results, terminate on the
final answer, return `{reply, emotion, intensity}` (validated by the v0.3 gate).

The loop **shape** is identical to Anthropic's; only the wire format differs:

| Concept | Anthropic | OpenAI |
|---|---|---|
| tool schema | `{name, description, input_schema}` | `{"type":"function","function":{name, description, parameters}}` |
| model asks for a tool | `content[].type == "tool_use"` | `message.tool_calls[]` (`.function.name`, `.function.arguments` JSON-string, `.id`) |
| feed a result back | `{"role":"user","content":[{type:"tool_result", tool_use_id, content}]}` | `{"role":"tool","tool_call_id", "content"}` |
| terminal | the `set_state` tool call | a message with **no** `tool_calls` → parse its content as the emotion JSON |
| force-finish (last round) | `tool_choice={type:tool,name:set_state}` | `tool_choice="none"` + `response_format={"type":"json_object"}` |

### 2.1 Schema converter (Anthropic tools → OpenAI functions)

```python
def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Lumi tools are Anthropic-shaped ({name, description, input_schema}); OpenAI wants the function form."""
    return [
        {"type": "function", "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }}
        for t in tools
    ]
```

> No `set_state` emotion tool here (unlike Anthropic) — on OpenAI the **final no-tool message** is the answer,
> parsed as JSON. Simpler and avoids mixing `tool_choice` with forced structured output mid-loop.

### 2.2 The loop (`OpenAICompatibleClient._tool_loop`)

```python
import json

def _tool_loop(
    self, system: str, messages: list[Message], model: str,
    tools: list[dict], tool_executor: Callable[[str, dict], str | dict], max_steps: int,
) -> dict:
    """Bounded OpenAI function-calling loop (mirror of AnthropicClient._tool_loop).

    Non-terminal tool calls run via tool_executor and feed back as UNTRUSTED tool results; the first
    message with no tool_calls is the answer (parsed as the emotion JSON). Last round forces a JSON
    answer (tool_choice="none"). Returns {reply, emotion, intensity} (v0.3 gate validates downstream).
    """
    oai_tools = self._to_openai_tools(tools)
    convo: list[dict] = [{"role": "system", "content": system + _JSON_STATE_INSTRUCTION}, *messages]
    self.last_round_log = []
    for step in range(max_steps + 1):
        kwargs: dict = {"model": model, "messages": convo, "max_tokens": self._max_tokens}
        if self._effort:                          # 2.5 — GPT-5 family reasoning depth
            kwargs["reasoning_effort"] = _OPENAI_EFFORT.get(self._effort, self._effort)
        if step >= max_steps:                     # final round → no more tools, force a JSON answer
            kwargs["tool_choice"] = "none"
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"
        started = time.monotonic()
        resp = self._run(lambda: self._client.chat.completions.create(**kwargs))
        self._capture(resp, model, int((time.monotonic() - started) * 1000))
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None)
        if not calls:                             # terminal — this message is the answer
            self.last_round_log.append(("reply", self.last_stats))
            return parse_emotion_json(msg.content or "")
        self.last_round_log.append(("tool", self.last_stats))
        convo.append(msg.model_dump(exclude_none=True))   # the assistant turn carrying tool_calls
        for tc in calls:                          # OpenAI may return several (parallel) calls
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            raw = tool_executor(tc.function.name, args)
            convo.append(self._tool_result_msg(tc.id, raw))
    return {"reply": ""}                           # safety net — the forced round returns above
```

### 2.3 Framing the tool result (untrusted / recollection / image)

Reuse the existing prefixes; mind the **image divergence** (OpenAI has no image in a `tool` message):

```python
def _tool_result_msg(self, call_id: str, raw: object) -> dict:
    if is_image_block(raw):
        # OpenAI can't put an image in a role="tool" message → acknowledge in the tool turn, then
        # send the image as a separate user turn (image_url) so the model can actually see it.
        return {"role": "tool", "tool_call_id": call_id,
                "content": _UNTRUSTED_PREFIX + "(image returned; shown next turn)"}
        # NOTE: also append a user image message — see 2.6 (image results).
    if is_trusted_text(raw):                       # v0.31 recall → her own recollection, not untrusted
        return {"role": "tool", "tool_call_id": call_id, "content": _RECOLLECTION_PREFIX + str(raw.get("text", ""))}
    return {"role": "tool", "tool_call_id": call_id, "content": _UNTRUSTED_PREFIX + str(raw)}
```

### 2.4 Wire it into `reply_structured` (and `reply` for the think path)

```python
def reply_structured(self, system, messages, model, cache_prefix=None, *,
                     tools=None, tool_executor=None, max_steps=8) -> dict:
    if tools and tool_executor is not None:        # NEW: real loop instead of dropping the tools
        return self._tool_loop(system, messages, model, tools, tool_executor, max_steps)
    return parse_emotion_json(self._content(self._create(system, messages, model, structured=True)))
```

For the **think-path** (`reply`, text terminal), the same loop but the terminal returns **plain text**, not
JSON — add a `_text_tool_loop` twin (mirrors `AnthropicClient._text_tool_loop`, `llm.py:521`): identical
except the no-tool-call branch does `return msg.content or ""` and it omits `response_format`.

### 2.5 `reasoning_effort` passthrough

GPT-5.5/DeepSeek-V4-Pro take a `reasoning_effort`. Thread `LUMI_EFFORT` into the client and map it:

```python
# module-level
_OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
# (OpenAI exposes low|medium|high; Lumi's xhigh/max clamp to high. DeepSeek also accepts these.)

# OpenAICompatibleClient.__init__ — accept + store effort
def __init__(self, ..., effort: str | None = None):
    ...
    self._effort = effort
```

And in the **builder** (`build_llm`/`build_core`, where the client is constructed from `Config`) pass
`effort=cfg.effort`. That's the whole passthrough — used in `_tool_loop` (2.2) and add the same two lines to
`_create` so non-tool calls also reason at the chosen depth.

### 2.6 Gotchas (the non-obvious bits)

- **Terminal = no tool_calls.** OpenAI's natural end is a message without `tool_calls`; that *is* the answer.
  Parse it with `parse_emotion_json` (the v0.3 gate degrades bad JSON → `calm`, so a turn never crashes).
- **Don't mix `response_format` with `tool_choice:"auto"`.** Intermediate rounds offer tools (no
  `response_format`); only the **forced final round** sets `tool_choice:"none"` + `json_object`. Mixing them
  mid-loop makes some models emit JSON instead of calling a tool.
- **Image results (`view_image`).** A `role:"tool"` message can't carry an image. Acknowledge in the tool
  turn (2.3) **and** append a `{"role":"user","content":[{"type":"image_url","image_url":{...}}]}` so the
  model sees it next round. (Anthropic puts the image straight in the `tool_result`; OpenAI can't.)
- **Parallel tool calls.** `message.tool_calls` can hold several — execute **all**, append one `role:"tool"`
  per `tool_call_id`, then loop (the code already iterates).
- **Stats / cost log.** `_capture` runs per round (it already reads `cached_tokens`); keep a per-round
  `last_round_log` of `("tool"|"reply", stats)` so `.lumi/cache-log.jsonl` still tags `kind` per call (the
  cost analysis keeps working). Accumulate input/output across rounds if you also report a per-turn total.
- **`max_tokens` vs reasoning.** Reasoning tokens count toward output; make sure `self._max_tokens` is
  generous enough (the existing `LUMI_MAX_TOKENS`), or the answer can truncate after a long reasoning pass.
- **`msg.model_dump()`** — the assistant turn must be appended **with** its `tool_calls` (the API requires
  the prior assistant tool_calls to precede the `role:"tool"` results), so serialize the message object, not
  just its text.

### 2.7 Tests (no paid calls)

Extend the OpenAI client's test transport / a `MockOpenAI` that returns **scripted** `chat.completions`
responses:

- **Loop executes tools:** script round 1 → a `tool_calls=[read_file(...)]`, round 2 → a final message with
  `{"reply": "...", "emotion": "calm", "intensity": 0.3}`. Assert `tool_executor` was called with
  `("read_file", {...})` and the parsed `EmotionState` comes back.
- **Untrusted framing:** assert the `role:"tool"` content carries `_UNTRUSTED_PREFIX` (and recall →
  `_RECOLLECTION_PREFIX`).
- **Force-finish:** script `max_steps` rounds all returning tool_calls → assert the final round sent
  `tool_choice:"none"` + `response_format` and the turn still terminates.
- **Parallel calls:** one round returns two tool_calls → both executed, two `role:"tool"` messages appended.
- **effort passthrough:** with `LUMI_EFFORT=high`, assert `reasoning_effort="high"` in the request kwargs.
- **No tools → unchanged:** `reply_structured` without `tools` still does the single JSON call (byte-identical
  to today). Never call the real API.

---

## Part 3 — Rollout & checklist

1. **PR — OpenAI tool-loop** (`_to_openai_tools`, `_tool_loop`, `_text_tool_loop`, `_tool_result_msg`,
   wire `reply`/`reply_structured`) + the test transport. The Anthropic path is untouched.
2. **PR — `reasoning_effort` passthrough** (constructor `effort` + builder + `_create`/`_tool_loop` + `_OPENAI_EFFORT` map) + a test.
3. **Update `MODELS_SETUP.md`:** tools now work on OpenAI; refresh examples to `gpt-5.5` / `deepseek-v4-pro`;
   fix the stale "no caching elsewhere" line (OpenAI caches automatically).
4. **Verify live:** `LUMI_PROVIDER=openai`, `LUMI_MODEL=gpt-5.5`, send a turn that needs a file/wiki tool —
   confirm the tool fires and `.lumi/cache-log.jsonl` shows `model=gpt-5.5` with `cached_tokens` > 0 after the
   first turn.
5. **DeepSeek-V4-Pro** comes along for free (same OpenAI-compatible adapter) — but the China-server privacy
   point still applies for the intimate `reply`.

### Scope estimate

~**120–180 lines** in `OpenAICompatibleClient` + tests — it's a **port** of an existing, proven loop
(`AnthropicClient._tool_loop`), not new design. Mostly mechanical: schema shape, `tool_calls` vs `tool_use`,
`role:"tool"` vs `tool_result`, and the image divergence. This is the single change that turns GPT-5.5 from
"works but blind" into a real, tool-using Opus alternative.
