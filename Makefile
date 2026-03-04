.PHONY: help train test test-perf build-minikube deploy rollout-status \
        rollout-promote rollout-abort rollout-update port-mlflow port-grafana \
        port-model port-minio setup clean promote-staging promote-production

IMAGE_NAME     ?= ml-model
IMAGE_TAG      ?= latest
NAMESPACE      ?= ml-serving
MLFLOW_URI     ?= http://localhost:5000
MINIO_URI      ?= http://localhost:9000
SERVER_HOST    ?= http://localhost:8080
LOCUST_USERS   ?= 20
LOCUST_TIME    ?= 1m

help:
	@echo ""
	@echo "  ML Pet Project — Makefile Commands"
	@echo "  ──────────────────────────────────────────"
	@echo "  make setup              — Full local setup"
	@echo "  make train              — Train model (saves to MinIO)"
	@echo "  make promote-staging    — Promote latest model to Staging"
	@echo "  make promote-production — Promote Staging model to Production"
	@echo "  make test               — Unit tests"
	@echo "  make test-perf          — Performance tests"
	@echo "  make build-minikube     — Build Docker image in Minikube"
	@echo "  make deploy             — Apply all K8s manifests"
	@echo "  make rollout-status     — Watch canary rollout"
	@echo "  make rollout-promote    — Manually promote canary"
	@echo "  make port-minio         — MinIO console → http://localhost:9001"
	@echo "  make port-mlflow        — MLflow UI → http://localhost:5000"
	@echo "  make port-grafana       — Grafana → http://localhost:3000"
	@echo "  make port-model         — ML Model API → http://localhost:8080"
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────
setup:
	bash scripts/setup-local.sh

# ── Training ──────────────────────────────────────────────────────
train:
	@echo "🚂 Training model..."
	. .venv/bin/activate && \
	MLFLOW_TRACKING_URI=$(MLFLOW_URI) \
	MLFLOW_S3_ENDPOINT_URL=$(MINIO_URI) \
	AWS_ACCESS_KEY_ID=minioadmin \
	AWS_SECRET_ACCESS_KEY=minioadmin123 \
	python3 src/model/train.py
	@echo "✅ Model uploaded to MinIO, metrics in MLflow."

promote-staging:
	@echo "🔀 Promoting latest model to Staging..."
	. .venv/bin/activate && MLFLOW_TRACKING_URI=$(MLFLOW_URI) python3 scripts/promote_staging.py

promote-production:
	@echo "🚀 Promoting Staging model to Production..."
	. .venv/bin/activate && MLFLOW_TRACKING_URI=$(MLFLOW_URI) python3 scripts/promote_production.py

# ── Tests ─────────────────────────────────────────────────────────
test:
	@echo "🧪 Running unit tests..."
	. .venv/bin/activate && pytest tests/unit/ \
		--cov=src \
		--cov-report=term-missing \
		--cov-fail-under=75 \
		-v

test-perf:
	@echo "🚀 Running performance tests..."
	mkdir -p results
	. .venv/bin/activate && locust -f tests/performance/locustfile.py \
		--host=$(SERVER_HOST) \
		--users=$(LOCUST_USERS) \
		--spawn-rate=5 \
		--run-time=$(LOCUST_TIME) \
		--headless \
		--csv=results/perf \
		--html=results/perf_report.html
	@echo "✅ Report: results/perf_report.html"

# ── Docker ────────────────────────────────────────────────────────
build-minikube:
	@echo "🐳 Building image inside Minikube..."
	eval $$(minikube docker-env) && \
		docker build . \
			-t $(IMAGE_NAME):$(IMAGE_TAG) \
			-t $(IMAGE_NAME):latest \
			--build-arg GIT_COMMIT=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
	@echo "✅ Image available in Minikube"

# ── Deploy ────────────────────────────────────────────────────────
deploy:
	@echo "🚢 Deploying to Minikube..."
	kubectl apply -f k8s/00-namespace.yaml
	kubectl apply -f k8s/00-minio.yaml
	@echo "Waiting for MinIO..."
	kubectl -n minio rollout status deploy/minio --timeout=3m
	kubectl apply -f k8s/01-mlflow.yaml
	@echo "Waiting for MLflow..."
	kubectl -n mlflow rollout status deploy/mlflow --timeout=3m
	kubectl apply -f k8s/03-services.yaml
	kubectl apply -f k8s/04-istio.yaml
	kubectl apply -f k8s/02-rollout.yaml
	kubectl apply -f k8s/05-analysis-alerts.yaml
	kubectl apply -f k8s/06-servicemonitor.yaml
	@echo "✅ Deployed!"

# ── Rollout ───────────────────────────────────────────────────────
rollout-status:
	kubectl-argo-rollouts get rollout ml-model -n $(NAMESPACE) --watch

rollout-promote:
	kubectl-argo-rollouts promote ml-model -n $(NAMESPACE)

rollout-abort:
	kubectl-argo-rollouts abort ml-model -n $(NAMESPACE)

rollout-update:
	kubectl-argo-rollouts set image ml-model \
		ml-model=$(IMAGE_NAME):$(IMAGE_TAG) -n $(NAMESPACE)

# ── Port-forwards ─────────────────────────────────────────────────
port-minio:
	@echo "🔗 MinIO console: http://localhost:9001 (minioadmin/minioadmin123)"
	kubectl port-forward -n minio svc/minio 9000:9000 9001:9001

port-mlflow:
	@echo "🔗 MLflow: http://localhost:5000"
	kubectl port-forward -n mlflow svc/mlflow 5000:5000

port-grafana:
	@echo "🔗 Grafana: http://localhost:3000 (admin/admin123)"
	kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80

port-prometheus:
	@echo "🔗 Prometheus: http://localhost:9090"
	kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090

port-model:
	@echo "🔗 Model API: http://localhost:8080"
	kubectl port-forward -n $(NAMESPACE) svc/ml-model 8080:80

# ── Cleanup ───────────────────────────────────────────────────────
clean:
	minikube delete
	@echo "✅ Cluster deleted"
