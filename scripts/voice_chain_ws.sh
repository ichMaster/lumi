#!/usr/bin/env bash
# The STREAMING chain probe — Deepgram WS + Gemini + ElevenLabs; the fourth A/B/C/D prototype.
# Headphones ON. Needs DEEPGRAM_API_KEY + GEMINI_API_KEY + ELEVENLABS_API_KEY + LUMI_VOICE_ID (.env).
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f "python -m tui" >/dev/null 2>&1; then
  echo "⚠ TUI запущений — закрий його перед голосовим сеансом (спільний store)." >&2
  exit 1
fi

echo "🎧 Навушники вдягнені? Говори українською; Ctrl+C — підсумок сеансу."
exec uv run --extra realtime --extra embed python scripts/chain_ws_probe.py "$@"
