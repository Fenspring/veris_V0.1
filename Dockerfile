# Veris — Healthcare Knowledge Operating System
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VERIS_DATA_DIR=/app/data \
    VERIS_DB=/app/data/veris.db \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# Dependencies first so code edits do not invalidate the layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY veris/ ./veris/
COPY web/ ./web/
COPY eval/ ./eval/
COPY tests/ ./tests/
COPY corpus/ ./corpus/

# Run unprivileged. The data volume is the only writable path.
RUN useradd --create-home --uid 10001 veris \
 && mkdir -p /app/data && chown -R veris:veris /app
USER veris

VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "veris.api:app", "--host", "0.0.0.0", "--port", "8000"]
