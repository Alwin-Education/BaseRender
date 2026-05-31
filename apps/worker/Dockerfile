FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY packages/baserender packages/baserender
COPY apps/worker apps/worker

RUN pip install --no-cache-dir -e packages/baserender -e apps/worker

CMD ["python", "-m", "baserender_worker.service"]
