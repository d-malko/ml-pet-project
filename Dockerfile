# ─── Stage 1: Builder ─────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Устанавливаем зависимости отдельным слоем (кеш Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ─── Stage 2: Runtime ─────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Security: non-root user
RUN groupadd -r mluser && useradd -r -g mluser -u 1001 mluser

WORKDIR /app

# Копируем Python пакеты из builder
COPY --from=builder /root/.local /home/mluser/.local

# Копируем исходный код
COPY --chown=mluser:mluser src/ ./src/

# Аргументы для метаданных образа
ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown
ARG MODEL_VERSION=1.0.0

LABEL org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${MODEL_VERSION}" \
      ml.model.version="${MODEL_VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    MODEL_VERSION=${MODEL_VERSION} \
    PATH=/home/mluser/.local/bin:$PATH

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

USER mluser
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "src.server.app:app", "--host", "0.0.0.0", "--port", "8080"]
