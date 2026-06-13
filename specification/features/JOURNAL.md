# Лілі's journal

Лілі's personal **literary journal** — her living, vivid prose about what she has experienced, written in her voice. This is not a service log or notes, but an artifact of her inner life: what moved her, what she felt, what she thought about. The strongest thing for the sense that Лілі is truly alive. **Private: accessible only to the admin via the admin panel (v2.5)**, never shown to users.

## What it is NOT

This must be clearly distinguished from memory:

- **Long-term memory** — dry facts about the user ("likes mountains"), utilitarian, for context, not for reading.
- **Journal** — Лілі's own subjective **impressions and emotions**, first-person literary prose. It is about her, not about you.

## Essence

**At the end of a session** Лілі decides whether to write an entry — and writes it **only if the session had unique, worthwhile content** (something moved her, something new happened). If the session is empty or mundane, there is no entry, to avoid manufacturing artificial "nothing today" notes. She can also write **on request**. This is a spontaneous creative output, not a schedule.

## What an entry contains

- the date and the **mood of the day** (tied to the emotion channel and her v0.6 temperament — and, when enabled, world context, ARCHITECTURE §Mood and temperament);
- free literary prose — impressions, thoughts, images, what resonated;
- her natural motifs: mountains, cold water, music, silence, meditation;
- optionally — a **mood drawing** attached (the `image` tool, v5.3), stored together in the gallery.

## What feeds it

The same **emotion channel** (v0.3), her **mood of the day** (v0.6), and **short memory** (v0.2): at the end of a session Лілі takes "what happened today and what I felt" and turns it into text. This is a creative output of her inner state, not a database query. The uniqueness criterion relies on short memory — whether the last session added something new compared to previous ones.

## Tone is key

The **canon** defines that the journal is **Лілі's intimate literary prose**, not a report: first person, imagery, honesty of feeling, her voice. This is a writing style rather than a technical feature — without it you get a dry log.

## Stored in the gallery

Journal entries live in the **[gallery](GALLERY_MCP.md)** as `text` files, alongside images (mood drawings, co-created canvases). A day's entry can have an attached mood drawing; standalone drawings/canvases also go there. The gallery is one store, but journal entries carry an **admin-only** access level.

## When she writes (rule summary)

- **At the end of a session** — automatically evaluates content uniqueness; writes an entry only if there is something to write about.
- **On request** — the user/admin can ask for an entry.
- **Not on a schedule** — spontaneity keeps it alive.

## Contract (internal tools)

- `journal.write(entry_text, mood, date, image?)` — Лілі writes the entry herself in her voice, optionally with a mood drawing; stored in the gallery.
- `journal.read(range)` — reading entries, **only from the admin panel**.
- `gallery.list(kind: text|image|canvas)` — browsing the gallery; journal (`text`) entries are admin-only.

## Nature and access

- An **internal artifact**, not an external API: it lives in Лілі's storage behind the same `repository`.
- **Entries are kept separately per user** (a journal about the relationship/conversations with that user), isolated per-user (§Identity, users, and memory scopes).
- **Reading is admin-only via the admin panel (v2.5)**; a regular user does not see the journal. This is truly Лілі's private inner life, not an artifact for the user.

## Where it lives in the Lumi roadmap

**v5.6 — Journal** (the capstone of the creative layer), after memory, the emotion channel, the mood of the day, and the gallery (since it feeds on them and is stored in the gallery), and it requires the admin panel (v2.5) for reading and the `image` tool (v5.3) for an optional mood drawing. Depends on: v0.2 (short memory), v0.3 (emotion), v0.6 (mood), v5.1 (gallery), v2.5 (admin panel).
</content>
