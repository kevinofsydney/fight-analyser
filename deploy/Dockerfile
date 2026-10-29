# One image for both the app and the daily refresher (see deploy/docker-compose.yml).
FROM python:3.12-slim

ARG SUPERCRONIC_VERSION=v0.2.33
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DB_PATH=/data/fightdb.duckdb \
    DOWNLOAD_DIR=/data/sources \
    OVERRIDES_DIR=/app/data/overrides \
    HYPOTHESES_DIR=/app/hypotheses

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && arch="${TARGETARCH:-amd64}" \
    && curl -fsSL -o /usr/local/bin/supercronic \
       "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}" \
    && chmod +x /usr/local/bin/supercronic

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY hypotheses ./hypotheses
COPY data/overrides ./data/overrides
COPY .streamlit ./.streamlit
COPY deploy/crontab ./deploy/crontab
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 fight && mkdir -p /data && chown -R fight /data /app
USER fight
VOLUME ["/data"]
EXPOSE 8501

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/fightdb/app/Home.py", "--server.port=8501", "--server.address=0.0.0.0"]
