# ---------------------------------------------------------------------------
# Stage 1 — builder
# We install dependencies in a separate stage so the final image doesn't
# contain pip's cache, making it leaner.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Set a safe working directory inside the build stage
WORKDIR /build

# Copy only the requirements file first.
# Docker caches each instruction as a layer.  Copying requirements.txt before
# the source code means the expensive `pip install` layer is only re-run when
# requirements.txt actually changes, not on every code edit.
COPY requirements.txt .

RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — final runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# OCI image labels — good practice, shows up in `docker inspect`
LABEL org.opencontainers.image.title="cloud-resource-manager"
LABEL org.opencontainers.image.description="Cloud Resource Lifecycle Manager — FastAPI + SQLite"
LABEL org.opencontainers.image.authors="SBU CS Student"

# Create a non-root user.  Running as root inside containers is a security
# anti-pattern — real cloud deployments always use a dedicated service account.
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /install /usr/local

# Copy application source code
COPY app/ ./app/

# The SQLite database file will be written here.  Declare it as a volume so
# docker-compose can mount a host directory for persistence across restarts.
VOLUME ["/app/data"]

# Tell the container which port the app listens on (metadata only — doesn't
# actually publish the port; that's done in docker-compose.yml)
EXPOSE 8000

# Switch to non-root user before starting the process
USER appuser

# Environment variables with sensible defaults
ENV DB_PATH=/app/data/resources.db \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Start the ASGI server.
# --host 0.0.0.0  → listen on all network interfaces inside the container
#                   (required — 127.0.0.1 would be unreachable from outside)
# --port 8000     → must match EXPOSE above and the docker-compose port mapping
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
