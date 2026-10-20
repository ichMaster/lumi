#!/usr/bin/env bash
# v2.8 (LUMI-239) — the container smoke: the image starts the server with a test config, answers /v1/health as
# dev with this checkout's VERSION, and a `docker stop` (SIGTERM, like `docker compose stop`) exits cleanly.
# No paid call: a dummy model key — the server never calls the model to start. Usage: scripts/container_smoke.sh IMAGE
set -euo pipefail
image="${1:?usage: scripts/container_smoke.sh IMAGE}"
name="lumi-smoke-$$"
port=18765
data="$(mktemp -d)"
chmod 777 "$data"                                      # the image runs as uid 1000, the runner as someone else
version="$(cat "$(dirname "$0")/../VERSION")"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$data"' EXIT

docker run -d --init --name "$name" \
  -e LUMI_ENV=dev -e LUMI_SERVER_TOKEN=smoke -e LUMI_SERVER_HOST=0.0.0.0 -e LUMI_SERVER_PORT=8765 \
  -e ANTHROPIC_API_KEY=dummy -e TZ=Europe/Kyiv \
  -v "$data:/data" -p "127.0.0.1:$port:8765" "$image" >/dev/null

body=""
for _ in $(seq 1 60); do
  if body="$(curl -fsS "http://127.0.0.1:$port/v1/health" 2>/dev/null)"; then break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$name")" != "true" ]; then break; fi
  sleep 2
done
if ! python3 -c 'import json, sys; d = json.loads(sys.argv[1]); assert d == {"ok": True, "env": "dev", "version": sys.argv[2]}, d' \
    "${body:-null}" "$version"; then
  echo "smoke: /v1/health did not answer as dev $version (got: ${body:-nothing})" >&2
  docker logs "$name" >&2 || true
  exit 1
fi
echo "smoke: /v1/health → $body"

docker stop -t 120 "$name" >/dev/null
code="$(docker inspect -f '{{.State.ExitCode}}' "$name")"
if [ "$code" != "0" ]; then
  echo "smoke: the server exited $code on SIGTERM, not 0 (her session close must finish)" >&2
  docker logs "$name" >&2 || true
  exit 1
fi
echo "smoke: a stop exits 0 — the session close ran"
