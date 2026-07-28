#!/usr/bin/env bash
# The HYBRID voice probe — OpenAI Realtime brain (text out) + ElevenLabs voice; the A/B/C third.
# Headphones ON. Needs OPENAI_API_KEY + ELEVENLABS_API_KEY + LUMI_VOICE_ID (.env).
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f "python -m tui" >/dev/null 2>&1; then
  echo "⚠ TUI запущений — закрий його перед голосовим сеансом (спільний store)." >&2
  exit 1
fi

echo "🎧 Навушники вдягнені? Говори українською; Ctrl+C — підсумок сеансу."
exec uv run --extra realtime --extra embed python scripts/hybrid_probe.py "$@"
