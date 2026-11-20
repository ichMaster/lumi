# mote

A separate standalone project: a colored ASCII world in the terminal in which a small creature lives on its
own. It explores, builds and rests, driven by its needs, and its mind is an Anthropic model (Haiku by
default, Opus optionally) that is asked "what next?" from time to time. Its inner voice is visible in the
thought log. Without an API key a quiet rule-based mind takes over, so the world always works.

This is not part of Clay — it has its own codebase, its own engine, its own repository. Only the idea is
shared: a body on fast ticks and a separate mind behind a clear interface.

## How it works

The body lives on fast ticks on its own — it moves, its needs decay, it carries out its last intent. The
mind is consulted only when something matters, and the answer arrives in a separate thread, so the body does
not freeze for the seconds the model is thinking.

```
body (fast ticks)  ──perception──>  mind (LLM)  ──intent──>  body
   needs decay, movement,           "what next?"             carries out the intent
   intent execution,                + say (voice)            many ticks on its own
   drift when free
```

- **Perception** (body -> mind): the needs (levels + urgency) and the world around (a local patch, a few
  unexplored options, a place to build).
- **Intent** (mind -> body): one intent (`goto` / `build` / `rest` / `wander`) plus `say` — a short line of
  inner voice for the thought log.

The mind is woken only on: a cold start, a substantial intent finished, a need that crossed a threshold while
drifting, or a heartbeat. In between, the body drifts on its own.

## The mind (LLM)

By default `LLMBrain` runs on **Haiku** (`claude-haiku-4-5-20251001`); the `o` key or the `--opus` flag
switches it to **Opus**. It:

- runs in a separate thread (`request` / `poll` / `busy`), so the body is not blocked;
- limits its own call rate (`min_call_interval`, 4 s by default), so that continuous life does not flood the
  API with requests — between calls the body simply drifts;
- on any error or a missing key, quietly falls back to a drift intent.

**Cost.** Each decision is one API call. The real rate is roughly one call every few seconds (less often when
the creature is calm). The levers: `min_call_interval` in `brain.py`, `HEARTBEAT` in `body.py`, `--interval`
(the tick speed).

If `ANTHROPIC_API_KEY` is not set, `make_brain` returns `RuleBrain` (instant, free), and the world lives on
anyway.

## Needs

| need | grounding |
|---|---|
| novelty | reaching unexplored cells |
| creation | placing a block |
| rest | keeping still in place |

## Structure

```
mote/
  world.py        terrain, blocks, "the picture around", unexplored options
  needs.py        drives: decay, replenishment, urgency
  intent.py       the intent + say (the inner voice)
  perception.py   a snapshot of the needs + surroundings; to_prompt_dict() -> the prompt
  brain.py        Brain (the interface), LLMBrain (Haiku/Opus, threaded), RuleBrain
  body.py         life on ticks, carrying out the intent, when to wake the mind
  __init__.py     Simulation + exports
run.py            the curses app: the colored ASCII world + the thought log
run_headless.py   the same without curses (a log of decisions and thoughts, for debugging)
requirements.txt
```

## Running

```
pip install anthropic
export ANTHROPIC_API_KEY=...
python run.py
```

Options:

```
python run.py --opus              # Opus instead of Haiku
python run.py --interval 0.06     # faster
python run.py --width 64 --depth 30
python run.py --rule              # force the free rule-based mind
python run_headless.py --rule     # debugging without graphics
```

Controls: `q` quit, `space` pause, `+/-` speed, `o` Haiku/Opus.

`@` is the creature, `#` are placed blocks, the terrain shades show height, brighter cells are already
visited. On the right: the mind's state, the needs, the current action, the thought log.

## Turning this into a character

The mind is the only seam. Put your own canon into `SYSTEM_PROMPT` (`brain.py`), extend the set of needs, add
personality context — the `Intent` contract (with the `say` field) stays the same, and the body and the
render do not change.

## Natural next steps

- A* with a jump cost instead of the greedy step (uneven terrain).
- Serializing the state (resuming after a restart).
- A two-tier mind: Haiku for the routine "what next", Opus for rare, deeper decisions.
- The creature's memory: a condensed log of the past in the perception, so that the voice has continuity.
