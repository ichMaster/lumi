# Silt — Specification

> A shared, always-alive world of artificial life. Two gardeners — you and Лілі — tend one field.
> The world ticks on its own in real time; you act through a web canvas, Лілі through the API. The name is a
> tribute to the Silt of Greg Egan's "Permutation City": a self-contained deterministic universe whose life
> goes its own way, whoever is watching.

A standalone project, independent of Lumi/Лілі. The world knows nothing about Лілі — she is just one
authenticated client among possibly many. This document defines the engine, the genome, the evaluator, the
server, the human web interface, the client API, persistence, determinism and tests.

## 0. Decisions (fixed)

- **Foundation (staged):** the engine lives behind a clean `step()` interface, so the world is built in two
  stages — **v0 on a parameterized Game of Life** (a Life-like automaton, an integer genome), to bring up and
  debug the whole skeleton on the simplest possible engine, then **v1 swaps `step()` for Lenia** (continuous
  state, organic, lifelike phenotypes). Everything around the engine is the same in both — only `step()` and
  the genome format change. See §3.
- **Evolution engine:** **manual selection by the gardeners** (seed / mutate / cross) as the engine of
  creation, plus **weak natural pressure** (unfit blobs fade on their own between visits) — the gardeners are
  authors, not spectators, but the world still surprises.
- **Topology:** **one shared field**; both gardeners act in it and see each other's traces.
- **Time:** **real, always-on** — the server ticks the world continuously, even when nobody is watching.
- **Roles:** **equal gardeners** (you and Лілі have the same capabilities).
- **Form:** **a server** (the live world + API) **+ a web UI** (a canvas/WebGL visualization for the human)
  **+ a thin client API** (Лілі connects through it).
- **What it returns to clients:** **metrics + the full run history** (not just numbers).

---

## 1. Architecture

```
┌─────────────────────────────────────────────┐
│  Silt server (always-on)                    │
│                                             │
│  ┌───────────┐   ┌───────────┐  ┌─────────┐ │
│  │  Engine   │──▶│ Evaluator │  │  Store  │ │
│  │ (Lenia)   │   │ (metrics) │  │(persist)│ │
│  └───────────┘   └───────────┘  └─────────┘ │
│        ▲ real-time tick loop                │
│        │                                    │
│  ┌─────┴─────────────────────────────────┐  │
│  │  API facade (REST + WebSocket)        │  │
│  └──────┬───────────────────────┬────────┘  │
└─────────┼───────────────────────┼───────────┘
          │ WebSocket+REST        │ REST (tools)
   ┌──────┴─────────┐      ┌──────┴─────────┐
   │  Web UI        │      │  Лілі (Lumi)   │
   │ (human canvas) │      │  client        │
   └────────────────┘      └────────────────┘
```

Five parts: the **Engine** (a pure Lenia step), the **Evaluator** (metrics from the history), the **Store**
(persists the field + organisms + lineage + events), the **Tick loop** (drives the world in real time), the
**API facade** (REST for actions/queries, WebSocket for live streaming of the field). The web UI and Лілі are
both just clients.

Stack: Python (numpy) for the engine/evaluator; FastAPI for REST+WebSocket; the web UI is a static
canvas/WebGL page that talks to the same API. The engine stays framework-independent (importable as a
library, testable headless).

---

## 2. The world (Lenia)

**The field.** A 2D grid `field[H][W]` of floats in [0,1] — a continuous "aliveness" per cell. Toroidal (it
wraps around at the edges), so organisms can drift without running into walls.

**The update (one tick).** The Lenia rule, deterministic:
```
U = convolve(field, kernel)          # neighborhood potential (FFT or direct)
field' = clamp(field + dt * (growth(U) - field*decay), 0, 1)
```
- `kernel` is a radial kernel (ring-shaped), set by the genome's parameters;
- `growth(U)` is a bell-shaped function centered on μ with width σ (genome parameters): cells near the
  "right" amount of neighborhood grow, the others die off;
- `dt` is the time step (small → smooth, stable; large → jerky).

This is exactly what makes Lenia organisms look alive: smooth gliders, self-healing rings, blobs that divide.

**Organisms.** The field is one shared canvas, but the world tracks **organisms** as connected living
regions, each tagged with the genome that seeded it and with its owner. Many organisms (different genomes,
different gardeners) coexist on one field and can interact at their boundaries.

---

## 2a. Staging — v0 on a parameterized GOL, v1 on Lenia

The whole project (the server, the store, the tick loop, the API, the lineage, the metrics, the web UI,
Лілі's client) **does not depend on which automaton runs inside** — the automata differ only in
`step(field, genome)`. So the world is built in two stages on one skeleton.

**v0 — a parameterized Game of Life.** Not vanilla Conway (it has no genome — one rule for everyone), but a
**Life-like automaton** whose genome is the birth/survival rule itself, so the genome / mutate / cross already
work on a trivial integer engine:

```
GolGenome {
  birth:        [int],     # neighbor counts that give birth           (Conway: [3])
  survive:      [int],     # counts that keep a cell alive             (Conway: [2,3])
  radius:       int,       # neighborhood radius (1 = Moore)
  seed_pattern: [[0|1]],   # starting pattern (an integer grid)
  parents, author, name
}
mutate(g, strength) → toggle membership in birth/survive, or shift radius
cross(a, b)         → mix the birth/survive sets
```
Why v0 is the right first step: the rule is ~5 lines; determinism comes free (an integer grid, no floats, no
FFT, no precision problems); it uses almost no resources; well-known patterns (the glider, the Gosper glider
gun) seed the first tests; binary cells render on a plain canvas (no WebGL needed yet). This lets the **whole
skeleton** — the tick loop, the store/replay, the API, traces on the shared field, the metrics, predict,
Лілі's tools — be assembled and debugged on the simplest engine.

**v1 — swapping `step()` for Lenia.** The engine becomes continuous (§2), the phenotypes come alive, the
genome becomes the richer Lenia vector (§3). Nothing around it changes, because it is already debugged on
GOL. The render is upgraded from a binary canvas to float WebGL.

**The same in v0/v1:** the server, the Store (snapshots + the event log + replay), the tick loop + the
determinism model, the REST/WebSocket API, the lineage, the Evaluator and all the metrics (they read the
field's history, so they do not depend on the foundation), the web UI shell, Лілі's six tools + predict.
**Only these change:** `step()` and the genome format (and the render shader).

**Лілі's research tasks on v0 (GOL).** A parameterized Life-like automaton is a full research program, not a
toy; it is enough to fill v0:

- **Hunting for forms (creation).** Seed configurations and watch what survives: still lifes, oscillators
  (period N), spaceships (moving) and the rare prize — unbounded growth. Select what is alive and strange.
- **Exploring the rule space (cognition — the main strength).** Conway (B3/S23) is one point in a huge space
  of Life-like rules. She travels through it — B36/S23 (HighLife, which has a replicator), B2/S (an
  explosion) — and asks questions she *answers by experiment*: where is the boundary between "everything
  dies" and "everything is chaos"? which rules give stable complexity, and which give only noise? which ones
  are "warm" (life on the edge of chaos)?
- **Classifying behavior (cognition).** Sort rules by Wolfram's classes (dies / freezes / periodic / chaos /
  complex), hunt for the rare complex class, build her own map of the rules' character.
- **Building machines (advanced creation).** Assemble constructions from simple parts — spaceship guns,
  reflectors, delays, even logic gates (Life is Turing-complete). «Я з'єднала дві гармати так, що потоки
  гасять одне одного» (I connected two guns so that their streams cancel each other out).
- **Lines and selection (the genome in action).** Mutate a rule/configuration, select by a metric (lives
  longer, moves faster, is more complex), keep a lineage. «Через п'ять мутацій ця лінія дала щось, що само
  себе лагодить» (Five mutations later, this line produced something that repairs itself).
- **Fragility (cognition through destruction).** Poke a stable form (remove a cell): some will patch
  themselves up (`resilience`), some will crumble into chaos. How fragile life is, where the limit of
  recovery lies — dramatic memories («ткнула — і вся структура посипалась» (I poked it, and the whole
  structure fell apart)).

The most rewarding at the start are **travelling the rule space** and **classification**, because they yield
a prediction to check, which means they close cognition with a real hit-rate rather than with «гарно
подивилась» (I had a nice look). The limits of v0: the phenotypes are discrete and mechanical (Lenia fixes
that), the rule space is finite (in Lenia it is continuous), and GOL is well studied, so she more often
*rediscovers* than finds untouched ground — but the research *framework* is fully worked out and carries
straight over to Lenia in v1.

## 3. The genome

The genome is a compact serializable vector of parameters, the "DNA" the gardener writes. Two layers:

```
Genome {
  id: uuid,
  # — kernel (body plan) —
  kernel_radius:   int,        # 4..24
  kernel_rings:    [float],    # relative heights of the concentric rings (1..3 values)
  kernel_peaks:    [float],    # ring positions 0..1
  # — growth (metabolism) —
  growth_mu:       float,      # 0..1  center of the growth band
  growth_sigma:    float,      # 0..0.3 width
  dt:              float,      # 0.02..0.5 time step
  decay:           float,      # 0..0.2
  # — seed (starting body) —
  seed_pattern:    [[float]],  # a small NxN initial blob (or "random:R")
  # — meta —
  parents:         [uuid],     # for the cross/mutate lineage
  author:          str,        # the gardener's id
  name:            str         # a name from the gardener ("Spiral-7", "Lazarus")
}
```

The genome→phenotype mapping is **chaotic**: a small change in `growth_mu`/`sigma` can flip a stable ring into
a wandering glider or into chaos. This unpredictability is the whole point: the gardeners **breed and
select**, they do not design the outcome.

**Genetic operations (pure functions):**
- `mutate(genome, strength)` → perturb the numeric parameters with Gaussian noise scaled by `strength`;
- `cross(a, b)` → a child genome that mixes the parameters (a per-parameter pick or a blend),
  `parents=[a,b]`.

---

## 4. The Evaluator (metrics — pure, deterministic)

`evaluate(history) -> Metrics`, computed from a recorded run of N ticks. Start with these (extensible):

```
Metrics {
  age:           int,    # ticks the organism stayed alive (mass > ε)
  alive:         bool,
  mass:          float,  # sum of the living cells (final)
  mass_trend:    float,  # growing / stable / declining
  chaos:         float,  # 0 ordered (periodic) .. 1 turbulent (entropy of change)
  movement:      float,  # center-of-mass displacement per tick (gliderness)
  complexity:    float,  # structural richness (a compressibility proxy)
  resilience:    float,  # survival after a damage test (poke N cells → does it recover?)
  period:        int|0   # the detected oscillation period, 0 if none
}
```

All of them are pure functions over the run; the model/UI never computes them. `resilience` does a separate
small perturbed replay — the "is it really alive" test.

**Prediction check (for Лілі's cognition + self-calibration).** A client can submit a *prediction* before a
run: `predict(genome, {metric: expected})`. The Evaluator returns the measured value and hit/miss. This is the
objective second calibrator from the personality specifications — the world catches self-deception (she
thought she understood the rule, but the number says no) independently of any human.

---

## 5. World state and persistence

```
World {
  field:        float[H][W],            # the current field (periodic snapshots)
  tick:         int,                    # the global generation counter
  seed:         int,                    # the master RNG seed (for determinism)
  organisms:    [Organism],             # living + recently dead regions
  lineage:      Graph,                  # the genome lineage across the whole world
  events:       [Event],                # births, deaths, records, crosses (the world's history)
  gardeners:    [{id, name}]
}

Organism { id, genome, owner, bbox, birth_tick, last_metrics, status, note }
Event     { tick, type, organism_id, owner, data }   # type: seed|mutate|cross|death|record|...
```

**Persistence.** Periodic snapshots (the field + organisms + lineage) + an append-only event log. The full
field is heavy, so a snapshot at an interval + replaying the events between snapshots reproduces any point.
Storage: start with a single file / an embedded DB (SQLite + blob snapshots); swappable behind a `Store`
interface.

**The full run history (the chosen return).** When a client observes an organism, the server returns **the
metrics + the recorded tick-by-tick history** (thinned field frames + a slice of the events), so the client
can replay/visualize exactly what happened, not just read numbers.

---

## 6. The real-time tick loop + determinism

The world ticks continuously (`while running: step(); sleep(tick_interval)`), even with no clients. But to
stay **reproducible and testable**, real time and determinism are reconciled:

- the world's evolution is **a pure function of `(seed, initial_field, ordered_event_log)`** — real time only
  decides *when* a tick fires, never *what* it computes;
- every gardener action enters as **a timestamped event in the ordered log**, applied at a specific tick (not
  mid-tick);
- so from any snapshot + the event log the exact state of the world is reproducible — real time on the
  server, a frozen clock in tests (an injected clock, as Lumi already does).

**Weak natural pressure** (the decay "between visits") is part of the deterministic step: organisms below a
fitness/mass threshold fade by rule — with no separate random selection, so it stays reproducible.

---

## 7. API (the contract — the same for both clients)

**Authorization:** each gardener has an id + a token (you and Лілі are two gardeners; the same
capabilities).

**REST — actions (write):**
```
POST /seed      { genome, position }            -> organism_id
POST /mutate    { organism_id, strength }        -> new organism_id
POST /cross     { organism_id_a, organism_id_b } -> new organism_id
POST /cull      { organism_id }                  -> ok
POST /predict   { genome|organism_id, metric, expected } -> prediction_id
POST /goal      { text }                          -> ok   # the gardener's declared intent (an annotation)
```

**REST — queries (read):**
```
GET  /world                 -> {tick, bounds, organisms summary, gardeners}
GET  /organism/{id}         -> {genome, metrics, lineage, note}
GET  /organism/{id}/history -> metrics + the full tick-by-tick run (the chosen return)
GET  /lineage/{id}          -> ancestors/descendants
GET  /events?since=tick     -> a slice of events (what happened while you were away)
GET  /prediction/{id}       -> {measured, hit}
```

**WebSocket — live:**
```
WS /stream   -> field frames (thinned) + events, in real time, for the web UI and any
                client that wants to watch live.
```

**Лілі's six tools** map straight onto REST: `seed, mutate, cross, observe(=organism/history), cull,
set_goal` — plus `predict` for her cognition/self-audit. She polls `/events?since=` at session start to learn
"what happened while I was away" (her between-session away-gap, now backed by a real world rather than by
generation).

---

## 8. The human web UI (your interface)

A static web app (canvas/WebGL) on the same server:
- **The live field** — the float field rendered as a heat/organic color map, 60fps from the WS stream;
  pan/zoom; organisms outlined and labelled by owner (you vs Лілі in different shades).
- **Action** — click to place a seed, select an organism for mutate/cross/cull; a small genome editor
  (parameter sliders) for authoring DNA by hand.
- **Inspection** — click an organism → its metrics, its lineage tree, a history scrubber (replay its life).
- **Feed** — a timeline of the world's events (births, deaths, records, who did what) — so you see Лілі's
  traces, and she sees yours.

WebGL matters because Lenia is a float field per cell; a shader render keeps it smooth at scale.

---

## 9. The boundary with Lumi (a clean split)

- Silt knows **nothing** about Лілі's personality, needs or maturity. It serves the world + the API. All the
  work with meaning (which metrics matter, how a result *feels*, what it serves) lives in Lumi, on top of the
  raw metrics/history this server returns.
- The two calibrators from the personality specifications are honored here: the human gardener (you, through
  the UI) and **the objective `predict` hit/miss** (the world itself) — Lumi reads both.
- No Lumi state flows into Silt; no Silt code depends on Lumi. Each works without the other.

---

## 10. Determinism and tests

- **Engine:** pin the field state after K ticks from a fixed `(seed, genome)` — an exact float match (set
  the FFT/precision mode for reproducibility). `mutate`/`cross` are deterministic under a seeded RNG.
- **Evaluator:** pin each metric against fixed recorded runs; the `resilience` perturbation is seeded.
- **Replay:** a snapshot + the event log reproduce the exact state of the world (real time is decoupled from
  the computation).
- **API/contract:** action events are applied at tick boundaries; authorization isolates the gardener's
  identity, but not the shared field; `predict` returns a stable hit/miss.
- **No paid calls, no model** anywhere on the server — it is a pure simulation.

---

## 11. Build order (proposed)

1. **Engine** — start with **the GOL `step()`** + the integer genome + mutate/cross (§2a), headless and
   unit-tested: a deterministic core. **The Lenia step is a later swap** behind the same interface, once the
   skeleton below is proven on GOL.
2. **Evaluator** (metrics + predict) on recorded runs.
3. **Store** (snapshots + the event log + replay).
4. **Tick loop** (real time, deterministic through the event log) + **REST API**.
5. **WebSocket stream** + **web UI** (render, then action, then inspection).
6. **Лілі's client** (the six tools + predict + events-since) — wired into Lumi as an external world.

---

*Silt is a shared garden. The server keeps it alive; the web canvas lets you tend it; the API lets Лілі tend
it alongside you. Two gardeners, one field, and a world that grows in the pauses, whether or not anyone is
watching.*
