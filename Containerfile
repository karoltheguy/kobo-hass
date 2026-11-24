# hadash server image. Config and layout are not baked in: mount the folder that
# holds config.yaml and layout.yaml at /config (layout edits reload live).
FROM docker.io/library/python:3.13-slim

# The renderer looks for DejaVu Sans first; Symbola draws emoji icons in black and white.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-symbola \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY hadash ./hadash
RUN uv sync --locked --no-dev

ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
EXPOSE 8090
VOLUME /config
STOPSIGNAL SIGINT
CMD ["hadash", "-c", "/config/config.yaml", "serve"]
