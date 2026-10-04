"""The Lumi server (v2.2) — FastAPI over one core + one session, behind a client token, on localhost.

The core does not change: the server is a thin transport over the same ``reply`` contract and the v2.2
command layer. ``python -m server`` runs it (the ``server`` extra); ``server.app.create_app`` builds the
app around an injected core, so tests drive it with a mock model and FastAPI's ``TestClient``.
"""
