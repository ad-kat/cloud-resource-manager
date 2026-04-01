# ---------------------------------------------------------------------------
# Stage 1 — builder
# Install deps in a separate stage so pip's cache doesn't bloat the final image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# libpq-dev + gcc needed to compile psycopg2 from source
# if you switch from psycopg2-binary to psycopg2 at some point
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
 && rm -rf /var/lib/apt/lists/*

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
LABEL org.opencontainers.image.description="Cloud Resource Lifecycle Manager — FastAPI + PostgreSQL"
LABEL org.opencontainers.image.authors="Adri Katyayan"

# libpq5 is the runtime library psycopg2-binary links against
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/

EXPOSE 8000

# non-root user — running as root inside a container is a security anti-pattern
USER appuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

