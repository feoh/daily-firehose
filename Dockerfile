FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv venv --relocatable /app/.venv && \
    uv sync --frozen --no-dev

COPY . .

RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["gunicorn", "daily_firehose.wsgi:application", "--bind", "0.0.0.0:8000"]
