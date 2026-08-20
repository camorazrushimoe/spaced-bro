# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the package and its dependencies.
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

# Run as a non-root user; /data holds the SQLite file (named volume).
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-m", "spacedbro.healthcheck"]

ENTRYPOINT ["python", "-m", "spacedbro"]
