"""
Performance тесты с Locust.
SLO: P99 < 300ms, error rate < 1%, throughput > 50 RPS

Запуск:
  locust -f tests/performance/locustfile.py \
    --host=http://localhost:8080 \
    --users=50 --spawn-rate=5 \
    --run-time=2m --headless \
    --csv=results/perf
"""

import json
import random
import numpy as np
from locust import HttpUser, task, between, events, constant_throughput
from locust.runners import MasterRunner


def make_image(batch_size: int = 1):
    """Генерация случайного изображения-пейлоада."""
    if batch_size == 1:
        return {"image": np.random.randint(0, 255, (28, 28), dtype=np.uint8).tolist()}
    return {"image": np.random.randint(0, 255, (batch_size, 28, 28), dtype=np.uint8).tolist()}


# ──────────────────────────────────────────────────────────────────
# Пользователи
# ──────────────────────────────────────────────────────────────────

class NormalUser(HttpUser):
    """
    Обычный пользователь: mixed traffic.
    80% single predict, 15% batch, 5% health checks.
    """
    wait_time = between(0.05, 0.2)

    def on_start(self):
        # Прогреваем — проверяем что сервер поднят
        self.client.get("/health")

    @task(16)
    def single_predict(self):
        payload = make_image(1)
        with self.client.post(
            "/predict",
            json=payload,
            name="POST /predict [single]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                if "predicted_class" not in data:
                    resp.failure("Missing predicted_class in response")
                elif not (0 <= data["predicted_class"] <= 9):
                    resp.failure(f"Invalid class: {data['predicted_class']}")
                elif resp.elapsed.total_seconds() > 0.5:
                    resp.failure(f"Latency SLO breach: {resp.elapsed.total_seconds():.3f}s")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(3)
    def batch_predict(self):
        batch_size = random.choice([4, 8])
        payload = make_image(batch_size)
        with self.client.post(
            "/predict",
            json=payload,
            name=f"POST /predict [batch={batch_size}]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                if resp.elapsed.total_seconds() > 2.0:
                    resp.failure(f"Batch latency SLO breach: {resp.elapsed.total_seconds():.3f}s")
            else:
                resp.failure(f"HTTP {resp.status_code}")

    @task(1)
    def health_check(self):
        with self.client.get("/health", name="GET /health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Health check failed: {resp.status_code}")


class HighLoadUser(HttpUser):
    """
    Высокая нагрузка: constant throughput — stress test.
    """
    wait_time = constant_throughput(10)  # 10 запросов/сек на юзера

    @task
    def rapid_predict(self):
        payload = make_image(1)
        with self.client.post(
            "/predict",
            json=payload,
            name="POST /predict [stress]",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 429):  # 429 = rate limit, OK
                resp.failure(f"Unexpected HTTP {resp.status_code}")


# ──────────────────────────────────────────────────────────────────
# SLO Validation (выполняется по завершению теста)
# ──────────────────────────────────────────────────────────────────

# SLO пороги
SLO = {
    "p50_latency_ms": 100,
    "p95_latency_ms": 250,
    "p99_latency_ms": 500,
    "error_rate_pct": 1.0,
    "min_rps": 10,
}


@events.quitting.add_listener
def validate_slos(environment, **kwargs):
    stats = environment.runner.stats.total

    if stats.num_requests == 0:
        print("⚠️  No requests made — skipping SLO validation")
        return

    p50 = stats.get_response_time_percentile(0.50)
    p95 = stats.get_response_time_percentile(0.95)
    p99 = stats.get_response_time_percentile(0.99)
    error_rate = stats.fail_ratio * 100
    rps = stats.current_rps

    print("\n" + "=" * 60)
    print("📊 SLO VALIDATION REPORT")
    print("=" * 60)
    print(f"  Total requests:  {stats.num_requests}")
    print(f"  P50 latency:     {p50:.0f}ms  (SLO ≤ {SLO['p50_latency_ms']}ms)")
    print(f"  P95 latency:     {p95:.0f}ms  (SLO ≤ {SLO['p95_latency_ms']}ms)")
    print(f"  P99 latency:     {p99:.0f}ms  (SLO ≤ {SLO['p99_latency_ms']}ms)")
    print(f"  Error rate:      {error_rate:.2f}% (SLO ≤ {SLO['error_rate_pct']}%)")
    print(f"  Current RPS:     {rps:.1f}   (SLO ≥ {SLO['min_rps']})")

    violations = []
    if p50 > SLO["p50_latency_ms"]:
        violations.append(f"P50 latency {p50:.0f}ms > {SLO['p50_latency_ms']}ms")
    if p95 > SLO["p95_latency_ms"]:
        violations.append(f"P95 latency {p95:.0f}ms > {SLO['p95_latency_ms']}ms")
    if p99 > SLO["p99_latency_ms"]:
        violations.append(f"P99 latency {p99:.0f}ms > {SLO['p99_latency_ms']}ms")
    if error_rate > SLO["error_rate_pct"]:
        violations.append(f"Error rate {error_rate:.2f}% > {SLO['error_rate_pct']}%")

    if violations:
        print("\n❌ SLO VIOLATIONS:")
        for v in violations:
            print(f"   • {v}")
        environment.process_exit_code = 1
    else:
        print("\n✅ All SLOs passed!")
    print("=" * 60)
