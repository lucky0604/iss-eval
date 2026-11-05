# syntax=docker/dockerfile:1.7

# EvalScope service image: Flask dashboard + native EvalScope engine + isolated AIPerf 0.13.0 backend.
#
# Build from the repository root (docker compose does this automatically):
#   docker build -f docker/Dockerfile -t evalscope:latest .
#
# Two Python environments are intentional:
#   /usr/local (framework)  -> eval / perf / service extras, Python 3.12
#   /opt/aiperf-venv        -> locked AIPerf 0.13.0 runtime, Python 3.12
# AIPerf pins packages that conflict with the framework environment, so the service
# probes it as an external executable through EVALSCOPE_AIPERF_EXECUTABLE.

ARG PYTHON_VERSION=3.12
ARG NODE_VERSION=22

# ---------------------------------------------------------------------------
# Stage 1: build the React dashboard (evalscope/web/dist)
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-bookworm-slim AS web-builder

WORKDIR /web

ARG NPM_REGISTRY=https://registry.npmjs.org

COPY evalscope/web/package.json evalscope/web/package-lock.json ./
COPY evalscope/web/tsconfig.json evalscope/web/tsconfig.app.json evalscope/web/tsconfig.node.json evalscope/web/tsconfig.test.json ./
COPY evalscope/web/vite.config.ts evalscope/web/vitest.config.ts evalscope/web/index.html ./
COPY evalscope/web/src ./src
COPY evalscope/web/public ./public

RUN npm ci --registry "${NPM_REGISTRY}" --no-audit --no-fund \
    && npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ARG AIPERF_LOCK_FILE=requirements/aiperf-linux-x86_64-py312.lock
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# procps provides /bin/ps, which EvalScope's external (AIPerf) runner requires.
# tini reaps orphaned children and forwards signals while the service is PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        procps \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# A single global pip configuration keeps the framework and AIPerf installs aligned
# with an optional private mirror, without leaving mirror settings in runtime env.
RUN set -eux; \
    mkdir -p /etc; \
    { \
        echo '[global]'; \
        echo "index-url = ${PIP_INDEX_URL}"; \
        if [ -n "${PIP_TRUSTED_HOST}" ]; then echo "trusted-host = ${PIP_TRUSTED_HOST}"; fi; \
    } > /etc/pip.conf

RUN groupadd --gid "${APP_GID}" evalscope \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash evalscope

WORKDIR /app

# Dependency layer: rebuilds only when requirements/ changes.
COPY requirements ./requirements
RUN pip install --no-cache-dir \
        -r requirements/framework.txt \
        -r requirements/service.txt \
        -r requirements/perf.txt

# AIPerf runtime layer. The lock is resolved for linux/x86_64 + CPython 3.12 with
# binary wheels only, matching docs/en/user_guides/stress_test/aiperf_cache.md.
COPY ${AIPERF_LOCK_FILE} /tmp/aiperf.lock
RUN set -eux; \
    python -m venv /opt/aiperf-venv; \
    /opt/aiperf-venv/bin/python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/aiperf.lock; \
    /opt/aiperf-venv/bin/python -m pip check; \
    /opt/aiperf-venv/bin/aiperf --version; \
    rm -f /tmp/aiperf.lock

# Application layer: source + freshly built dashboard, installed as a regular package
# so evalscope/web/dist is shipped inside site-packages.
COPY . /app
COPY --from=web-builder /web/dist /app/evalscope/web/dist
RUN pip install --no-cache-dir --no-deps /app \
    && test -f /app/evalscope/web/dist/index.html

# Persisted state. Named volumes inherit this ownership on first use.
RUN mkdir -p /data/outputs /data/cache/modelscope /data/cache/evalscope /data/cache/huggingface/datasets \
    && chown -R evalscope:evalscope /data

ENV EVALSCOPE_OUTPUT_DIR=/data/outputs \
    EVALSCOPE_AIPERF_EXECUTABLE=/opt/aiperf-venv/bin/aiperf \
    EVALSCOPE_SERVICE_HOST=0.0.0.0 \
    EVALSCOPE_SERVICE_PORT=9000 \
    MODELSCOPE_CACHE=/data/cache/modelscope \
    EVALSCOPE_CACHE=/data/cache/evalscope \
    HF_HOME=/data/cache/huggingface \
    HF_DATASETS_CACHE=/data/cache/huggingface/datasets

COPY docker/entrypoint.sh /usr/local/bin/evalscope-entrypoint.sh
RUN chmod 0755 /usr/local/bin/evalscope-entrypoint.sh

USER evalscope

EXPOSE 9000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('EVALSCOPE_SERVICE_PORT', '9000'), timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/evalscope-entrypoint.sh"]
CMD []
