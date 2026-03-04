#!/usr/bin/env bash
# scripts/start-existing.sh
# Запуск существующего minikube кластера v1.35.1 и установка всего необходимого
# Запуск: bash scripts/start-existing.sh

set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date +%T)] ✅ $*${NC}"; }
info() { echo -e "${BLUE}[$(date +%T)] ℹ️  $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date +%T)] ⚠️  $*${NC}"; }
err()  { echo -e "${RED}[$(date +%T)] ❌ $*${NC}"; exit 1; }

# ── 1. Стартуем существующий кластер ─────────────────────────────
info "Starting existing minikube cluster (v1.35.1)..."
minikube start --kubernetes-version=v1.35.1
kubectl cluster-info
kubectl get nodes
log "Minikube is running"

# ── 2. Включаем нужные addons ────────────────────────────────────
info "Enabling addons..."
minikube addons enable metrics-server
minikube addons enable ingress
log "Addons enabled"

# ── 3. Istio ─────────────────────────────────────────────────────
info "Checking Istio..."
if ! command -v istioctl &>/dev/null; then
    info "Installing Istio 1.24 (совместима с k8s 1.35)..."
    curl -L https://istio.io/downloadIstio | ISTIO_VERSION=1.24.0 sh -
    sudo mv istio-1.24.0/bin/istioctl /usr/local/bin/
    rm -rf istio-1.24.0
fi

if ! kubectl get namespace istio-system &>/dev/null; then
    istioctl install --set profile=demo -y
    log "Istio installed"
else
    warn "Istio already installed — skipping"
fi

kubectl label namespace default istio-injection=enabled --overwrite

# ── 4. Argo Rollouts ─────────────────────────────────────────────
info "Checking Argo Rollouts..."
if ! kubectl get namespace argo-rollouts &>/dev/null; then
    kubectl create namespace argo-rollouts
    kubectl apply -n argo-rollouts \
        -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
    kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=5m
    log "Argo Rollouts installed"
else
    warn "Argo Rollouts already installed — skipping"
fi

if ! command -v kubectl-argo-rollouts &>/dev/null; then
    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
    curl -Lo /tmp/kubectl-argo-rollouts \
        "https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-${OS}-${ARCH}"
    chmod +x /tmp/kubectl-argo-rollouts
    sudo mv /tmp/kubectl-argo-rollouts /usr/local/bin/kubectl-argo-rollouts
    log "kubectl-argo-rollouts plugin installed"
fi

# ── 5. Prometheus + Grafana ──────────────────────────────────────
info "Checking Prometheus stack..."
if ! helm list -n monitoring 2>/dev/null | grep -q kube-prometheus; then
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update
    helm upgrade --install kube-prometheus-stack \
        prometheus-community/kube-prometheus-stack \
        --namespace monitoring --create-namespace \
        --set grafana.adminPassword=admin123 \
        --set prometheus.prometheusSpec.retention=7d \
        --wait --timeout=10m
    log "Prometheus + Grafana installed"
else
    warn "Prometheus stack already installed — skipping"
fi

# ── 6. K8s манифесты ─────────────────────────────────────────────
info "Applying K8s manifests..."
kubectl apply -f k8s/00-namespace.yaml
sleep 2
kubectl apply -f k8s/01-mlflow.yaml
kubectl apply -f k8s/03-services.yaml
kubectl apply -f k8s/04-istio.yaml
kubectl apply -f k8s/06-servicemonitor.yaml
kubectl -n mlflow rollout status deploy/mlflow --timeout=3m
log "Manifests applied"

# ── 7. Билдим образ прямо в minikube ─────────────────────────────
info "Building Docker image inside Minikube..."
eval $(minikube docker-env)
docker build . -t ml-model:latest -t ml-model:v1.0.0
eval $(minikube docker-env --unset)
log "Image built"

# ── 8. Деплоим модель ─────────────────────────────────────────────
info "Deploying ML model..."
kubectl apply -f k8s/02-rollout.yaml
kubectl apply -f k8s/05-analysis-alerts.yaml
log "Model deployed"

# ── Итог ──────────────────────────────────────────────────────────
MINIKUBE_IP=$(minikube ip)
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN} 🎉 Ready! Useful commands:${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo "  make port-mlflow     → http://localhost:5000"
echo "  make port-grafana    → http://localhost:3000 (admin/admin123)"
echo "  make port-model      → http://localhost:8080/docs"
echo "  make rollout-status  → watch canary"
echo "  make train           → train model"
echo "  make test            → unit tests"
echo ""
