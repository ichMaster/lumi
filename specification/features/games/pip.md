# pip

A chat tamagotchi. A small creature lives in your terminal: it has needs (hunger, energy, mood) that decay in
real time, it speaks up on its own about how it feels, and you both talk with it and take care of it — all in
one chat. The voice is an Anthropic model (Haiku); without an API key simple templated lines take over, and
it lives on anyway.

This is the world simulations (mote/Clay) reduced to their very essence: **the world is removed; what is left
is the body + the needs + the LLM as the voice and for small decisions.** It is the same two-clock principle:
the body's needs tick on their own (pure math, always), and the mind (the LLM) is asked only when there is
something to say.

## Features

- **Time really passes.** Step away, and you come back to a hungry pip. Hunger, energy and mood decay with
  real time, even while you are not typing.
- **It talks about its needs on its own.** When a need drops low, pip speaks up unprompted: "...i'm getting
  really hungry".
- **Chat and care together.** Plain text is conversation (pip answers in character, mentioning how it
  feels). Commands are care.
- **Its mood reacts to you.** Kindness and play lift the mood; when pip is ignored, the mood quietly slides
  down. The LLM returns not only a line but also a small mood shift.

## Chat commands

| command | action |
|---|---|
| `/feed` | feed it (raises fullness) |
| `/play` | play with it (raises mood, tires it out) |
| `/sleep` | let it sleep (restores energy) |
| `/status` | show the needs |
| `/help` | the list of commands |
| `/quit` | quit |

Any other text is just conversation.

## Running

```
pip install anthropic
export ANTHROPIC_API_KEY=...
python pip.py
```

Options:

```
python pip.py --name moss      # a different name
python pip.py --speed 2        # the needs decay twice as fast
python pip.py --rule           # no API, only the templated voice
```

Example:

```
  ╭───────╮   pip
  │ o   o │   hunger ######----
  │  ---  │   energy #######---
  ╰───────╯   mood   ######----
  pip: hi! i'm so glad you're here.

  you> i missed you today
  pip: i missed you too. though i'm getting a little hungry...
  ╭───────╮   pip
  │ O   O │   hunger ##--------
  │   o   │   energy #######---
  ╰───────╯   mood   #######---

  you> /feed
  pip: mmm, thank you. that's better.
  ╭───────╮   pip
  │ ^   ^ │   hunger #########-
  │  \_/  │   energy #######---
  ╰───────╯   mood   ########--
```

The face's expression and the frame's color change with the state: hungry (O_O), tired (-_-), sad (;_;),
happy (^_^).

## How it works

- **Body (the fast tick):** three needs decay with real time. Pure math.
- **Care (you):** `/feed` / `/play` / `/sleep` change the numbers.
- **Voice + decisions (the LLM):** to your line or action, pip answers in character, mentioning its state,
  and returns a small mood shift. The spontaneous reminders about needs are templated (instant and free), so
  that the background voice costs nothing.

Without `ANTHROPIC_API_KEY` or without the `anthropic` package, it quietly falls back to the templated voice.

Input uses `select` on stdin — that is Unix (macOS/Linux). One file, with no external dependencies besides the
optional `anthropic`.

## Where next

- Memory: pip remembers previous conversations across sessions (save the state + the log to disk).
- A two-tier voice: templated for routine reactions, the LLM for real conversation.
- Your own canon: put your own personality into the SYSTEM prompt.
- States that last: sickness from neglect, sleep as a separate mode with time passing.
