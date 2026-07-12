# Inner monologue — Лілі thinks in her own voice before she speaks

Лілі already runs on **Opus 4.8 with extended thinking**, and the client already shows the
think-block in a separate window. So the *mechanism* of "thinking before replying" **exists** —
this spec is not about building it, but about making that thinking **hers** (her inner voice
weighing her own states) instead of the model's generic reasoning, and about the policy around it
(one call, what to show, what not to store, invariants inside think).

This is the **sixth** personality layer and it is **distinct** from the others: inner life (v1.7)
holds thoughts *between* sessions; emotional memory (v1.10) holds thoughts *after* a session; this
is thinking *in the moment* — the hidden step inside the reply turn. It is the place where all the
existing states converge into a decision *how to speak*. Scheduled at **v1.9** (right after the
inner-life states it weighs exist); it adds **no new engine** — it shapes the thinking that already
happens.

> The point: today the think-window most likely shows Opus's technical reasoning ("the user asks X,
> I'll cover Y"). That is the model's voice, not Лілі's. The inner monologue is when that hidden
> step sounds like *her*: "he's asking about the deploy, but his voice is tired — don't pile on
> detail, ask how he is first."

## Architecture decision: ONE call, not two

A reply to your message is **one model call** with thinking enabled — not a separate "think" call
followed by a "generate" call.

- **One HTTP call** per message; **one context** (system prompt + history + state blocks).
- Inside the single response: a `thinking` block (her inner monologue) **then**
  `{reply, emotion, intensity}` (+ optional `relational_feeling`, RELATIONAL_FEELINGS.md).
- The separate window is **not** a separate call — it is the `thinking` content block of the same
  response, which the client renders apart from the `text`.
- **Do not** use the two-step prompt (think-call → reply-call). That is the manual emulation for
  models without native thinking — here it would only double cost/latency and force storing an
  intermediate note. We have native thinking, so it is unnecessary.
- **Housekeeping stays separate and thinking-OFF** (mood, inner life, summary, consolidation) —
  exactly as today. The inner monologue is the **one place thinking is ON**: the real-time reply
  turn. (Contrast with the deterministic, mocked housekeeping calls.)

## Make the thinking *hers* (the only real work)

Two additions to the existing reply turn:

1. **A think-phase instruction, in her voice** (in the system prompt): *before answering, think as
   Лілі — what is he really asking; what is under the words; how am I right now (mood, needs, how
   close we are); how would I, specifically, say this.* This turns generic reasoning into her inner
   voice.
2. **The state blocks in context**, so the monologue is concrete, not abstract: the already-built
   **mood** (v0.6/0.8), **closeness** (v0.10), **needs** (v1.5–v1.8), and — when they exist — any
   later **self-regard** / **relational feeling** layers. The think-phase becomes the **convergence
   point** where these inputs are weighed into how she speaks — it consumes them, it does not
   duplicate them.

That is the whole feature: an authored think-phase instruction + giving think access to the states
that already exist. No new store, no new loop.

## Show / log / memory policy

- **Show:** a product choice of three modes — **debug** (you see it, the user does not; safe
  default), **open thoughts** (shown as her inner voice — intimate, but then it MUST stay in
  character, never raw technical reasoning), or **log-always / show-optional**.
- **Log:** log the think-block for transparency and debugging (the v0.3 logged tier), never audio.
- **Do NOT write think into long-term memory by default.** Thoughts are ephemeral, like a person's.
  What persists is the **digested impression** via emotional memory (v1.10), not the raw monologue.
  (This is also why two calls are unnecessary — nothing needs the think to outlive the turn.)

## Invariants apply *inside* the thinking too

Hidden does not mean unconstrained. The think-block is **not** a place to work around the rules:
**never competence**, **honesty about her nature**, **anti-dependency**, and the
provocation / retreat-before-pain invariant all hold inside the monologue exactly as in the reply.
This matters doubly if thoughts are ever shown to the user.

## Contract & tests

- **No contract change.** The reply still returns `{reply, emotion, intensity}` (+ optional
  `relational_feeling`); `thinking` is a content block of the same response, not a new field. The
  emotion-channel contract test passes verbatim.
- **One-call invariant (test):** the reply turn issues exactly one model call; assert no second
  "generation" call is made; housekeeping calls stay thinking-off.
- **Voice test:** with the think-phase instruction, the think-block references her states
  (mood/closeness/needs) rather than generic task analysis (asserted against a mocked thinking
  response).
- **Memory test:** the raw think-block is **not** persisted to long-term memory; only impressions
  are (v1.10).
- **Determinism:** the model is mocked in tests; thinking content is asserted structurally, not by
  exact wording.

## Mapping to the roadmap

**v1.9 — Inner monologue (think-phase in her voice)**, once the states it weighs exist:
**v0.6/0.8** (mood), **v0.10** (closeness), **v1.5–v1.8** (needs + plans), plus later **self-regard**
and **relational feelings** as additive inputs when they land. The mechanism is already present
(Opus 4.8 thinking); this layer is the **authored instruction + state access + show/log/memory
policy + invariants-inside-think**. The in-the-moment sibling of inner life (between sessions) and
emotional memory (after a session).
