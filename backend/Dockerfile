FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets the PORT env variable dynamically. Uvicorn binds to it.
# `exec` replaces the shell with uvicorn so PID 1 receives SIGTERM directly,
# enabling FastAPI lifespan shutdown (DB pool close, WebSocket drain) instead
# of being SIGKILLed after Cloud Run's 10s grace period.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
