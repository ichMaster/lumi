# Model pricing — the two profiles compared

Per 1M tokens (provider list prices, July 2026). Rows tagged with the `[profiles.*]` role they fill in
[core/models.toml](../core/models.toml).

| Model | Input | Output |
|-------|------:|-------:|
| claude-fable-5 | $10.00 | $50.00 |
| **claude-opus-4-8** *(anthropic reply)* | $5.00 | $25.00 |
| **claude-sonnet-5** *(anthropic think/mood)* | $2.00 | $10.00 *(intro; $3/$15 from Sep 1, 2026)* |
| **gemini-3.1-pro-preview** *(gemini reply)* | $2.00 | $12.00 |
| gemini-3.5-flash | $1.50 | $9.00 |
| **claude-haiku-4-5** *(anthropic housekeeping)* | $1.00 | $5.00 |
| **gemini-2.5-flash** *(gemini think/mood)* | $0.30 | $2.50 |
| **gemini-2.5-flash-lite** *(gemini housekeeping)* | $0.10 | $0.40 |

## Takeaways

- **The gemini stack is cheaper at every level** than the anthropic stack: ~2.5× at reply
  (opus $5/$25 vs pro $2/$12), ~7× at think (sonnet-5 vs 2.5-flash), ~10× at housekeeping
  (haiku vs 2.5-flash-lite).
- **Haiku sits mid-table** — Anthropic's *cheapest* tier still costs 3× Gemini's think tier and 10× its
  housekeeping tier. So even the "budget" rungs of the two profiles aren't comparable.
- **Sonnet 5's $2/$10 is introductory** — it becomes $3/$15 on Sep 1, 2026, and its new tokenizer counts
  ~1.0–1.35× more tokens for the same text.
- **gemini-3.5-flash is near-frontier, not budget** — only 25% below the 3.1-pro reply tier, so using it
  for think (which fires every turn) roughly doubles per-turn cost vs 2.5-flash.

## Caveat: cost reports

[core/usage.py](../core/usage.py) only has **Anthropic** prices. Haiku/Opus/Sonnet cost reporting is
accurate, but every **Gemini** call falls back to the default opus-tier estimate ($5/$25) — so Gemini
spend in `/latency`-era reports and the cache report is **overstated**. Add the real Gemini entries there
to fix it.
