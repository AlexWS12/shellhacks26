# Tandem API. Build context = repo root (the image needs ../data):
#   docker build -f backend/Dockerfile -t tandem-api .
FROM python:3.12-slim

# poppler-utils = pdftotext (the committed text cache also works without it)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY data /app/data

ENV TANDEM_REPO_DIR=/app PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
