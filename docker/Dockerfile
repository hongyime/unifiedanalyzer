FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
ENV PATH="/opt/venv/bin:${PATH}"

FROM python:3.12-slim AS dependencies
ENV PATH="/opt/venv/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 libxcb1 libgomp1 ffmpeg tesseract-ocr postgresql-client \
    cifs-utils util-linux \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv

RUN useradd -u 1001 -m appuser \
    && mkdir -p /app/data /app/backups /app/logs /home/appuser/.cache \
    && chown -R appuser:appuser /app /home/appuser

FROM dependencies AS dev
RUN pip install --no-cache-dir watchfiles==1.3.0
COPY --chown=appuser:appuser src/ /app/src/
COPY --chown=appuser:appuser data/ /app/data/
USER appuser
EXPOSE 8001
CMD ["watchfiles", "--target-type", "command", "python -m src.main serve", "/app/src"]
FROM dependencies AS production
COPY src/ src/
COPY data/ data/
COPY docker/embed-backfill-loop.sh /app/embed-backfill-loop.sh
RUN chmod +x /app/embed-backfill-loop.sh
COPY docker/face_worker_entrypoint.sh /app/face_worker_entrypoint.sh
RUN chmod +x /app/face_worker_entrypoint.sh
USER appuser
EXPOSE 8001
CMD ["python", "-m", "src.main", "serve"]
