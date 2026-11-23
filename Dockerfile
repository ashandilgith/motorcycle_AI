# Moto AI backend + phone web app in one container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DB_PATH=/data/motorcycle_ai.db \
    OSM_TILE_DIR=/srv/backend/data/osm_tiles

WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY web web

# Non-root user; /data holds the SQLite database (mount a persistent disk there).
RUN useradd --create-home app && mkdir -p /data && chown -R app /data /srv/backend/data
USER app
WORKDIR /srv/backend

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/health')"
# --proxy-headers: hosts terminate HTTPS in front of us; needed for client IPs (rate limits).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
