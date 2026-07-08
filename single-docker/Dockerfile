# Build frontend
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Python app
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8200

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

COPY --from=frontend /frontend/dist ./frontend/dist

EXPOSE 8200

CMD uvicorn app.main:app --host "$HOST" --port "$PORT"
