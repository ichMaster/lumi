"""`python -m viewer` — launch Лілі's local face window (v0.7).

Reads the emotion signal the core writes (LUMI-028), ensures a placeholder pack exists
(so it runs before real art), and opens the window. The viewer talks to the core only
through the shared signal file.
"""

from __future__ import annotations

from pathlib import Path

from core.config import load_config
from viewer.app import run
from viewer.placeholders import generate_placeholders

FACES_DIR = Path(__file__).resolve().parent / "faces"


def main() -> None:  # pragma: no cover - launches a GUI
    cfg = load_config()
    signal = cfg.face_signal or cfg.store_path.parent / "face.txt"
    if not (FACES_DIR / "calm.png").exists():  # seed placeholders if there's no art yet
        generate_placeholders(FACES_DIR)
    run(signal, FACES_DIR, idle_timeout=cfg.face_idle if cfg.face_idle > 0 else None)


if __name__ == "__main__":
    main()
