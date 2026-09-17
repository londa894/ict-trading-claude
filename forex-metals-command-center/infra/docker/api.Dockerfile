FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /repo
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    STRATEGY_SPEC_DIR=/repo/packages/strategy-spec

COPY services/api/pyproject.toml services/api/uv.lock services/api/
RUN cd services/api && uv sync --frozen --no-dev

COPY packages/strategy-spec packages/strategy-spec
COPY services/api services/api

WORKDIR /repo/services/api
RUN useradd --system --uid 10001 fmcc
USER fmcc
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
