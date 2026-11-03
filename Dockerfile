# Runtime image: the standard-library lab and nothing else, run as a non-root user.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WITNESSFORGE_ENV=production WITNESSFORGE_HOST=0.0.0.0 WITNESSFORGE_PORT=8765 WITNESSFORGE_DATA_DIR=/data
RUN useradd --system --uid 10001 --no-create-home app && mkdir /data && chown app /data
WORKDIR /app
COPY witnessforge witnessforge
USER app
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['WITNESSFORGE_PORT'], timeout=2)"]
CMD ["python", "-m", "witnessforge", "serve"]
