"""Local emotion viewer (v0.7) — a separate desktop window showing Лілі's face.

Another renderer of the locked v0.3 emotion channel (EMOTION.md §5): the core writes
her current emotion to a one-word signal file (LUMI-028); this package resolves it to a
`faces/<emotion>.png` and shows it. Linked to the core only through the signal — no
direct call. `face.py` is the pure, testable resolver/reader; `app.py` (LUMI-030) is the
thin Tkinter shell.
"""
