# syntax=docker/dockerfile:1
# BrewberryPi application image (ADR-0001, runtime contract version 2).
# Build: make image   (multi-arch release builds run in CI: .github/workflows/ci.yml)
# Optional build secrets google_drive_client_id and google_drive_client_secret build the Cloud Sync Google
# client into the image (docs/deployment/cloud-sync-google.md); without them Cloud Sync is unavailable.

# --- 1. Front end (architecture-independent output, built on the build host) -----------------
FROM --platform=$BUILDPLATFORM node:22-trixie-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Wheel + locked runtime requirements (pure Python, built on the build host) -----------
FROM --platform=$BUILDPLATFORM python:3.13-slim-trixie AS wheel
COPY --from=ghcr.io/astral-sh/uv:0.12.24 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY --from=frontend /src/src/brewberrypi/web/static/ src/brewberrypi/web/static/
COPY scripts/google-drive-client.py /tmp/
RUN --mount=type=secret,id=google_drive_client_id --mount=type=secret,id=google_drive_client_secret \
    python3 /tmp/google-drive-client.py /run/secrets src/brewberrypi/data/google-drive-client.json
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /dist/requirements.txt

# --- 3. Runtime ---------------------------------------------------------------------------------
FROM debian:trixie-slim
ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="BrewberryPi" \
      org.opencontainers.image.source="https://github.com/tinkerius/brewberrypi-app" \
      org.opencontainers.image.version="${VERSION}" \
      org.brewberrypi.runtime-contract="2"

# Debian's python3 + libgpiod v2 bindings (no compiler needed); the venv sees them via
# --system-site-packages. All other dependencies are installed hash-checked from uv.lock.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-libgpiod \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv --system-site-packages /opt/brewberrypi \
 && useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent brewberrypi
COPY --from=wheel /dist/ /tmp/dist/
RUN /opt/brewberrypi/bin/pip install --no-cache-dir --require-hashes -r /tmp/dist/requirements.txt \
 && /opt/brewberrypi/bin/pip install --no-cache-dir --no-deps /tmp/dist/*.whl \
 && rm -rf /tmp/dist

ENV PATH="/opt/brewberrypi/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["brewberrypi"]
CMD ["--config", "/config/config.json"]
