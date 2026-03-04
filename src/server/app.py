"""
FastAPI inference server.
"""

import os
import time
import uuid
import logging
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from src.model.predict import ModelPredictor
from src.monitoring.metrics import (
    REQUEST_COUNT, REQUEST_LATENCY, ACTIVE_REQUESTS,
    PREDICTION_CONFIDENCE, PREDICTED_CLASS_COUNTER,
    MODEL_ERRORS, PREDICTION_LATENCY,
)

# ── Logging setup ─────────────────────────────────────────────────
def setup_logging():
    class JSONFormatter(logging.Formatter):
        def format(self, record):
            data = {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "service": "mnist-classifier",
                "model_version": os.getenv("MODEL_VERSION", "unknown"),
            }
            if record.exc_info:
                data["exception"] = self.formatException(record.exc_info)
            # Дополнительные поля из extra={}
            for key, val in record.__dict__.items():
                if key not in {
                    "name","msg","args","levelname","levelno","pathname",
                    "filename","module","exc_info","exc_text","stack_info",
                    "lineno","funcName","created","msecs","relativeCreated",
                    "thread","threadName","processName","process","message",
                    "taskName",
                }:
                    data[key] = val
            return json.dumps(data)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]

setup_logging()
logger = logging.getLogger("server")

# ── App state ─────────────────────────────────────────────────────
predictor: ModelPredictor = None
MODEL_VERSION = os.getenv("MODEL_VERSION", "1.0.0")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global predictor
    model_path = os.getenv("MODEL_PATH", "saved_model")
    if Path(model_path).exists():
        try:
            logger.info("Loading model", extra={"model_path": model_path})
            predictor = ModelPredictor(model_path=model_path)
            logger.info("Model loaded", extra={"model_version": MODEL_VERSION})
        except Exception as e:
            logger.warning("Model load failed, degraded mode", extra={"error": str(e)})
    else:
        logger.warning("No model found, degraded mode", extra={"model_path": model_path})
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="MNIST Classifier API",
    version=MODEL_VERSION,
    lifespan=lifespan,
)


# ── Middleware ────────────────────────────────────────────────────
@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    req_id = str(uuid.uuid4())
    start = time.time()
    ACTIVE_REQUESTS.inc()

    try:
        response = await call_next(request)
        duration = time.time() - start

        REQUEST_LATENCY.labels(endpoint=request.url.path).observe(duration)
        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status=str(response.status_code),
        ).inc()

        logger.info(
            "Request completed",
            extra={
                "request_id": req_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_seconds": round(duration, 4),
            },
        )
        response.headers["X-Request-ID"] = req_id
        return response
    except Exception as exc:
        REQUEST_COUNT.labels(
            method=request.method, endpoint=request.url.path, status="500"
        ).inc()
        logger.error("Request failed", extra={"request_id": req_id, "error": str(exc)}, exc_info=True)
        raise
    finally:
        ACTIVE_REQUESTS.dec()


# ── Schemas ───────────────────────────────────────────────────────
class PredictRequest(BaseModel):
    # (N, 28, 28) или (28, 28) — список чисел
    image: list

    @field_validator("image")
    @classmethod
    def validate_image(cls, v):
        arr = np.array(v)
        # Принимаем (28,28) или (N,28,28)
        if arr.ndim == 2 and arr.shape == (28, 28):
            return v
        if arr.ndim == 3 and arr.shape[1:] == (28, 28):
            return v
        raise ValueError(f"Expected shape (28,28) or (N,28,28), got {arr.shape}")


class PredictResponse(BaseModel):
    predicted_class: int
    confidence: float
    probabilities: list[float]
    model_version: str
    request_id: str


# ── Endpoints ─────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "model_loaded": predictor is not None}


@app.get("/ready")
def ready():
    if predictor is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/info")
def info():
    return {"model_version": MODEL_VERSION, "framework": "tensorflow"}


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest):
    if predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready")

    req_id = str(uuid.uuid4())
    try:
        raw = np.array(request.image)

        t0 = time.time()
        classes, confidences = predictor.predict_class(raw)
        probs = predictor.predict(raw)
        latency = time.time() - t0

        PREDICTION_LATENCY.observe(latency)
        PREDICTION_CONFIDENCE.labels(model_version=MODEL_VERSION).observe(float(confidences[0]))
        PREDICTED_CLASS_COUNTER.labels(
            predicted_class=str(int(classes[0])), model_version=MODEL_VERSION
        ).inc()

        logger.info(
            "Prediction made",
            extra={
                "request_id": req_id,
                "predicted_class": int(classes[0]),
                "confidence": round(float(confidences[0]), 4),
                "inference_latency": round(latency, 4),
                "model_version": MODEL_VERSION,
            },
        )

        return PredictResponse(
            predicted_class=int(classes[0]),
            confidence=round(float(confidences[0]), 4),
            probabilities=[round(float(p), 4) for p in probs[0]],
            model_version=MODEL_VERSION,
            request_id=req_id,
        )

    except ValueError as e:
        MODEL_ERRORS.labels(error_type="validation_error").inc()
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        MODEL_ERRORS.labels(error_type="inference_error").inc()
        logger.error("Prediction failed", extra={"request_id": req_id, "error": str(e)}, exc_info=True)
        raise HTTPException(status_code=500, detail="Inference error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
