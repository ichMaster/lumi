"""State — repository implementations and local storage (keyed by ``user_id``).

The concrete store behind the core's ``Repository`` interface. Local JSON/SQLite
first (v0.1); a server DB later (v2) — swapping the backend never touches the core.
"""
