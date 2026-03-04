"""
ML Pet Project — Pulumi Infrastructure
=======================================
Manages the full MLOps stack on Minikube:
  MinIO → MLflow → ml-serving (Argo Rollouts canary) → Monitoring

Dependency order:
  Namespaces → MinIO → MLflow → MlServing
                              ↘ Monitoring

Usage:
  cd infra
  python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  pulumi stack init dev
  pulumi config set --secret minio_password minioadmin123
  pulumi up
"""

import pulumi
import pulumi_kubernetes as k8s

import config as cfg_module
from components import (
    Namespaces,
    MinioComponent,
    MlflowComponent,
    MlServingComponent,
    MonitoringComponent,
)

# ── Load typed config ─────────────────────────────────────────────
cfg = cfg_module.load()

# ── Kubernetes provider (uses context from Pulumi.<stack>.yaml) ───
k8s_cfg   = pulumi.Config("kubernetes")
k8s_ctx   = k8s_cfg.get("context") or "minikube"

provider = k8s.Provider(
    "k8s-provider",
    context=k8s_ctx,
    # Suppress "configured" warnings for CRDs not yet registered
    suppress_deprecation_warnings=True,
    enable_server_side_apply=True,
)

provider_opts = pulumi.ResourceOptions(provider=provider)

# ── 1. Namespaces ─────────────────────────────────────────────────
namespaces = Namespaces(
    "namespaces",
    provider=provider,
    opts=provider_opts,
)

ns_deps = [namespaces.ml_serving, namespaces.mlflow, namespaces.minio]

# ── 2. MinIO ──────────────────────────────────────────────────────
minio = MinioComponent(
    "minio",
    cfg=cfg.minio,
    provider=provider,
    namespace_deps=ns_deps,
    opts=provider_opts,
)

# ── 3. MLflow ─────────────────────────────────────────────────────
mlflow = MlflowComponent(
    "mlflow",
    cfg=cfg.mlflow,
    provider=provider,
    minio=minio,
    opts=provider_opts,
)

# ── 4. ML Serving (Rollout + Istio + Services) ────────────────────
ml_serving = MlServingComponent(
    "ml-serving",
    cfg=cfg.model,
    provider=provider,
    mlflow_tracking_uri=mlflow.tracking_uri,
    minio_secret=minio.secret_ml_serving,
    opts=provider_opts,
)

# ── 5. Monitoring (ServiceMonitor + PrometheusRule + Analysis) ────
monitoring = MonitoringComponent(
    "monitoring",
    cfg=cfg.monitoring,
    provider=provider,
    opts=provider_opts,
)

# ── Stack Outputs ─────────────────────────────────────────────────
# Accessible via: pulumi stack output <key>
pulumi.export("k8s_context",       k8s_ctx)
pulumi.export("minio_endpoint",    minio.endpoint)
pulumi.export("minio_bucket",      cfg.minio.bucket)
pulumi.export("mlflow_uri",        mlflow.tracking_uri)
pulumi.export("model_namespace",   cfg.model.namespace)
pulumi.export("model_image",       cfg.model.image)
pulumi.export("model_stage",       cfg.model.stage)

# Port-forward hints printed after deploy
pulumi.export("port_forward_hint", pulumi.Output.concat(
    "\n  MinIO console:  kubectl port-forward -n minio svc/minio 9001:9001",
    "\n  MLflow UI:      kubectl port-forward -n mlflow svc/mlflow 5000:5000",
    "\n  Model API:      kubectl port-forward -n ml-serving svc/ml-model 8080:80",
    "\n  Grafana:        kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80",
))
