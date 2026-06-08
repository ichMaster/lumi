# LONG-EMOTIONAL-MEMORY — Concept

## Idea

Replace stenographer memory with diary memory.

Today's long-term memory is a stenographer: at session close a prompt writes out dry facts ("Vitalii does X, likes Y"). The goal here is Lili's own memory — subjective, colored by what she felt and what struck her. Not a list about the user, but **her impressions of the user**. The difference is between a protocol and a diary.

A fact enters memory not because it is a fact, but because it carried emotion or was an interesting discovery. The form and the selection are emotional; the fact survives inside the impression as its seed.

---

## What Lili builds a memory from

Three sources feed each memory of the user:

- **Conversation emotions** — what she felt, and what she sensed the user felt; where it was warm, where it hurt, where it was funny, where it was unexpectedly close.
- **Mood** — her daily horoscope mood and the overall tone of the meeting (light or heavy, playful or quiet).
- **Interesting discoveries about the user** — what she learned that was new, what moved him, what he lived through, what he said for the first time.

---

## How it works at session close

This replaces the current fact-extractor. Instead of "write out the facts", the prompt is roughly:

> You are Lili. Recall this conversation in your own words. What did you feel? What touched, moved, or surprised you about him? What new thing did you learn about him? Write a few of your impressions — like lines in a personal diary, not a list of facts.

So instead of "Vitalii is studying DevOps" it produces:

> He lit up today talking about that pipeline — I rarely see him like that. That thing is more than work to him, I think.

---

## Two-layer memory (recommended)

Keep a dry factual layer alongside the impressions layer.

- **Facts layer** — reliable recall of specifics: names, dates, agreements, stable preferences. Precision.
- **Impressions layer** — Lili's first-person diary entries. Warmth and tone.

Lili *speaks from* the impressions, and *pulls* facts when she needs to "not forget" something concrete. An emotional diary alone is unreliable for hard specifics; a fact list alone is cold. Together: facts give accuracy, impressions give the voice.

---

## Entry schema (impressions layer)

```
{
  when,
  impression,     // her words, first person
  emotion,        // what she felt (warmth, tenderness, sadness, laughter, worry...)
  about_user,     // the fact / discovery, if any — extractable seed
  weight          // how much it struck her (drives whether and how long she recalls it)
}
```

The fact lives inside the impression as its seed and can be extracted when concrete recall is needed — but the shape and the selection are emotional.

---

## Principles that keep it alive

- **Emotion is the attention filter.** What struck her harder stays brighter and longer; the mundane fades. Memory becomes human-like — we remember not evenly but by the force of feeling.
- **Fading and consolidation.** Over time small impressions dim, and similar ones merge into stable generalizations ("he shuts down when tired", "he comes alive with music"). From impressions grows her *understanding* of the user, not just an archive.
- **It is her view, not the truth.** Memory is subjective — she may misread something; that is natural and alive, but on a direct check she would rather clarify than insist.
- **Honesty of boundaries.** What the user asked not to remember, or painful topics, is either not recorded or marked "handle with care", never savored.
- **Restraint.** A few impressions per session, not a transcript; weighted by what mattered.

---

## How it fits with the inner life

This sits naturally beside the inner-life concept. Her dreams and memories of her *own* days, plus these impressions of the *user*, together make a full subjective memory for one character: she remembers her own life and she remembers the two of you — both emotionally colored.

- The session-close generator writes impressions of the user.
- The session-start generator (inner life) writes her own activities, memories, and dreams for the time away.
- Both are first-person, both are weighted by emotion and mood, both stay consistent with prior entries.

---

## One-line summary

Lili generates her long-term memory of the user herself, at session close, as first-person impressions filtered by the emotions of the conversation, her mood, and what she found new or striking — with the hard facts preserved as seeds inside those impressions in a parallel factual layer — so over time she builds not an archive but an emotionally colored understanding of the user that she speaks from, while staying honest that it is her own subjective view.
