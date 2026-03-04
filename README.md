# 🤖 ML Pet Project — MNIST Classifier

A complete MLOps pet project: CNN model training → MLflow Registry → MinIO (S3) → Docker → Minikube + Istio → Canary Deployment → CI/CD via GitHub Actions with a self-hosted runner.

## 🏗️ Architecture

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

## 📁 Project Structure

```
ml-pet-project/
├── src/
│   ├── model/
│   │   ├── train.py                   # CNN training, upload to MinIO
│   │   └── predict.py                 # Inference (SavedModel + keras)
│   ├── server/
│   │   └── app.py                     # FastAPI: /predict /health /metrics
│   └── monitoring/
│       └── metrics.py                 # Prometheus metrics
├── tests/
│   ├── unit/
│   │   └── test_model.py              # pytest, coverage ≥ 75%
│   └── performance/
│       └── locustfile.py              # Locust, SLO validation
├── k8s/
│   ├── 00-namespace.yaml              # Namespaces + Istio injection
│   ├── 00-minio.yaml                  # MinIO: Deployment + PVC + setup Job
│   ├── 01-mlflow.yaml                 # MLflow: SQLite backend + S3 artifacts
│   ├── 02-rollout.yaml                # Argo Rollouts canary + init container
│   ├── 03-services.yaml               # Services (stable/canary) + secrets
│   ├── 04-istio.yaml                  # VirtualService + DestinationRule
│   ├── 05-analysis-alerts.yaml        # AnalysisTemplate + PrometheusRule
│   ├── 06-servicemonitor.yaml         # Prometheus ServiceMonitor
│   ├── 07-training-job.yaml           # K8s Job for in-cluster training
│   └── 08-perf-test-job.yaml          # K8s Job for Locust (SLO check)
├── scripts/
│   ├── setup-local.sh                 # Full cluster setup from scratch
│   ├── setup-github-runner.sh         # Install self-hosted GitHub runner
│   ├── promote_staging.py             # Promote model → Staging
│   └── promote_production.py          # Promote model → Production
├── .github/
│   └── workflows/
│       └── ci-cd.yml                  # 5-stage CI/CD pipeline
├── notebooks/
│   └── train_and_explore.ipynb        # Training + visualization
├── Dockerfile                         # Inference image (multi-stage)
├── Dockerfile.train                   # Training image (in-cluster training)
├── Makefile                           # Local development commands
├── requirements.txt                   # Python dependencies
└── pyproject.toml                     # pytest config
```

## 🚀 Quick Start

### Prerequisites

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

### 1. Start the cluster

```bash
git clone <repo> && cd ml-pet-project

# Full setup from scratch (minikube + istio + argo rollouts + monitoring)
bash scripts/setup-local.sh
```

### 2. Deploy infrastructure

```bash
# MinIO + MLflow + K8s manifests
make deploy
```

### 3. Set up Python environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install setuptools wheel
pip install -r requirements.txt
```

### 4. Train and deploy the model

```bash
# Open access to services
make port-minio &    # MinIO console: http://localhost:9001
make port-mlflow &   # MLflow UI:     http://localhost:5000

# Train — model is saved to MinIO and registered in MLflow
make train

# Promote to Production
make promote-staging
make promote-production

# Build inference image and deploy
# (init container will download the model from MinIO automatically)
make build-minikube IMAGE_TAG=v1
kubectl-argo-rollouts set image ml-model ml-model=ml-model:v1 -n ml-serving

# Watch the rollout
make rollout-status
```

---

## 🔄 CI/CD Pipeline

The pipeline consists of 5 jobs. Jobs 1–2 run on **GitHub hosted runners**, jobs 3–5 run on a **self-hosted runner** (your local machine with Minikube).

```
push → main
        │
   ┌────▼──────────┐
   │ 1. Unit Tests │  github-hosted ── pytest, coverage ≥ 75%
   └────┬──────────┘
        │
   ┌────▼──────────┐
   │ 2. Build      │  github-hosted ── inference + training images
   │    Images     │                   security scan (Trivy)
   └────┬──────────┘
        │  images saved as GitHub Artifacts
        │
   ┌────▼──────────────────────────────────┐
   │ 3. Train in Cluster   (self-hosted)   │
   │    kubectl apply 07-training-job.yaml │
   │    wait for completion (timeout 30m)  │
   │    model → MinIO → MLflow → Staging   │
   └────┬──────────────────────────────────┘
        │
   ┌────▼──────────────────────────────────┐
   │ 4. Perf Tests         (self-hosted)   │
   │    kubectl apply 08-perf-test-job     │
   │    Locust inside cluster (2 minutes)  │
   │    SLO: P95 < 250ms                   │
   │         P99 < 500ms                   │
   │         errors < 1%                   │
   └────┬──────────────────────────────────┘
        │  SLO ✅ + manual approve
        │
   ┌────▼──────────────────────────────────┐
   │ 5. Canary Deploy      (self-hosted)   │
   │    10% → smoke test → promote 100%    │
   │    init container downloads Staging   │
   │    model → Production                 │
   └───────────────────────────────────────┘
```

### Installing the self-hosted runner

```bash
# Get token: GitHub → Settings → Actions → Runners → New self-hosted runner
# Use a Classic token (not fine-grained) with scopes: repo + workflow
export GITHUB_REPO="owner/ml-pet-project"
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
bash scripts/setup-github-runner.sh
```

After installation the runner will appear in `Settings → Actions → Runners` with status **Idle**.

---

## 📦 Model Storage (MinIO + MLflow)

The model is stored in MinIO (S3-compatible storage). MLflow is used as the Model Registry and for experiment tracking.

```
Training
  └── model.export("/tmp/saved_model")
      └── boto3 upload → s3://mlflow-artifacts/artifacts/<run_id>/saved_model/
          └── mlflow.register_model(s3_uri) → mnist-classifier v1
              └── transition_stage → Staging → Production

Deployment
  └── init container in pod
      └── MLflow: get_latest_versions(stage="Staging")
          └── version.source = s3://mlflow-artifacts/...
              └── boto3 download → /model/saved_model/
                  └── FastAPI server loads the model
```

### Managing models manually

```bash
# Promote the latest version to Staging
make promote-staging

# Promote Staging → Production (validates accuracy ≥ 0.95)
make promote-production

# List all versions
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
New image deployed
        │
   setWeight: 10%  ──── pause 2m
        │
   setWeight: 30%  ──── pause 3m
        │
   setWeight: 50%  ──── pause 5m
        │
   setWeight: 100% ──── ✅ Promotion

   SLO violated → automatic Rollback ❌
```

```bash
make rollout-status    # watch rollout progress
make rollout-promote   # manually promote to next step
make rollout-abort     # abort and rollback
```

---

## 🔍 Monitoring

| Component     | URL                        | Command                | Credentials              |
|---------------|----------------------------|------------------------|--------------------------|
| MinIO Console | http://localhost:9001      | `make port-minio`      | minioadmin/minioadmin123 |
| MLflow UI     | http://localhost:5000      | `make port-mlflow`     | —                        |
| Grafana       | http://localhost:3000      | `make port-grafana`    | admin/admin123           |
| Prometheus    | http://localhost:9090      | `make port-prometheus` | —                        |
| Model API     | http://localhost:8080/docs | `make port-model`      | —                        |

---

## 📊 API Reference

```bash
# Health check
curl http://localhost:8080/health

# Readiness (returns 503 if model is not loaded)
curl http://localhost:8080/ready

# Prediction (28×28 pixel array)
curl -X POST http://localhost:8080/predict \
  -H "Content-Type: application/json" \
  -d '{"image": [[0,0,...], ...]}'

# Prometheus metrics
curl http://localhost:8080/metrics
```

**Example `/predict` response:**
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

## 🛠️ Makefile Commands

```bash
make setup              # Full cluster setup from scratch
make train              # Train model (saves to MinIO)
make promote-staging    # Promote latest version → Staging
make promote-production # Promote Staging → Production (accuracy ≥ 0.95)
make test               # Unit tests (pytest + coverage)
make test-perf          # Performance tests (Locust)
make build-minikube     # Build image inside Minikube
make deploy             # Apply all K8s manifests
make rollout-status     # Watch canary rollout
make rollout-promote    # Manually promote canary
make rollout-abort      # Abort and rollback rollout
make port-minio         # MinIO console → :9001
make port-mlflow        # MLflow UI → :5000
make port-grafana       # Grafana → :3000
make port-model         # Model API → :8080
make clean              # Delete cluster (minikube delete)
```

---

## 🧱 Tech Stack

| Category            | Technologies                                    |
|---------------------|-------------------------------------------------|
| ML                  | TensorFlow 2.15, Keras, NumPy, scikit-learn     |
| Experiment tracking | MLflow 2.10                                     |
| Artifact storage    | MinIO (S3-compatible)                           |
| Serving             | FastAPI, Uvicorn, Pydantic                      |
| Containers          | Docker (multi-stage), Python 3.11               |
| Orchestration       | Kubernetes (Minikube), Argo Rollouts            |
| Service mesh        | Istio 1.24 (VirtualService, DestinationRule)    |
| Monitoring          | Prometheus, Grafana, kube-prometheus-stack      |
| CI/CD               | GitHub Actions (hosted + self-hosted runner)    |
| Testing             | pytest, Locust                                  |
| IaC                 | Pulumi, Helm, kubectl, Makefile                 |
