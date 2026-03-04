#!/usr/bin/env bash
# scripts/setup-local.sh
# Полная настройка локального окружения с нуля
# Запуск: bash scripts/setup-local.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%T)] ✅ $*${NC}"; }
info() { echo -e "${BLUE}[$(date +%T)] ℹ️  $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date +%T)] ⚠️  $*${NC}"; }
err()  { echo -e "${RED}[$(date +%T)] ❌ $*${NC}"; exit 1; }

# ── 1. Проверяем зависимости ──────────────────────────────────────
info "Checking dependencies..."

check_tool() {
    command -v "$1" &>/dev/null || err "$1 not found. Install it first."
}

check_tool minikube
check_tool kubectl
check_tool helm
check_tool docker
check_tool python3

log "All dependencies found"

# ── 2. Запускаем minikube ─────────────────────────────────────────
info "Starting Minikube..."

minikube start \
    --driver=docker \
    --cpus=4 \
    --memory=8192 \
    --disk-size=30g \
    --kubernetes-version=v1.35.1 \
    --addons=metrics-server \
    --container-runtime=docker

minikube addons enable metrics-server

log "Minikube started"
kubectl cluster-info

# ── 3. Устанавливаем Istio ────────────────────────────────────────
info "Installing Istio..."

# Istio 1.24 поддерживает k8s до 1.31, для 1.35 используем 1.24+
ISTIO_VERSION="1.24.0"
if ! command -v istioctl &>/dev/null; then
    curl -L https://istio.io/downloadIstio | ISTIO_VERSION=$ISTIO_VERSION sh -
    sudo mv istio-${ISTIO_VERSION}/bin/istioctl /usr/local/bin/
    rm -rf istio-${ISTIO_VERSION}
fi

istioctl install --set profile=demo -y

# Включаем sidecar injection для нашего namespace
kubectl label namespace ml-serving istio-injection=enabled --overwrite 2>/dev/null || true

log "Istio installed"

# ── 4. Устанавливаем Argo Rollouts ────────────────────────────────
info "Installing Argo Rollouts..."

kubectl create namespace argo-rollouts 2>/dev/null || true
kubectl apply -n argo-rollouts \
    -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml

# kubectl plugin
if ! command -v kubectl-argo-rollouts &>/dev/null; then
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    curl -LO "https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-${OS}-amd64"
    chmod +x "kubectl-argo-rollouts-${OS}-amd64"
    sudo mv "kubectl-argo-rollouts-${OS}-amd64" /usr/local/bin/kubectl-argo-rollouts
fi

kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=5m
log "Argo Rollouts installed"

# ── 5. Устанавливаем Prometheus + Grafana ─────────────────────────
info "Installing kube-prometheus-stack..."

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kube-prometheus-stack \
    prometheus-community/kube-prometheus-stack \
    --namespace monitoring --create-namespace \
    --set grafana.adminPassword=admin123 \
    --set prometheus.prometheusSpec.retention=7d \
    --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=10Gi \
    --wait --timeout=10m

log "Prometheus + Grafana installed"

# ── 6. Применяем K8s манифесты ────────────────────────────────────
info "Applying K8s manifests..."

kubectl apply -f k8s/00-namespace.yaml
sleep 2
kubectl apply -f k8s/01-mlflow.yaml
kubectl apply -f k8s/03-services.yaml
kubectl apply -f k8s/04-istio.yaml
kubectl apply -f k8s/06-servicemonitor.yaml

# Ждём MLflow
kubectl -n mlflow rollout status deploy/mlflow --timeout=3m
log "K8s manifests applied"

# ── 7. Билдим Docker образ в minikube ─────────────────────────────
info "Building Docker image in Minikube..."

eval $(minikube docker-env)
docker build . -t ml-model:latest -t ml-model:v1.0.0
eval $(minikube docker-env --unset)

log "Docker image built"

# ── 8. Деплоим модель ─────────────────────────────────────────────
info "Deploying ML model..."

kubectl apply -f k8s/02-rollout.yaml
kubectl apply -f k8s/05-analysis-alerts.yaml

log "Deployment initiated"

# ── 9. Выводим endpoints ──────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
echo -e "${GREEN} 🎉 Setup complete! Useful commands:${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════${NC}"

MINIKUBE_IP=$(minikube ip)

echo ""
echo "📊 Access MLflow UI:"
echo "   kubectl port-forward -n mlflow svc/mlflow 5000:5000"
echo "   → http://localhost:5000"
echo ""
echo "📈 Access Grafana:"
echo "   kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80"
echo "   → http://localhost:3000  (admin / admin123)"
echo ""
echo "🔮 Access ML Model:"
echo "   kubectl port-forward -n ml-serving svc/ml-model-stable 8080:80"
echo "   → http://localhost:8080/docs"
echo ""
echo "🚀 Watch Canary Rollout:"
echo "   kubectl argo-rollouts get rollout ml-model -n ml-serving --watch"
echo ""
echo "🏃 Train model:"
echo "   MLFLOW_TRACKING_URI=http://localhost:5000 python src/model/train.py"
echo ""
echo "🧪 Run tests:"
echo "   pytest tests/unit/ -v"
echo "   locust -f tests/performance/locustfile.py --host=http://localhost:8080"
echo ""
