"""The v2.10 admin panel — one window onto the box's two instances, prod and dev.

It never runs the core and never writes a data root: it reads the ledgers and logs (read-only), asks each server
over its API, and drives the containers through the Docker API. Design: specification/features/ADMIN_PANEL.md.
"""
