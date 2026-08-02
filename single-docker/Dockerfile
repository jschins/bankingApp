# Build frontend
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY single-docker/frontend/package.json single-docker/frontend/package-lock.json* ./
RUN npm install
COPY single-docker/frontend/ ./
RUN npm run build

# Python app
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8200

WORKDIR /app

COPY shared /shared
COPY single-docker/pyproject.toml single-docker/README.md ./
COPY single-docker/app ./app
RUN pip install --upgrade pip && pip install /shared && pip install .

COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8200

CMD uvicorn app.main:app --host "$HOST" --port "$PORT"
