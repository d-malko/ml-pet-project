# 🤖 ML Pet Project — MNIST Classifier

Полный MLOps пет-проект: обучение CNN модели → MLflow Registry → MinIO (S3) → Docker → Minikube + Istio → Canary Deployment → CI/CD через GitHub Actions с self-hosted runner.

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Actions CI/CD Pipeline                                  │
│                                                                 │
│  unit-tests → build images → train in cluster →                 │
│  perf tests (real model) → canary deploy                        │
└──────────────────────────────┬──────────────────────────────────┘
                               │ self-hosted runner
┌──────────────────────────────▼──────────────────────────────────┐
│  Minikube Cluster                                               │
│                                                                 │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────────┐  │
│  │    MinIO    │   │   MLflow    │   │   Argo Rollouts      │  │
│  │  (S3 store) │◄──│  Registry   │   │   Canary Deploy      │  │
│  │             │   │  Tracking   │   │                      │  │
│  └──────┬──────┘   └─────────────┘   │  init container      │  │
│         │                            │    ↓ download model  │  │
│         │ artifacts                  │  ml-model pod        │  │
│         └────────────────────────────►  (FastAPI server)    │  │
│                                      └──────────────────────┘  │
│                                                                 │
│  Istio VirtualService (weight-based routing)                    │
│    stable (90-100%) ◄──── canary (0-10%)                        │
│                                                                 │
│  Prometheus + Grafana → Monitoring & Alerting                   │
└─────────────────────────────────────────────────────────────────┘
```

## 📁 Структура проекта

```
ml-pet-project/
├── src/
│   ├── model/
│   │   ├── train.py                   # Обучение CNN, загрузка в MinIO
│   │   └── predict.py                 # Инференс (SavedModel + keras)
│   ├── server/
│   │   └── app.py                     # FastAPI: /predict /health /metrics
│   └── monitoring/
│       └── metrics.py                 # Prometheus метрики
├── tests/
│   ├── unit/
│   │   └── test_model.py              # pytest, coverage ≥ 75%
│   └── performance/
│       └── locustfile.py              # Locust, SLO валидация
├── k8s/
│   ├── 00-namespace.yaml              # Namespaces + Istio injection
│   ├── 00-minio.yaml                  # MinIO: Deployment + PVC + setup Job
│   ├── 01-mlflow.yaml                 # MLflow: SQLite backend + S3 artifacts
│   ├── 02-rollout.yaml                # Argo Rollouts canary + init container
│   ├── 03-services.yaml               # Services (stable/canary) + secrets
│   ├── 04-istio.yaml                  # VirtualService + DestinationRule
│   ├── 05-analysis-alerts.yaml        # AnalysisTemplate + PrometheusRule
│   ├── 06-servicemonitor.yaml         # Prometheus ServiceMonitor
│   ├── 07-training-job.yaml           # K8s Job для обучения в кластере
│   └── 08-perf-test-job.yaml          # K8s Job для Locust (SLO check)
├── scripts/
│   ├── setup-local.sh                 # Полный setup кластера с нуля
│   ├── setup-github-runner.sh         # Установка self-hosted GitHub runner
│   ├── promote_staging.py             # Promote модели → Staging
│   └── promote_production.py          # Promote модели → Production
├── .github/
│   └── workflows/
│       └── ci-cd.yml                  # 5-шаговый CI/CD pipeline
├── notebooks/
│   └── train_and_explore.ipynb        # Обучение + визуализация
├── Dockerfile                         # Inference образ (multi-stage)
├── Dockerfile.train                   # Training образ (обучение в кластере)
├── Makefile                           # Команды для локальной разработки
├── requirements.txt                   # Python зависимости
└── pyproject.toml                     # pytest config
```

## 🚀 Быстрый старт

### Требования

```bash
# Ubuntu/Debian
sudo apt-get install -y docker.io curl
# minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube
# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install kubectl /usr/local/bin/kubectl
# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
# argo rollouts plugin
curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
sudo install kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
```

### 1. Поднять кластер

```bash
git clone <repo> && cd ml-pet-project

# Полный setup с нуля (minikube + istio + argo rollouts + monitoring)
bash scripts/setup-local.sh
```

### 2. Задеплоить инфраструктуру

```bash
# MinIO + MLflow + K8s manifests
make deploy
```

### 3. Настроить Python окружение

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install setuptools wheel
pip install -r requirements.txt
```

### 4. Обучить и задеплоить модель

```bash
# Открываем доступ к сервисам
make port-minio &    # MinIO console: http://localhost:9001
make port-mlflow &   # MLflow UI:     http://localhost:5000

# Обучение — модель сохраняется в MinIO, регистрируется в MLflow
make train

# Promote в Production
make promote-staging
make promote-production

# Собрать inference образ и задеплоить
# (init container скачает модель из MinIO автоматически)
make build-minikube IMAGE_TAG=v1
kubectl-argo-rollouts set image ml-model ml-model=ml-model:v1 -n ml-serving

# Следить за rollout
make rollout-status
```

---

## 🔄 CI/CD Pipeline

Pipeline состоит из 5 jobs. Jobs 1–2 выполняются на **GitHub hosted runner**, jobs 3–5 — на **self-hosted runner** (твоя машина с Minikube).

```
push → main
        │
   ┌────▼──────────┐
   │ 1. Unit Tests │  github-hosted ── pytest, coverage ≥ 75%
   └────┬──────────┘
        │
   ┌────▼──────────┐
   │ 2. Build      │  github-hosted ── inference + training образы
   │    Images     │                   security scan (Trivy)
   └────┬──────────┘
        │  образы как GitHub Artifacts
        │
   ┌────▼──────────────────────────────────┐
   │ 3. Train in Cluster   (self-hosted)   │
   │    kubectl apply 07-training-job.yaml │
   │    ожидание завершения (timeout 30m)  │
   │    модель → MinIO → MLflow → Staging  │
   └────┬──────────────────────────────────┘
        │
   ┌────▼──────────────────────────────────┐
   │ 4. Perf Tests         (self-hosted)   │
   │    kubectl apply 08-perf-test-job     │
   │    Locust внутри кластера (2 минуты)  │
   │    SLO: P95 < 250ms                   │
   │         P99 < 500ms                   │
   │         errors < 1%                   │
   └────┬──────────────────────────────────┘
        │  SLO ✅ + manual approve
        │
   ┌────▼──────────────────────────────────┐
   │ 5. Canary Deploy      (self-hosted)   │
   │    10% → smoke test → promote 100%    │
   │    init container скачивает Staging   │
   │    модель → Production                │
   └───────────────────────────────────────┘
```

### Установка self-hosted runner

```bash
# Получить токен: GitHub → Settings → Actions → Runners → New self-hosted runner
export GITHUB_REPO="owner/ml-pet-project"
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
bash scripts/setup-github-runner.sh
```

После установки runner появится в `Settings → Actions → Runners` со статусом **Idle**.

---

## 📦 Хранение модели (MinIO + MLflow)

Модель хранится в MinIO (S3-совместимое хранилище), MLflow используется как Registry и для трекинга экспериментов.

```
Обучение
  └── model.export("/tmp/saved_model")
      └── boto3 upload → s3://mlflow-artifacts/artifacts/<run_id>/saved_model/
          └── mlflow.register_model(s3_uri) → mnist-classifier v1
              └── transition_stage → Staging → Production

Деплой
  └── init container в поде
      └── MLflow: get_latest_versions(stage="Staging")
          └── version.source = s3://mlflow-artifacts/...
              └── boto3 download → /model/saved_model/
                  └── FastAPI сервер загружает модель
```

### Работа с моделями вручную

```bash
# Promote последней версии в Staging
make promote-staging

# Promote Staging → Production (с проверкой accuracy ≥ 0.95)
make promote-production

# Посмотреть все версии
source .venv/bin/activate
python3 -c "
import mlflow
mlflow.set_tracking_uri('http://localhost:5000')
client = mlflow.tracking.MlflowClient()
for v in client.search_model_versions(\"name='mnist-classifier'\"):
    print(f'v{v.version}: {v.current_stage} | {v.source}')
"
```

---

## 🐦 Canary Deployment

```
Новый образ задеплоен
        │
   setWeight: 10%  ──── pause 2m
        │
   setWeight: 30%  ──── pause 3m
        │
   setWeight: 50%  ──── pause 5m
        │
   setWeight: 100% ──── ✅ Promotion

   При нарушении SLO → автоматический Rollback ❌
```

```bash
make rollout-status    # наблюдать за прогрессом
make rollout-promote   # ручной promote на следующий шаг
make rollout-abort     # откат
```

---

## 🔍 Мониторинг

| Компонент     | URL                      | Команда              | Credentials          |
|---------------|--------------------------|----------------------|----------------------|
| MinIO Console | http://localhost:9001    | `make port-minio`    | minioadmin/minioadmin123 |
| MLflow UI     | http://localhost:5000    | `make port-mlflow`   | —                    |
| Grafana       | http://localhost:3000    | `make port-grafana`  | admin/admin123       |
| Prometheus    | http://localhost:9090    | `make port-prometheus` | —                  |
| Model API     | http://localhost:8080    | `make port-model`    | —                    |

---

## 📊 API Reference

```bash
# Health check
curl http://localhost:8080/health

# Readiness (возвращает 503 если модель не загружена)
curl http://localhost:8080/ready

# Prediction (28×28 массив пикселей)
curl -X POST http://localhost:8080/predict \
  -H "Content-Type: application/json" \
  -d '{"image": [[0,0,...], ...]}'

# Prometheus метрики
curl http://localhost:8080/metrics
```

**Пример ответа `/predict`:**
```json
{
  "predicted_class": 7,
  "confidence": 0.9842,
  "probabilities": [0.001, 0.002, 0.003, 0.002, 0.001, 0.001, 0.001, 0.984, 0.003, 0.002],
  "model_version": "sha-abc12345",
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 🛠️ Makefile команды

```bash
make setup              # Полный setup кластера с нуля
make train              # Обучить модель (сохраняет в MinIO)
make promote-staging    # Promote последней версии → Staging
make promote-production # Promote Staging → Production (accuracy ≥ 0.95)
make test               # Unit тесты (pytest + coverage)
make test-perf          # Performance тесты (Locust)
make build-minikube     # Собрать образ внутри Minikube
make deploy             # Применить все K8s манифесты
make rollout-status     # Следить за canary rollout
make rollout-promote    # Ручной promote canary
make rollout-abort      # Откатить rollout
make port-minio         # MinIO console → :9001
make port-mlflow        # MLflow UI → :5000
make port-grafana       # Grafana → :3000
make port-model         # Model API → :8080
make clean              # Удалить кластер (minikube delete)
```

---

## 🧱 Стек технологий

| Категория       | Технологии                                      |
|-----------------|-------------------------------------------------|
| ML              | TensorFlow 2.15, Keras, NumPy, scikit-learn     |
| Experiment tracking | MLflow 2.10                                 |
| Artifact storage | MinIO (S3-compatible)                          |
| Serving         | FastAPI, Uvicorn, Pydantic                      |
| Containers      | Docker (multi-stage), Python 3.11               |
| Orchestration   | Kubernetes (Minikube), Argo Rollouts            |
| Service mesh    | Istio 1.24 (VirtualService, DestinationRule)    |
| Monitoring      | Prometheus, Grafana, kube-prometheus-stack      |
| CI/CD           | GitHub Actions (hosted + self-hosted runner)    |
| Testing         | pytest, Locust                                  |
| IaC             | Helm, kubectl, Makefile                         |
