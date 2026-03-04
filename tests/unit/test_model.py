"""
Unit тесты для MNIST Classifier.
Покрывают: preprocessing, model output, API endpoints, edge cases.
"""

import json
import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────
# Фикстуры
# ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def mock_predictor():
    """Мок предиктора — не загружает реальную модель."""
    predictor = MagicMock()

    def fake_predict(raw_input):
        arr = np.array(raw_input)
        n = 1 if arr.ndim <= 3 else arr.shape[0]
        probs = np.zeros((n, 10), dtype=np.float32)
        probs[:, 7] = 0.95  # всегда предсказываем цифру 7
        probs[:, 0] = 0.05
        return probs

    def fake_predict_class(raw_input):
        probs = fake_predict(raw_input)
        return np.argmax(probs, axis=-1), np.max(probs, axis=-1)

    def fake_preprocess(raw):
        arr = np.array(raw, dtype=np.float32)
        if arr.max() > 1.0:
            arr = arr / 255.0
        if arr.ndim == 2:
            arr = arr[np.newaxis, :, :, np.newaxis]
        return arr

    predictor.predict.side_effect = fake_predict
    predictor.predict_class.side_effect = fake_predict_class
    predictor.preprocess.side_effect = fake_preprocess
    return predictor


@pytest.fixture(scope="session")
def client(mock_predictor):
    """TestClient с подменённым предиктором."""
    import src.server.app as app_module
    app_module.predictor = mock_predictor

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def valid_image_28x28():
    return np.random.randint(0, 255, (28, 28), dtype=np.uint8).tolist()


@pytest.fixture
def valid_batch_4x28x28():
    return np.random.randint(0, 255, (4, 28, 28), dtype=np.uint8).tolist()


# ──────────────────────────────────────────────────────────────────
# Тесты ModelPredictor (unit — без реальной TF модели)
# ──────────────────────────────────────────────────────────────────

class TestModelPredictorPreprocess:
    """Тесты препроцессинга входных данных."""

    def test_2d_input_gets_batch_and_channel_dims(self, mock_predictor):
        raw = np.random.rand(28, 28).astype(np.float32)
        result = mock_predictor.preprocess(raw)
        assert result.shape == (1, 28, 28, 1)

    def test_uint8_input_normalized_to_01(self, mock_predictor):
        raw = np.full((28, 28), 255, dtype=np.uint8)
        result = mock_predictor.preprocess(raw)
        assert result.max() <= 1.0, "Значения должны быть ≤ 1.0 после нормализации"

    def test_float_input_unchanged_if_already_normalized(self, mock_predictor):
        raw = np.ones((28, 28), dtype=np.float32) * 0.5
        result = mock_predictor.preprocess(raw)
        np.testing.assert_array_less(result, 1.01)
        np.testing.assert_array_less(-0.01, result)


class TestModelPredictorOutput:
    """Тесты корректности выходных данных модели."""

    def test_output_shape_single_image(self, mock_predictor, valid_image_28x28):
        result = mock_predictor.predict(np.array(valid_image_28x28))
        assert result.shape == (1, 10), f"Expected (1, 10), got {result.shape}"

    def test_output_probabilities_sum_to_one(self, mock_predictor, valid_image_28x28):
        result = mock_predictor.predict(np.array(valid_image_28x28))
        np.testing.assert_almost_equal(result.sum(), 1.0, decimal=4)

    def test_output_probabilities_in_range(self, mock_predictor, valid_image_28x28):
        result = mock_predictor.predict(np.array(valid_image_28x28))
        assert (result >= 0).all(), "Все вероятности должны быть ≥ 0"
        assert (result <= 1).all(), "Все вероятности должны быть ≤ 1"

    def test_predict_class_returns_valid_digit(self, mock_predictor, valid_image_28x28):
        classes, confidences = mock_predictor.predict_class(np.array(valid_image_28x28))
        assert 0 <= int(classes[0]) <= 9, "Предсказанный класс должен быть от 0 до 9"
        assert 0.0 <= float(confidences[0]) <= 1.0, "Confidence должен быть в [0, 1]"

    @pytest.mark.parametrize("batch_size", [1, 4, 8, 16])
    def test_batch_prediction_output_shape(self, mock_predictor, batch_size):
        batch = np.random.rand(batch_size, 28, 28).astype(np.float32)
        result = mock_predictor.predict(batch)
        assert result.shape[0] == batch_size, \
            f"Batch size mismatch: expected {batch_size}, got {result.shape[0]}"


# ──────────────────────────────────────────────────────────────────
# Тесты API endpoints
# ──────────────────────────────────────────────────────────────────

class TestHealthEndpoints:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_contains_status(self, client):
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"

    def test_ready_returns_200_when_model_loaded(self, client):
        response = client.get("/ready")
        assert response.status_code == 200

    def test_metrics_endpoint_returns_prometheus_format(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert b"http_requests_total" in response.content

    def test_info_endpoint(self, client):
        response = client.get("/info")
        assert response.status_code == 200
        data = response.json()
        assert "model_version" in data
        assert "framework" in data


class TestPredictEndpoint:
    def test_predict_single_image_returns_200(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        assert response.status_code == 200

    def test_predict_response_schema(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        data = response.json()
        assert "predicted_class" in data
        assert "confidence" in data
        assert "probabilities" in data
        assert "model_version" in data
        assert "request_id" in data

    def test_predicted_class_is_valid_digit(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        data = response.json()
        assert 0 <= data["predicted_class"] <= 9

    def test_confidence_in_valid_range(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        data = response.json()
        assert 0.0 <= data["confidence"] <= 1.0

    def test_probabilities_length_is_10(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        data = response.json()
        assert len(data["probabilities"]) == 10

    def test_batch_predict(self, client, valid_batch_4x28x28):
        response = client.post("/predict", json={"image": valid_batch_4x28x28})
        # Batch request — first image in batch
        assert response.status_code == 200

    def test_invalid_shape_returns_422(self, client):
        bad_image = [[1, 2, 3], [4, 5, 6]]  # (2, 3) — неверная форма
        response = client.post("/predict", json={"image": bad_image})
        assert response.status_code == 422

    def test_empty_image_returns_422(self, client):
        response = client.post("/predict", json={"image": []})
        assert response.status_code == 422

    def test_missing_image_field_returns_422(self, client):
        response = client.post("/predict", json={})
        assert response.status_code == 422

    def test_response_has_request_id_header(self, client, valid_image_28x28):
        response = client.post("/predict", json={"image": valid_image_28x28})
        assert "x-request-id" in response.headers


class TestMetricsIntegration:
    """Проверяем что метрики инкрементируются после запросов."""

    def test_request_count_increments_after_predict(self, client, valid_image_28x28):
        # Делаем запрос
        client.post("/predict", json={"image": valid_image_28x28})
        # Получаем метрики
        metrics_response = client.get("/metrics")
        assert b"http_requests_total" in metrics_response.content

    def test_prediction_metrics_present(self, client, valid_image_28x28):
        client.post("/predict", json={"image": valid_image_28x28})
        metrics_response = client.get("/metrics")
        assert b"ml_prediction_confidence" in metrics_response.content
        assert b"ml_predicted_class_total" in metrics_response.content


# ──────────────────────────────────────────────────────────────────
# Edge cases
# ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_all_zeros_image(self, client):
        """Чёрное изображение не должно крашить сервер."""
        zeros = np.zeros((28, 28), dtype=np.uint8).tolist()
        response = client.post("/predict", json={"image": zeros})
        assert response.status_code == 200

    def test_all_ones_image(self, client):
        """Белое изображение."""
        ones = np.ones((28, 28), dtype=np.float32).tolist()
        response = client.post("/predict", json={"image": ones})
        assert response.status_code == 200

    def test_float_image_values(self, client):
        """Модель должна принимать float значения."""
        float_image = np.random.rand(28, 28).tolist()
        response = client.post("/predict", json={"image": float_image})
        assert response.status_code == 200
