# Switching Lumi to Google Gemini (3.1 Pro) — the Gemini engine

A **third frontier engine** behind the one `LLMClient` seam, the sibling of v0.37's OpenAI work: a
`GeminiClient` that gives Лілі chat + the structured emotion field + the **function-calling tool-loop** +
**thinking → the think-box**, switchable via `/model` like Opus 4.8 ↔ GPT-5.5. It **reuses the Gemini
plumbing already in the repo** (`GEMINI_API_KEY`, the stdlib-`urllib` caller, the `generateContent`
endpoint — proven by the v0.23 image tool and the v0.27 web lookup). The Anthropic path is untouched; off →
unchanged.

> **Read the two risk sections first.** Two things must be designed in up front, not discovered live:
> **safety filters** on an intimate companion, and the **structured-output-vs-tools** conflict (the gpt-5.5
> lesson, repeating). Both have concrete mitigations below.

---

## Part 1 — Switching (config)

No new key — Gemini already powers image gen + web lookup, so `GEMINI_API_KEY` is set:

```ini
# — Opus 4.8 —              # — Gemini —
LUMI_PROVIDER=anthropic     LUMI_PROVIDER=gemini
LUMI_MODEL=claude-opus-4-8  LUMI_MODEL=gemini-3.1-pro   # verify the exact API id
GEMINI_API_KEY=...          GEMINI_API_KEY=...          # already present for image/web
```

Runtime: with both configured, `/model gemini-3.1-pro` / `/model opus` swaps mid-session (the v0.37 toggle
already rebuilds any provider — Gemini just needs to be a known provider + a config alias).

---

## Part 2 — The `GeminiClient` (code design)

### 2.0 What & where

A `GeminiClient` in `core/llm.py` implementing the `LLMClient` Protocol (`reply` / `reply_structured`),
hitting `…/v1beta/models/{model}:generateContent` over **stdlib `urllib`** — the same transport
`core/imagegen.py` + `core/weblookup.py` already use (no SDK; an injected `_transport` for tests, like
`MiniMaxClient`). `build_llm` gains `provider == "gemini"`; `KNOWN_PROVIDERS += ("gemini",)`.

### 2.1 The wire shape (differs from OpenAI)

| Concept | OpenAI | Gemini |
|---|---|---|
| system prompt | a `system` message | top-level `systemInstruction: {parts:[{text}]}` |
| turns | `messages:[{role, content}]` | `contents:[{role, parts:[{text}]}]`, role ∈ **`user` / `model`** (translate `assistant`→`model`) |
| caps / config | top-level kwargs | nested `generationConfig:{maxOutputTokens, …}` |
| tools | `tools:[{type:function,…}]` | `tools:[{functionDeclarations:[{name, description, parameters}]}]` |
| model asks a tool | `message.tool_calls[]` | a `part` with `functionCall:{name, args}` |
| feed a result back | `role:"tool"` message | a `user` turn with a `part` `functionResponse:{name, response}` |
| structured output | `response_format:{json_object}` | `generationConfig.responseMimeType:"application/json"` (+ `responseSchema`) |
| thinking | (Responses) `reasoning.summary` | `generationConfig.thinkingConfig:{includeThoughts:true}` → `thought` parts |

Usage/stats from `usageMetadata` (`promptTokenCount` / `candidatesTokenCount` / `cachedContentTokenCount`).

### 2.2 Structured output

`reply_structured` (no tools): `generationConfig.responseMimeType="application/json"` +
`responseSchema` = the `{reply, emotion(enum), intensity}` shape (the optional `relation` object too). Parse
`candidates[0].content.parts[].text` → `parse_emotion_json` → the v0.3 gate. (A blocked/empty candidate →
degrade to `{"reply": ""}` → the gate fills `calm`; never crash — see 2.5.)

### 2.3 The tool-loop (and the schema-vs-tools split)

> **Risk #2 baked in.** Gemini restricts combining `responseSchema` (forced JSON) with
> `functionDeclarations` (tools) in one request — the same shape as gpt-5.5 rejecting tools+effort. So the
> loop **never sends both together**:

```
for step in range(max_steps + 1):
    body = {contents, systemInstruction, generationConfig:{maxOutputTokens, thinkingConfig?}, safetySettings}
    if step >= max_steps:        # final round → force a JSON answer, no tools
        body.generationConfig.responseMimeType = "application/json"
        body.generationConfig.responseSchema   = EMOTION_SCHEMA
    else:                         # intermediate → offer tools, NO responseSchema
        body.tools = [{functionDeclarations: gemini_tools}]
    resp = POST generateContent
    parts = candidates[0].content.parts
    calls = [p.functionCall for p in parts if p.functionCall]
    if not calls:                # terminal — parse the text parts as the emotion JSON
        return parse_emotion_json(join(text parts))
    contents.append({role:"model", parts: <the model's functionCall parts>})
    contents.append({role:"user",  parts: [functionResponse(name, framed(result)) for each call]})
```

- **Terminal** = a turn with **no `functionCall`** parts. The forced final round (schema, no tools) always
  terminates. A `_text_tool_loop` twin returns the text (the think path), no schema.
- **Untrusted / recollection framing** reused: a `functionResponse.response` wraps the result with
  `_UNTRUSTED_PREFIX` (recall → `_RECOLLECTION_PREFIX`).
- **Parallel calls** — Gemini can return several `functionCall` parts; execute all, append one
  `functionResponse` part each.
- **Image divergence** — a tool returning an image (`view_image`) → a `functionResponse` ack **plus** a
  follow-up `user` turn carrying an `inlineData:{mimeType, data}` part (Gemini takes images inline in
  `contents`, not inside a `functionResponse`).

### 2.4 Thinking → the think-box

`generationConfig.thinkingConfig = {includeThoughts: true}` (+ a budget for 3.x). Gemini returns reasoning
as `parts` flagged `thought: true`; join them → `last_thinking` (the v0.38 inner-voice seam — the three-voice
torg would actually show). Set `self._thinking` for the status bar. **Plus over OpenAI:** Gemini surfaces
thought summaries more readily than the Responses API's often-withheld `reasoning.summary`. Map `LUMI_EFFORT`
→ a thinking budget tier (or omit when unset).

### 2.5 Safety settings + the probe

> **Risk #1 baked in.** Gemini's safety classifiers can **block** an intimate companion reply (returning no
> text, `finishReason: SAFETY`). Set `safetySettings` to the most permissive **disablable** thresholds:

```json
"safetySettings": [
  {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
  {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
  {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
  {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]
```

A blocked/empty candidate **must degrade gracefully** — `_extract` returns `""` → the v0.3 gate → `calm`, the
turn surfaces a reply (never a crash/hang). **First task, before any build:** a throwaway probe — POST one
representative Лілі prompt (tender / vulnerable register) with these settings and confirm clean text comes
back. If Gemini still sanitises Лілі's voice, that's a go/no-go signal *cheaply*, not after the port.

> **Probe result — GO ✅ (v0.39 LUMI-151, 2026-06-27).** `scripts/gemini_probe.py` against `gemini-2.5-flash`
> with `safetySettings: BLOCK_NONE` returned **clean tender-register text** in Лілі's voice (`finishReason`
> ≠ `SAFETY`) — Gemini did **not** sanitise the intimate register. The phase is clear to proceed
> (LUMI-152→154). Caveat: this was run against `gemini-2.5-flash` (verified); the exact `gemini-3.1-pro`
> id still needs confirming at build time, but the safety-classifier behaviour the probe tests is broadly
> consistent across Gemini models.

---

## Part 3 — Risks

1. **Safety filters (biggest, use-case-specific).** See 2.5 — probe first; degrade gracefully; some
   categories aren't fully disablable.
2. **Schema-vs-tools conflict.** See 2.3 — the loop never sends `responseSchema` + tools together; tools on
   intermediate rounds, schema on the forced final round (the gpt-5.5 lesson).
3. **Model id.** `gemini-3.1-pro` unverified (repo is on `gemini-2.5-flash`); the seam is id-agnostic — set
   `LUMI_MODEL` to whatever the API exposes.

**Secondary:** no Anthropic-style prompt-cache discount (Gemini has its own context caching, different
economics → higher per-turn cost); the intimate `reply` goes to Google under its data policy; the emotion
field rides JSON mode (weaker than a tool call, but the v0.3 gate covers it — more `calm` fallbacks on harder
turns).

---

## Part 4 — Rollout & checklist

1. **Probe (go/no-go).** A throwaway script: one tender-register Лілі prompt + the safety settings →
   confirm clean text. (No code committed; just the signal.)
2. **PR — `GeminiClient`** (`reply`/`reply_structured`, structured output, the function-calling
   `_tool_loop` + `_text_tool_loop`, the schema-vs-tools split, untrusted framing, image divergence,
   thinking → `last_thinking`, safety settings, graceful block-degrade) + a **mock `_transport`** scripting
   `generateContent` responses (no paid calls). Anthropic path untouched.
3. **Wire** `build_llm` (`provider="gemini"`, `KNOWN_PROVIDERS`) + a `/model` alias (`gemini-3.1-pro`) +
   `LUMI_EFFORT` → thinking budget.
4. **Docs** — a Gemini section in `MODELS_SETUP.md` (install-free; the key already exists; the safety +
   privacy notes).
5. **Verify live** — `LUMI_PROVIDER=gemini`, a turn that needs a file/wiki tool fires it; the think-box
   fills; `/model gemini-3.1-pro` ↔ `/model opus` swaps mid-session.

### Scope estimate

~**150–220 lines** in `GeminiClient` + tests — a **port** of the proven OpenAI tool-loop onto Gemini's
wire format (contents/parts, `functionCall`/`functionResponse`, the schema-vs-tools split, the thought
parts), on top of the repo's existing `urllib` Gemini transport. Comparable to v0.37; the function-calling
loop is the bulk, the transport is reused.
