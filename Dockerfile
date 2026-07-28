# Production image for the video-to-knowledge-graph app: both the vidblog
# ingestion pipeline (YouTube -> transcript/screenshots/PDF) and the kgwiki
# knowledge-graph server live in the same container so kgwiki's /api/ingest
# endpoint can call vidblog.cli.run() in-process.
# Build context is the repo root (needed so both packages can be COPYed in).
FROM python:3.12-slim

WORKDIR /app

# System deps:
#   curl        - HEALTHCHECK below
#   ffmpeg deps - opencv-python-headless and imageio-ffmpeg are both built to
#                 avoid needing system ffmpeg/libGL, but libgl1/libglib2.0-0
#                 cover the rare indirect dependency some opencv wheels probe
#                 for at import time even in headless builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY vidblog/ ./vidblog/
COPY kgwiki/ ./kgwiki/
COPY ollama_client.py ./ollama_client.py

ENV KGWIKI_HOST=0.0.0.0 \
    KGWIKI_PORT=8765 \
    KGWIKI_NO_BROWSER=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8765/api/llm_status || exit 1

CMD ["python", "-m", "kgwiki.server"]
