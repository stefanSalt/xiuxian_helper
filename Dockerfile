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

CMD ["python", "xiuxian.py"]
