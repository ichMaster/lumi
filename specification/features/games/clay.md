# Clay

> A world you sculpt block by block. The earthen brother of Silt — a voxel substrate in which a creature lives.

The starter core: a bot that lives in a voxel world on fast ticks, and a separate stub brain that, when
asked, says what to do next. The request passes the **needs** and **the real picture of the surroundings**.
The brain is swapped for Лілі (Lumi) without any change to the body.

## The main principle

The body lives on fast ticks on its own. The brain is consulted only when something matters. Between calls
to the brain the bot moves, its needs decay, and it finishes its last intent — all of this in plain code. It
is exactly the gap between thoughts that creates the feeling of continuous life rather than of turn-by-turn
play.

```
body (fast ticks)  ──perception──>  brain  ──intent──>  body
   movement, pathfinding,           "what next?"        carries out the intent
   needs decay,                                         hundreds of ticks on its own
   intent execution
```

- **Perception** (body -> brain): the needs (levels + urgency) + a local patch of the world around + the
  nearest unexplored cell + a place to build.
- **Intent** (brain -> body): one high-level intent (`goto` / `build` / `rest` / `wander` / `idle`) with a
  reason. The body carries it out without new requests until it is done.

The brain is woken only on: a cold start, a substantial intent finished, a need that crossed a threshold
while drifting, or a heartbeat that fired. Otherwise the body drifts on its own, without a request. In a
headless run this is ~1 call per 18 ticks.

## Needs

Three generic stub drives. The shape is the same as Лілі's — her six NEEDS will be plugged in:

| here (stub) | Лілі | grounding in the world |
|---|---|---|
| novelty | novelty | visiting new terrain cells |
| creation | creation | placing blocks |
| rest | (bodily vitality) | resting |

Лілі adds solitude, connection, freedom and meaning — a richer world grounds them.

## Structure

```
sim/
  world.py        the voxel world: terrain, blocks, "the picture around"
  needs.py        drives: decay, replenishment, urgency
  intent.py       the intent (brain -> body)
  perception.py   a snapshot of the needs + surroundings (body -> brain); to_prompt_dict() = the seam for the LLM
  brain.py        Brain (the interface) + StubBrain (rules). THIS is what gets swapped for Лілі
  body.py         life on ticks: carrying out the intent, when to wake the brain, pathfinding
  simulation.py   ties world + body + brain together, the step() method
brains/
  llm_brain_template.py   the LLMBrain template — exactly how Лілі is plugged in
run_headless.py   a run in the terminal, works anywhere (no graphics)
game.py           the voxel render on Ursina (run it locally)
```

## Running

The core, with no dependencies:

```
python run_headless.py
python run_headless.py --ticks 4000 --map-every 500
```

The voxel 3D view (locally):

```
pip install ursina
python game.py
```

`@` is the bot, `#` are placed blocks, `.` is explored ground (in the headless map).

## Switching to Лілі

The body does not change. The whole contract is `request(perception)` / `poll()` / `busy`. StubBrain answers
instantly; LLMBrain answers in a separate thread, so the body keeps carrying out its previous intent for the
seconds the model is thinking. Once the answer arrives, `poll()` hands it over, and the body picks it up on
the next tick. No freeze, no rewrite.

```
pip install anthropic
export ANTHROPIC_API_KEY=...
```

```python
from sim import Simulation, World
from brains.llm_brain_template import LLMBrain
sim = Simulation(world=World(), brain=LLMBrain())   # instead of StubBrain
```

To make Лілі out of it: put her canon into SYSTEM_PROMPT, pass the six NEEDS instead of three, add the
personality layers (OPINIONS / SELF_REGARD / RELATIONAL_FEELINGS / INNER_MONOLOGUE) to the context, and
optionally add a `say` field next to the intent. The Intent contract stays the same.

## Tuning levers

| lever | where | effect |
|---|---|---|
| `TICK_INTERVAL` | game.py | the speed of life |
| `HEARTBEAT` | body.py | the maximum pause between requests to the brain |
| needs `decay` | needs.py | how fast the needs build up |
| `ACT_THRESHOLD` | brain.py | how urgent a need has to become before it acts |

Intent granularity vs. request frequency: small single-cell `goto`s produce many requests; for an LLM it is
worth issuing broader intents ("explore that region"), so that one request covers hundreds of ticks.

## Natural next steps

- A* + a jump cost instead of the greedy step (uneven terrain).
- Serializing the state to disk (resuming after a restart, like Silt's snapshots).
- Broader intents for the LLM (fewer requests, longer arcs of behavior).
- A `say` field / inner monologue in the intents, for Лілі's voice.
