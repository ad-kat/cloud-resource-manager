# ---------------------------------------------------------------------------
# Stage 1 — builder
# Install deps in a separate stage so pip's cache doesn't bloat the final image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# copy requirements first so this layer is only rebuilt when requirements.txt changes

# (not on every code edit — saves a lot of time locally)
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — final runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="cloud-resource-manager"
LABEL org.opencontainers.image.description="Cloud Resource Lifecycle Manager - FastAPI + SQLite"
LABEL org.opencontainers.image.authors="Adri Katyayan"

# libpq5 is the runtime library psycopg2-binary links against
RUN apt-get update && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash appuser \
 && mkdir -p /data && chown appuser /data

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/

EXPOSE 7860

USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]

