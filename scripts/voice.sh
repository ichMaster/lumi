#!/usr/bin/env bash
# v1.6.1 — launch the live voice probe: a spoken conversation with Лілі (gpt-realtime-2.1-mini).
# Headphones ON (no echo cancellation). OPENAI_API_KEY comes from .env. Ctrl+C = summary + exit.
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f "python -m tui" >/dev/null 2>&1; then
  echo "⚠ TUI запущений — закрий його перед голосовим сеансом (спільний store)." >&2
  exit 1
fi

echo "🎧 Навушники вдягнені? Говори українською; Ctrl+C — підсумок сеансу."
exec uv run --extra realtime --extra embed python scripts/realtime_probe.py "$@"
