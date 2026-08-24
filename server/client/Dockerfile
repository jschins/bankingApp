FROM node:22-alpine AS frontend-build

WORKDIR /build/frontend
COPY server/client/frontend/package.json server/client/frontend/package-lock.json ./
RUN npm ci
COPY server/client/frontend/ ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8300

WORKDIR /app
COPY shared/ ./shared/
COPY server/client/ ./client/
RUN pip install --no-cache-dir setuptools wheel \
    && pip install --no-cache-dir --no-build-isolation ./shared ./client
COPY --from=frontend-build /build/frontend/dist/ ./client/frontend/dist/

WORKDIR /app/client
EXPOSE 8300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8300/api/health', timeout=3)"

CMD ["boekhouding-client"]