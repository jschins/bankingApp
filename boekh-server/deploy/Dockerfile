# Build context is the repo root: `docker build -f deploy/Dockerfile .`
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_MODULE=app.main:app \
    HOST=0.0.0.0 \
    PORT=8000 \
    WORKERS=1

WORKDIR /app

# Install dependencies first for better layer caching
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e . || true

# Now copy the application source
COPY . .
RUN pip install -e .

EXPOSE 8000

# Shell form so env vars expand at runtime
CMD uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" --workers "$WORKERS"
