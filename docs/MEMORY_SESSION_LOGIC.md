# How the session memory works (summaries, prompt, flags)

Briefly and to the point: when session summaries are created, how `LUMI_SESSION_DAYS`,
`LUMI_SESSION_DETAIL_N` and `LUMI_SESSION_FORMAT` work, and what happens at session start, at session end
and on every turn.

> **The main idea (in one sentence):** a session summary is created **exactly once, when the session
> ends**; every later turn only **reads** the summaries already saved. The flags **regenerate
> nothing**; they only decide **how many** sessions to add and **in what form**.

---

## 1. When summarizing runs

A session summary is a `ShortSummary{user_id, session_id, summary, gist, ts}` record, where:
- **`summary`** is the detailed summary (long),
- **`gist`** is a one-line "headline" (the line after «СТИСЛО:», "IN SHORT:").

**Both tiers are created by one model call** in `Core.end_session()`. This happens **when the session
ends**:

| Trigger | What it is |
|---|---|
| Exiting the TUI | `Ctrl+Q` / `Ctrl+C` |
| `/new` | a new session; the previous one is saved |
| Abnormal exit | the `on_unmount` fallback path |

- **No summarizing happens during an ordinary chat message.**
- The saved summary goes into `store.json` and **is never regenerated again.**

---

## 2. How `LUMI_SESSION_DAYS` and the two new flags (v0.35) are used

All three act at the **prompt assembly** stage (every turn), **not** at generation.

| Flag | Default | What it means |
|---|--:|---|
| `LUMI_SESSION_DAYS` | 2 | **A window in days**: which closed sessions can reach the `## Останні розмови` ("Recent conversations") block at all (those that closed in the last N days). |
| `LUMI_SESSION_DETAIL_N` | *(unset = all)* | **HOW MANY** of the newest sessions from that window to add: unset → **all**; `0` → **none**; `N` → **the last N**. |
| `LUMI_SESSION_FORMAT` | `summary` | **IN WHAT FORM** to show each added session: `summary` (full) or `gist` (one line). |

**The two flags are independent** (count × form). By default (both unset) you get **all sessions, as the
full `summary`**, which is how it was before v0.35.

### What to set to get what you want

| I want | Set |
|---|---|
| **all sessions as gists** (a short prompt) | `LUMI_SESSION_FORMAT=gist` |
| the last 5, as gists | `LUMI_SESSION_DETAIL_N=5` + `LUMI_SESSION_FORMAT=gist` |
| the last 2, in full | `LUMI_SESSION_DETAIL_N=2` |
| no sessions | `LUMI_SESSION_DETAIL_N=0` |
| as before (no change) | set nothing |

> ⚠️ `LUMI_SESSION_DETAIL_N` is **a number of sessions**, **not** "a number of lines in the gist". Making a
> session "10 lines long" is impossible: a session has exactly **two** forms, the full `summary` and the
> one-line `gist`.

---

## 3. What happens when a session **ends** and **starts**

**Ending a session (`end_session`)** happens once: mark it closed → generate the `summary` + `gist`
(one model call) → extract the facts → **save** to `store.json`.

**Starting a session (`start_session`)**: the new session is **not** summarized. An empty session simply
opens, and its messages go into the "live window".

---

## 4. What runs on **every turn**

**No session summarizing at all.** On each of your messages:

1. **Reading** the saved summaries (`summaries_since`) and assembling the `## Останні розмови` block: it takes
   **the last `LUMI_SESSION_DETAIL_N`** sessions (or all of them), and for each one the `summary` field **or**
   the `gist` field (per `LUMI_SESSION_FORMAT`). This is **a choice among fields that already exist**, with no
   model call.
2. The already saved **daily/weekly** digests are pulled in.
3. **Auto-RAG**: the incoming message is encoded into a vector, and the relevant fragments of the **past**
   are retrieved.
4. **The live window**: the latest messages of the **current** session, verbatim.
5. **One** model call: Лілі's actual reply.
6. At the end, the new message is indexed as a vector (for future RAG).

---

## 5. What else matters

- **Generation vs selection.** Generation (`summary` + `gist`) happens once, at close. Selection (how many,
  which form) happens every turn, with no regeneration and no write to `store.json`.
- **The current session is never "gisted"**: it is always verbatim in the live window. The flags concern only
  **past (closed)** sessions.
- **Details are not lost.** Even when a session is shown as a gist (or dropped from the tier altogether), its
  verbatim messages are reachable through **auto-RAG** (push) or through **`recall`** / **`messages_on`** /
  **`messages_between`** (pull).
- **The daily/weekly digests are a separate story.** Their **format** is controlled by `LUMI_MEMORY_INDEX`
  (v0.34); after changing the format you need **`/regen-summaries`**. *(Not for the session `summary`/`gist`:
  both are already saved.)*
- **The settings (`settings.yaml` + `secrets.yaml`; before v2.9, `.env`) are read ONCE at startup**, so after changing any flag, **restart `./lumi`**.

---

## Example: the first session `[2026-06-21]`

That long session, `[2026-06-21] Розмова почалася з тестування… (RAG)` ("The conversation began with
testing… (RAG)"), has **two ready fields** in `store.json` (both written when it closed):

| Field | Size |
|---|--:|
| `summary` | ~1376 characters (a detailed paragraph) |
| `gist` | 1 line (~91 characters) |

- If `LUMI_SESSION_FORMAT=summary` (the default) **and** this session is among the `LUMI_SESSION_DETAIL_N`
  newest, its **full `summary`** goes into the prompt (that same ~1376-character paragraph).
- If `LUMI_SESSION_FORMAT=gist`, its **`gist`** (1 line) goes into the prompt.
- If it is older than the `LUMI_SESSION_DETAIL_N` newest, it **is not added at all**.

**When:** after a restart of `./lumi`, from the next turn. **How:** the code simply takes the needed,
**already existing** field from `store.json`, with no model call, no "generating N lines" and no write.

> To change **the length of the `summary` itself**, it has to be **regenerated**, and that happens **only**
> when that session closes (`/new` or exit), not because a flag changed.

---

## Frequently asked questions (quick)

- **"Is the summary regenerated when I change a flag?"** → **No. Never.** A different count or form of the
  already saved fields is simply selected.
- **"Are sessions summarized on every message?"** → **No.** They are only **read**.
- **"How do I make all sessions gists?"** → `LUMI_SESSION_FORMAT=gist` (and leave `LUMI_SESSION_DETAIL_N`
  unset, so that all of them are taken), then a restart.
- **"`LUMI_SESSION_DETAIL_N=0`?"** → the `## Останні розмови` block disappears (no sessions).
