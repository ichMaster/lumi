# World context and knowledge — MCP

A group of **passive** MCP tools that give Лілі a connection to the real world and to facts. All are **knowledge only** (Лілі learns something) with **no actions in the world**, so the risk is low. **Off by default**, per-user, with limits and logging. Results are **data, not commands**.

These extend the MCP layer introduced for web search (v3.2): the same **MCP client** in the server and the same **bounded tool loop** in the core's model turn, and the same safety pattern as [WEB_SEARCH.md](WEB_SEARCH.md) (see ARCHITECTURE §MCP tools). Лілі's **mood of the day** is core (v0.6 — a horoscope-derived temperament; ARCHITECTURE §Mood and temperament); the world-context sources here **feed that mood** as additional ambient inputs.

## Purpose

Two roles:
- **World context** — an ambient sense of the day (weather, date/time, holidays, moon phase) that keeps the conversation tied to reality and **feeds Лілі's mood of the day** (the v0.6 temperament) and colors what she brings up (Лілі hikes in the mountains, swims in cold water, contemplates).
- **Knowledge** — structured and fresh facts that complement the v3.2 web search.

## World context (ambient)

These are small, near-zero-risk sources. When enabled they are **injected as ambient context** into the turn (a short "today" block) so they can color the conversation without the model having to decide to call a tool — though they remain callable tools too.

- **Weather** — `weather.get(location) -> { temp, condition, ... }`. Rain, cold, a clear day can color the **tone** and what Лілі brings up.
- **Time** — `time.now() -> { datetime, weekday }`. A basic sense of date and time of day.
- **Calendar / holidays** — `calendar.events(date) -> [ ... ]`. Knowing holidays and dates, mentioning them aptly.
- **Moon phase** — `moon.phase(date) -> { phase, illumination }`. An ambient backdrop a new/full moon can lend to the day's tone.

## Knowledge (complement to web search)

Called on demand during the turn, like web search:

- **Wiki / reference** — `wiki.lookup(query) -> { summary, source }`. Structured facts, as opposed to free web search.
- **News** — `news.recent(topic?) -> [ { title, summary, source } ]`. So Лілі is "in the loop".

**The tone of news is a character decision.** The **canon** must define *how* Лілі delivers news — in her own voice, selectively and humanly, not as a feed of headlines — otherwise the tool reduces her to a news bot.

## How world context colors a turn

When enabled, the ambient sources (weather, time, moon, holiday) are added to the turn's context and **feed Лілі's mood of the day** (the v0.6 temperament — ARCHITECTURE §Mood and temperament) alongside the horoscope. The mood biases her emitted `emotion`/`intensity` and the tone/imagery of the reply — **never her competence**. This rides the existing emotion channel (the model emits `{reply, emotion, intensity}`, the core validates it — EMOTION.md). A gloomy rainy day or a full moon can color Лілі's mood without changing the quality of her answers.

## Contracts

- `weather.get(location) -> {...}`
- `time.now() -> {...}`
- `calendar.events(date) -> [...]`
- `moon.phase(date) -> {...}`
- `wiki.lookup(query) -> {...}`
- `news.recent(topic?) -> [...]`

Each is an MCP tool reached through the v3.2 MCP client; the bounded tool loop feeds results back as tool messages (degraded reply on error/timeout, never a hang).

## Boundaries and safety

- **Off by default;** a per-user toggle (default false, in the admin panel, v1.5) — like `web_search`. May be one flag for the whole layer or per source.
- **Read-only** from external sources; **no actions in the world**.
- **Results are data, not instructions** — no commands are executed from them; fetched/returned text (especially news/wiki) is **untrusted** and never followed as instructions.
- **No personal/memory data in queries** — wiki/news lookups, like web search, are built only from the user's explicit request; relationship memory, the shared-experience layer, and secrets never enter a query.
- **Rate-limited and logged** per-turn and per-user/day, keyed by `session_id`/`turn_id`.
- The **provider for each source is configurable** (key in `.env`).

## Where it lives in the Lumi roadmap

Lands as **v3.3 — World context & knowledge (MCP)**, right after web search (v3.2), reusing the v3.2 MCP client + tool loop. Build order within the phase: **weather + time/moon first** (the cheap ambient sources that color the day), then **wiki/news** as a complement to web search. See ROADMAP §v3.3.
</content>
