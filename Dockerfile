FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY README.md ./README.md

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.api.server:app --host 0.0.0.0 --port ${PORT:-8080} --workers ${UVICORN_WORKERS:-2} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive ${UVICORN_KEEPALIVE_SECONDS:-30}"]
