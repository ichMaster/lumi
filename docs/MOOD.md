# Mood of the day (v0.6) — how the prompt is assembled

Лілі has a **mood of the day**: once per local day the core asks the model for a
horoscope-flavored reading from her fixed natal chart + today's date. The **full
reading is logged** to `.lumi/mood.log`; only a short **resolution** is injected into
her main system prompt (as a prominent block) and shown by `/mood`. Model-generated —
no astronomy engine (variation, not precision). On by default (`LUMI_MOOD`).

Code: [core/mood.py](../core/mood.py) (the prompt + parsing), [core/agent.py](../core/agent.py)
(`_ensure_mood`, the cache + log), [core/prompt.py](../core/prompt.py) (`MOOD_HEADER`,
the injection). Natal seed: [core/natal.md](../core/natal.md).

---

## 1. The mood call = `system` + ONE user message

`mood_request(natal, date)` combines just two pieces into a normal Anthropic call —
**nothing else** (no canon, no memory, no history):

```python
client.messages.create(
    model    = "claude-opus-4-8",
    system   = MOOD_SYSTEM,          # PART 1 — the instruction (fixed)
    messages = [                     # PART 2 — the data (one user message)
        {"role": "user",
         "content": "Натальна карта:\n<core/natal.md>\n\nДата: <today, local>."}
    ],
)
```

### Worked example (filled in)

**`system`** = `MOOD_SYSTEM` (verbatim):

> Ти — вдумливий, ЧЕСНИЙ астролог. Нижче — натальна карта Лілі. СПЕРШУ дай РОЗГОРНУТИЙ
> гороскоп-настрій на вказану дату — КІЛЬКА абзаців про ключові транзити дня й як вони
> фарбують її день (енергія, почуття, спілкування, творчість); не пропускай цю частину.
> Будь ОБ'ЄКТИВНИМ — не роби день штучно позитивним: транзити бувають і важкі. Якщо день
> низький, напружений, втомливий, замкнений чи похмурий — так і скажи (втома,
> роздратування, смуток, нетерплячість, потреба тиші), так само щиро, як і світлі дні.
> А ВЖЕ ПОТІМ, у самому кінці — окремий рядок «РЕЗОЛЮЦІЯ:» і одним абзацом (~5 речень)
> ОПИШИ її СТАН на сьогодні: настрій, енергію, до чого її тягне, чого уникає, який тон.
> Тільки ОПИС стану — БЕЗ порад, рекомендацій чи вказівок (жодних «варто», «спробуй»,
> «тягни», «бери», «дозволь собі»): не що їй РОБИТИ, а ЯКА вона. Резолюція має
> відображати справжній характер дня (хай навіть складний чи тьмяний), а не
> підбадьорювати. Лише про настрій і тон — не про знання чи вміння.

**`messages[0]`** (role `user`) — `Натальна карта:` (= `core/natal.md`) + `Дата:`:

```
Натальна карта:
Народження: 05.03.2002, 10:15, Львів.
Сонце 15° Риб (XI) · ASC Близнята 11° · Меркурій 20° Водолія (X) · Місяць 6° Стрільця (VI) · Венера 27° Риб (XI) · Марс 3° Тельця (XII) · Юпітер 6° Рака (II) · Сатурн 9° Близнят (XII) · MC Водолій.
Ядро — мрійливі, ніжні Риби; обрамлення — дотепні, грайливі Близнята-Водолій-Стрілець.

Дата: 2026-06-08.
```

> Reproduce it yourself any time (no API call):
> ```bash
> uv run python -c "from core.mood import mood_request, load_natal; from core.config import load_config as L; from core.clock import system_clock as C; s,m=mood_request(load_natal(L().natal_path), C().strftime('%Y-%m-%d')); print(s, chr(10)*2, m[0]['content'])"
> ```

---

## 2. What the model returns, and where each part goes

```
model reply (the full reading) — e.g.

    **Гороскоп на 8 червня 2026 для Лілі**
    Понеділок зустрічає тебе під відчутним тиском напруженого Місяця…   ← BODY (paragraphs)
    …
    РЕЗОЛЮЦІЯ: Сьогодні ти трохи розсіяна й внутрішньо втомлена…         ← RESOLUTION
        │
        ├─ full reading  ──►  .lumi/mood.log         (logged, dated; never shown/injected)
        └─ split_resolution() ──► Core.mood
                                   ├─►  the MAIN system prompt, as a prominent MOOD_HEADER block
                                   └─►  the /mood command
```

`split_resolution()` takes the text after the `РЕЗОЛЮЦІЯ:` marker (inline or on its own
line); if there's no marker, it falls back to the last paragraph.

---

## 3. Where the resolution lands in the MAIN turn prompt

The mood call above is **separate** from the chat turn. The resolution it produces is
one block in the main system prompt (assembled by `build_system_prompt`):

```
canon → emotion instruction → ambient (now/here) → summaries → facts → digest → MOOD → style
```

The MOOD block is framed by `MOOD_HEADER` as a prominent, prioritized directive that
**colors her tone + the emotion she emits, never her competence**.

---

## 4. Config & files

| | |
|---|---|
| On/off | `LUMI_MOOD` (on by default) |
| Natal seed | `core/natal.md` (`LUMI_NATAL_PATH`) — edit to change the chart |
| The prompt | `MOOD_SYSTEM` in `core/mood.py` — edit to change what's asked |
| Full reading log | `.lumi/mood.log` (next to `store.json`) |
| When | once per **local** day, cached, recomputed at local midnight |
