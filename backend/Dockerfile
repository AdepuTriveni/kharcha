# One image for every backend service; pick the service with the command (PROJECT_SPEC §31).
#   docker build -f backend/Dockerfile -t kharcha-backend .        (context = repo root)
#   docker run kharcha-backend kharcha-ingest
FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# kharcha-agents uses ../../../ml (shared parsing metrics), so ml sits next to /app.
COPY ml /ml
COPY backend /app
WORKDIR /app
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm
RUN useradd --uid 10001 --create-home kharcha
COPY --from=build --chown=kharcha /ml /ml
COPY --from=build --chown=kharcha /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 KHARCHA_ENV=prod
WORKDIR /app
USER kharcha
EXPOSE 8000
CMD ["kharcha-ingest"]
