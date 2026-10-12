# Commerce mock: migrates its schema on start, then serves the API on :8000.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH

WORKDIR /app
# The whole workspace: uv needs every member's pyproject.toml to use the lockfile.
COPY . .
RUN uv sync --locked --no-dev --package commerce-mock

ENV POLICY_PATH=/app/packages/dtc_policy/policies/policy.yaml
EXPOSE 8000
CMD ["sh", "-c", "alembic -c apps/commerce_mock/alembic.ini upgrade head && exec uvicorn commerce_mock.app:create_app --factory --host 0.0.0.0 --port 8000"]
