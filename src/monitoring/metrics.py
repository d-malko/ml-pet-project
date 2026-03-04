"""
Prometheus метрики — инфраструктурные + ML-специфичные.
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY

# ── HTTP метрики ──────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

ACTIVE_REQUESTS = Gauge(
    "http_active_requests",
    "Number of requests currently being processed",
)

# ── ML метрики ────────────────────────────────────────────────────
PREDICTION_CONFIDENCE = Histogram(
    "ml_prediction_confidence",
    "Distribution of model confidence scores",
    ["model_version"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0],
)

PREDICTED_CLASS_COUNTER = Counter(
    "ml_predicted_class_total",
    "Predictions per class",
    ["predicted_class", "model_version"],
)

MODEL_ERRORS = Counter(
    "ml_model_errors_total",
    "Model inference errors",
    ["error_type"],
)

PREDICTION_LATENCY = Histogram(
    "ml_prediction_latency_seconds",
    "Model inference latency",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)
