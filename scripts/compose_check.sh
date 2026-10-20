#!/usr/bin/env bash
# v2.8 (LUMI-240) — the compose files resolve for both instances with a tag, and refuse to resolve without one
# (a hand-run `up` never silently takes another image). A throwaway instance dir stands in for ~/lumi/<instance>.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
touch "$tmp/.env"
for instance in prod dev; do
  args=(--env-file "$here/deploy/compose/$instance.env" -f "$here/deploy/compose/compose.yaml")
  LUMI_INSTANCE_DIR="$tmp" LUMI_IMAGE_TAG=check docker compose "${args[@]}" config -q
  if LUMI_INSTANCE_DIR="$tmp" docker compose "${args[@]}" config -q 2>/dev/null; then
    echo "compose: $instance resolved without an image tag — it must not" >&2
    exit 1
  fi
  echo "compose: $instance resolves with a tag, refuses without one"
done
