FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
COPY apps/api ./apps/api
RUN uv sync --locked --no-dev --all-packages --no-editable
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
EXPOSE 8000
CMD ["uvicorn", "yihe_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
