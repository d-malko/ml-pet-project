"""
Inference module — загрузка модели и предсказания.
Поддерживает SavedModel (export) и .keras форматы.
"""

import os
import numpy as np
import tensorflow as tf
from pathlib import Path


class ModelPredictor:
    """Обёртка над TF-моделью для инференса."""

    def __init__(self, model_path: str = None):
        model_path = model_path or os.getenv("MODEL_PATH", "saved_model")
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Model not found at: {model_path}")

        # Пробуем keras load, при ошибке — SavedModel signatures
        try:
            self._model = tf.keras.models.load_model(model_path)
            self._use_signatures = False
        except Exception:
            self._model = tf.saved_model.load(model_path)
            self._infer = self._model.signatures["serving_default"]
            self._use_signatures = True

        self.model_path = model_path
        self._input_shape = (28, 28, 1)

    def preprocess(self, raw: np.ndarray) -> np.ndarray:
        """
        Принимает: uint8/float (H,W) или (H,W,1) или (N,H,W) или (N,H,W,1)
        Возвращает: float32 (N, 28, 28, 1) нормализованные в [0, 1]
        """
        img = np.array(raw, dtype=np.float32)

        if img.ndim == 2:
            img = img[np.newaxis, :, :, np.newaxis]
        elif img.ndim == 3 and img.shape[-1] != 1:
            img = img[:, :, :, np.newaxis]
        elif img.ndim == 3:
            img = img[np.newaxis]

        if img.shape[1:] != self._input_shape:
            raise ValueError(
                f"Invalid input shape: expected (..., 28, 28, 1), got {img.shape}"
            )

        if img.max() > 1.0:
            img = img / 255.0

        return img

    def predict(self, raw_input: np.ndarray) -> np.ndarray:
        """Возвращает softmax вероятности (N, 10)."""
        processed = self.preprocess(raw_input)

        if self._use_signatures:
            # SavedModel через export()
            input_key = list(self._infer.structured_input_signature[1].keys())[0]
            tensor = tf.constant(processed)
            result = self._infer(**{input_key: tensor})
            output_key = list(result.keys())[0]
            return result[output_key].numpy()
        else:
            return self._model.predict(processed, verbose=0)

    def predict_class(self, raw_input: np.ndarray) -> tuple:
        """Возвращает (predicted_class, confidence)."""
        probs = self.predict(raw_input)
        return np.argmax(probs, axis=-1), np.max(probs, axis=-1)

    def evaluate(self, X: np.ndarray, y: np.ndarray) -> float:
        """Accuracy на переданных данных."""
        classes, _ = self.predict_class(X)
        return float(np.mean(classes == y))
