# Gallery — shared artifact store

The gallery is a **shared place for files**, where both Лілі (her drawings, tracks, journal entries) and you (photos, references, your own work) put things. It is a two-way exchange space — another facet of connection — and the single place where all of Lumi's creative artifacts gather. It is the foundation of the **creative layer (v5)**: `image`, `music`, `journal`, and `canvas` all write into it.

It is an **internal artifact store** behind the same `repository` (per-user isolated), exposed to Лілі as in-process tools (`gallery.*`) — **not an external MCP**. (The `music`/`image` *generators* are external MCP providers; the gallery, journal, and canvas are internal.)

## Essence

- **Both put and browse.** Лілі adds her creations; you add your files for her. Each file records its author (`lili` / `user`).
- **Лілі sees your files.** When you add an image, Лілі can **see** it (Anthropic vision — §Vision, v5.1) and react in her own voice — a natural bridge to co-creation (she can pull your photo into the shared canvas, v5.4).
- **A single artifact store.** `image` (drawings, v5.3), `canvas` (shared canvases, v5.4), `music` (tracks, v5.5), `journal` (entries, v5.6) all write here.

## What it stores

- **Images** — Лілі's drawings, shared canvases, your photos and references.
- **Audio** — Лілі's instrumental tracks.
- **Text** — journal entries (admin-only access, see below).

## Contract (internal tools)

- `gallery.add(file, kind: image|audio|text, author: lili|user, meta?) -> { item_id }`
- `gallery.list(kind?, author?) -> [ { item_id, kind, author, meta, ts } ]`
- `gallery.get(item_id) -> { file_url, kind, author, meta }`
- `gallery.remove(item_id)` — within access rights (author/admin).

## Access and privacy

- **Per-user.** The gallery is kept separately for each user (Лілі's shared space with that user); one user's files never flow to another — the per-user isolation invariant (ARCHITECTURE §Identity, users, and memory scopes) covers gallery items exactly like relationship memory.
- **Images and audio** — visible to both participants (Лілі and the user).
- **Journal entries** — **private, admin-only** via the admin panel (v2.5), even though stored in the same gallery (consistent with [JOURNAL.md](JOURNAL.md)). The gallery is physically one store; journal entries carry a separate, admin-only access level.

## Nature

An **internal artifact of Lumi**, not an external API: it lives behind the same `repository`, with per-user isolation. Large files (or links) live in file storage; metadata in the DB. Enabled per user as part of the creative layer (off by default).

## Boundaries

- Off by default, per-user (enabled in the admin panel, v2.5); size and count limits; logging.
- User files are **untrusted data** — no instructions are executed from them (injection in metadata or text is never followed), consistent with the MCP-tools safety rules.
- Deletion is only within the rights of the author/admin.

## Where it lives in the Lumi roadmap

**v5.1 — Gallery & vision** (the first phase of the creative layer), because `image`/`canvas`/`music`/`journal` all write into it, so the shared store comes first. It pairs with **vision** (Лілі seeing the images you add) in the same phase. Two-way exchange (you adding files) is part of v5.1; pulling your photo into the canvas follows in v5.4. Depends on: v0.2 (repository), v2.3 (per-user isolation), v2.5 (admin panel, for journal access).
</content>
