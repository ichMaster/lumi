# v2.8 (LUMI-238) — one image for every Lumi process on the box: the server (the default command) and both
# Telegram daemons (compose runs `python -m telegram.inbound` / `python -m telegram.outbound`).
# Nothing secret or lived is inside: settings come from the instance's .env (compose `env_file`), her memory is
# the instance's data root, bind-mounted at /data. The matrix-agora pattern (agents/Dockerfile) on the same box.
FROM python:3.13-slim

# ffmpeg: her Telegram voice replies (OGG/Opus); tzdata: her local day — the mood, quiet hours and the scheduler
# run on local time (compose sets TZ).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"

WORKDIR /app
# The lock exactly, with every runtime extra (as ./lumi-server runs today) — the test tools stay out.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-extra dev --no-install-project
COPY . .

# The box's user (uid 1000 owns both data roots) — the bind-mounted /data stays theirs.
RUN useradd --uid 1000 --user-group --create-home lumi && mkdir -p /data && chown 1000:1000 /data

# The build ref (CI: the release tag `vA.B.C`, or `sha-<short>`): the env guard lets prod run only a release
# build; the labels let the deploy agent check what it pulled.
ARG LUMI_BUILD=local
ARG LUMI_REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/ichMaster/lumi" \
      org.opencontainers.image.revision="${LUMI_REVISION}" \
      io.lumi.build="${LUMI_BUILD}"
ENV LUMI_CONTAINER=1 LUMI_BUILD=${LUMI_BUILD} LUMI_HOME=/data

USER 1000:1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('LUMI_SERVER_PORT', '8765') + '/v1/health', timeout=4)"]
CMD ["python", "-m", "server"]
