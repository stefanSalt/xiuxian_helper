FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY xiuxian.py ./
COPY xiuxian_bot ./xiuxian_bot
COPY README.md ./README.md
COPY .env.example ./.env.example

RUN mkdir -p /app/data /app/data/logs /app/data/sessions

EXPOSE 11111

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; port = os.getenv('WEB_PORT', '11111'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3)"]

CMD ["python", "xiuxian.py"]
