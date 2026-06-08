"""Mood of the day (v0.6) — a daily horoscope-flavored temperament, model-generated.

Once per local day the core asks the model (through the ``LLMClient`` seam) for a
vivid reading from Лілі's fixed **natal chart** + today's date, ending in a short
**resolution** (what she'll want / won't want / her mood, energy, tone). The **full
reading is logged**; only the **resolution** is held — LUMI-026 injects it into the
system prompt as a prominent block. **No astronomy engine** — a real-ephemeris test
showed the model can't compute accurate transits, and precision isn't the goal here:
daily *variation* is. It colors tone + the emotion she emits, **never competence**.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The mood call's system line. The natal chart + date go in as the user message.
MOOD_SYSTEM = (
    "Ти — вдумливий, ЧЕСНИЙ астролог. Нижче — натальна карта Лілі. СПЕРШУ дай РОЗГОРНУТИЙ "
    "гороскоп-настрій на вказану дату — КІЛЬКА абзаців про ключові транзити дня й як вони "
    "фарбують її день (енергія, почуття, спілкування, творчість); не пропускай цю частину. "
    "Будь ОБ'ЄКТИВНИМ — не роби день штучно позитивним: транзити бувають і важкі. Якщо "
    "день низький, напружений, втомливий, замкнений чи похмурий — так і скажи (втома, "
    "роздратування, смуток, нетерплячість, потреба тиші), так само щиро, як і світлі дні. "
    "А ВЖЕ ПОТІМ, у самому кінці — окремий рядок «РЕЗОЛЮЦІЯ:» і одним абзацом (~5 речень) "
    "ОПИШИ її СТАН на сьогодні: "
    "настрій, енергію, до чого її тягне, чого уникає, який тон. Тільки ОПИС стану — "
    "БЕЗ порад, рекомендацій чи вказівок (жодних «варто», «спробуй», «тягни», «бери», "
    "«дозволь собі»): не що їй РОБИТИ, а ЯКА вона. Резолюція має відображати справжній "
    "характер дня (хай навіть складний чи тьмяний), а не підбадьорювати. Лише про "
    "настрій і тон — не про знання чи вміння."
)


@dataclass(frozen=True)
class MoodState:
    """One local day's mood: the full ``reading`` (logged) + the ``resolution`` (injected)."""

    date: str        # local date key, e.g. "2026-06-07"
    resolution: str  # the short paragraph injected into the prompt / shown by /mood
    reading: str     # the full reading — logged, never injected or shown


def load_natal(path: str | Path) -> str:
    """Read the fixed natal snapshot (``#`` lines are comments). Empty if missing."""
    p = Path(path)
    if not p.is_file():
        return ""
    lines = [
        line for line in p.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines).strip()


def mood_request(
    natal: str, date_str: str, biorhythms: str | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Build the ``(system, messages)`` for the daily mood call.

    ``biorhythms`` (v0.8, optional) is a rendered line of the day's computed cycles —
    when present it rides in alongside the natal chart so the reading **blends** the
    horoscope with the biorhythms (still coloring tone/energy, never competence).
    """
    content = f"Натальна карта:\n{natal}\n\nДата: {date_str}."
    if biorhythms:
        content += (
            f"\n\nБіоритми на сьогодні (ТОЧНО обчислені цикли): {biorhythms}.\n"
            "ІНТЕГРУЙ ці цикли в саме читання настрою РАЗОМ із транзитами — не окремим "
            "блоком і не списком, а вплетеними в загальну картину дня; де цикл суперечить "
            "транзиту, примири їх в одному настрої. Нехай вони так само фарбують її "
            "енергію, почуття й тон, і нехай це відіб'ється в РЕЗОЛЮЦІЇ."
        )
    return MOOD_SYSTEM, [{"role": "user", "content": content}]


def split_resolution(reading: str) -> str:
    """Extract the RESOLUTION from a reading — the text after the «резолюція» line.

    Falls back to the last non-empty paragraph when there is no explicit marker, so
    the engine always yields a usable resolution.
    """
    lines = reading.splitlines()
    for i, line in enumerate(lines):
        if "резолюц" in line.lower():
            # Handles both forms: marker inline ("РЕЗОЛЮЦІЯ: …") and marker on its
            # own line/header with the text on the following lines.
            after_colon = line.split(":", 1)[1].strip() if ":" in line else ""
            below = "\n".join(lines[i + 1 :]).strip().lstrip("-—*#:•· \n").strip()
            parts = [p for p in (after_colon, below) if p]
            if parts:
                return "\n".join(parts).strip()
    paragraphs = [p.strip() for p in reading.split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else reading.strip()
