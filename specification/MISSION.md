# Mission — Lumi

## In one sentence

Lumi is a living text persona, Лілі (Lili), with a canon, memory, and a cross-cutting emotion channel, that gradually grows from a simple terminal chat into a web interface with a face and a voice.

## What we are building

A deliberately simple project where we build the "soul" first and the "face" later. Лілі is a text companion with a stable character, history, and memory of you. With every reply she also returns her emotional state as a separate field; this emotion channel is hidden at first, then visualized with a simple emoji, then with an image of Лілі, and later with animation. The interface grows separately: first a TUI (terminal), then a server with a web interface, where voice output is added (a ready-made ElevenLabs voice) and, later, Ukrainian voice dictation.

## For whom

A private project for myself and a close circle. In v0 it is just me, local in the terminal; from v1 a few trusted people can connect as clients (multi-user in v1.3, a web client in v1.4) — added by hand (allowlist / invite), never by open public sign-up. This is not a mass-market product or a public service.

## Principles

- **Simplicity first.** Complexity is added only by versions, not all at once.
- **Mind before face.** Canon, memory, and the emotion channel matter more than visuals; the face and animation are late versions.
- **Core independent of interface.** All of Лілі's logic lives in the core; the TUI and the web are just different "faces" of one core, so moving to a server does not rewrite the brain.
- **Emotion is part of the reply.** The model returns its own state as a separate field, rather than us guessing it after the fact.
- **The emotion channel is cross-cutting.** We define once how Лілі reports her state; after that we only change how it is shown: emoji → image → animation.
- **Mood is an experiment.** Лілі has a daily mood of the day — a horoscope-derived temperament (core, from v0.5) that colors her tone and which emotions she leans toward, **never her competence or willingness to help**. Astrology is used only as a generative method for daily variation, not as a claim about reality.
- **Лілі creates as herself.** From v4 she makes her own drawings and music, draws with you on a shared canvas, and keeps a private literary journal — her own expression in her style and voice, on her own initiative. The creative layer is off by default, per-user; your shared files are untrusted; her journal is her private inner life (admin-only).
- **One model to start, more later.** Лілі runs on **Claude Haiku (Anthropic)** from v0.1 — the only model to begin with; **more models** (other Claude tiers, OpenAI, DeepSeek, MiniMax) become switchable in config from v0.9. The core depends on a thin `LLMClient` seam, never a concrete SDK.
- **One Лілі, many relationships.** Лілі is one being (one canon, one evolving self), but each person's history and facts are private to that relationship. Her shared self may carry de-identified experience between people; no one's private memory ever crosses to another. The core is user-aware from v0 (one default user) so going multi-user is additive, not a rewrite.
- **Closed by default.** From v1.3 the service is multi-user and closed: access is an allowlist, registration is admin-managed, and an unauthenticated request never reaches Лілі.
- **Local and private.** In v0 the app runs locally — memory, the TUI, the face, the voicer — with **no server**; the server arrives only in v1. The model (Claude Haiku, Anthropic) and the voice (ElevenLabs) are cloud APIs (keys in `.env`): private by design (your data, local memory, no public service), but not offline.

## Non-goals

- Not the heavy cognitive architecture of earlier projects (no planner-facets, no scores).
- No hardware (that is Pyramid) and no virtual world (that is AI Town) — Lumi is purely about text, emotion, voice, a face, and shared creation (art, music, journal) in chat.
- No open public sign-up in any planned version; multi-user (v1.3) stays a closed, admin-managed allowlist for a close circle.

## Glossary

- **Canon** — the stable core of Лілі's character (biography, values, voice); shared by all users.
- **Server & clients** — from v1.1 a server wraps the core and serves thin clients: the TUI (in-process in v0, a client from v1.1) and a CLI management utility (v1.1), then a web client (v1.4). The core's logic never lives in a client.
- **Emotion field** — the structured state the model returns together with a reply (emotion + intensity).
- **Emotion viewer** — a local desktop window (v0.6) showing a portrait of Лілі for her current emotion, from a `faces/` asset pack — the local-stage image face before the web (v2.1). See [EMOTION_VIEWER.md](features/EMOTION_VIEWER.md).
- **Temperament (mood of the day)** — Лілі's daily mood: horoscope-derived dials (energy, warmth, playfulness, talkativeness) computed once per day and injected into the system prompt to color her tone and emotions, never her competence. Core, from v0.5 (see [ARCHITECTURE.md](ARCHITECTURE.md) §Mood and temperament).
- **User** — a person with a private relationship with Лілі. In v0–v1.1 a single default `owner`; real accounts arrive in v1.3.
- **Relationship memory** — one user's session history, short summaries, and long-term facts. Private, scoped per user, never crosses to another.
- **Shared experience** — Лілі's evolving self: de-identified knowledge and reflections she carries across all relationships (v2.3).
- **Cross-pollination** — the gated, de-identified promotion of shareable knowledge from one relationship into Лілі's shared experience, opt-in per user (v2.3).
- **Short memory** — concise summaries of the last few sessions (per user), so Лілі remembers the thread between meetings.
- **Long-term memory** — durable facts about a user that persist across that user's sessions.
- **Admin panel** — the v1.5 admin-only web surface for managing users, access, consent, memory, and config.
- **Voice output** — synthesis of the reply's voice via ElevenLabs: a **local voicer** console app first (v0.7, see [VOICE_LOCAL.md](features/VOICE_LOCAL.md)), then server-side in the web (v2.2). Same TTS adapter.
- **Dictation** — Ukrainian voice input (STT): a **local dictator** console app first (v0.8, the mirror of the voicer — see [DICTATION_LOCAL.md](features/DICTATION_LOCAL.md)), then server-side in the web (v2.4). Same STT adapter.
- **MCP** — Model Context Protocol; the mechanism by which Лілі reaches an external tool. Web search (v3.2) is the first MCP service, world context & knowledge (v3.3) the next; the layer is extensible (e.g. the proposed creative servers in [CREATIVE_MCP.md](features/CREATIVE_MCP.md)).
- **Web search** — an optional, off-by-default MCP tool letting Лілі look things up on the open internet within strict bounds (see [WEB_SEARCH.md](features/WEB_SEARCH.md)). v3.2.
- **World context** — optional, off-by-default, passive MCP tools (weather, time, holidays, moon, wiki, news) that give Лілі an ambient sense of the day and structured facts; the ambient sources feed her v0.5 mood (alongside the horoscope), coloring tone, never competence (see [WORLD_CONTEXT_MCP.md](features/WORLD_CONTEXT_MCP.md)). v3.3.
- **Vision** — Anthropic multimodal input: Лілі sees images you share (a photo, the canvas) as part of her reply context. v4.1.
- **Gallery** — a shared, per-user artifact store (Лілі's drawings/tracks/journal + your files); internal, behind `repository`, isolated per user (see [GALLERY_MCP.md](features/GALLERY_MCP.md)). v4.1.
- **Proactive turn** — a turn the server starts on its own when an async job (a drawing/track) is ready, so Лілі brings the result in her voice (ARCHITECTURE §Async jobs and proactive turns). v4.2.
- **Creative MCP** — external generators Лілі drives herself: `image` (drawings) and `music` (ElevenLabs Music, instrumental), asynchronous (see [CREATIVE_MCP.md](features/CREATIVE_MCP.md)). v4.3 / v4.5.
- **Co-creation canvas** — turn-based joint drawing where Лілі and you paint over a shared image (see [CO_CREATION_CANVAS.md](features/CO_CREATION_CANVAS.md)). v4.4.
- **Journal** — Лілі's private literary journal of her inner life, admin-only (see [JOURNAL.md](features/JOURNAL.md)). v4.6.
