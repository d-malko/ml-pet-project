"""
Typed configuration for the ml-pet-project Pulumi stack.
All values come from Pulumi.<stack>.yaml or pulumi config set.
Secrets are fetched with require_secret() — never hardcoded.
"""
from dataclasses import dataclass
import pulumi


@dataclass
class MinioConfig:
    user: str
    password: pulumi.Output  # secret
    storage_size: str
    namespace: str = "minio"
    image: str = "quay.io/minio/minio:RELEASE.2024-01-16T16-07-38Z"
    mc_image: str = "quay.io/minio/mc:RELEASE.2024-01-16T02-44-57Z"
    bucket: str = "mlflow-artifacts"


@dataclass
class MlflowConfig:
    image: str
    storage_size: str
    namespace: str = "mlflow"
    port: int = 5000


@dataclass
class ModelConfig:
    image: str
    replicas: int
    stage: str
    namespace: str = "ml-serving"
    port: int = 8080


@dataclass
class MonitoringConfig:
    prometheus_address: str
    namespace: str = "monitoring"


@dataclass
class StackConfig:
    minio: MinioConfig
    mlflow: MlflowConfig
    model: ModelConfig
    monitoring: MonitoringConfig


def load() -> StackConfig:
    """Load and validate all stack configuration."""
    cfg = pulumi.Config()

    minio = MinioConfig(
        user=cfg.get("minio_user") or "minioadmin",
        password=cfg.require_secret("minio_password"),
        storage_size=cfg.get("minio_storage") or "10Gi",
    )

    mlflow = MlflowConfig(
        image=cfg.get("mlflow_image") or "ghcr.io/mlflow/mlflow:v2.10.2",
        storage_size=cfg.get("mlflow_storage") or "5Gi",
    )

    model = ModelConfig(
        image=cfg.get("model_image") or "ml-model:latest",
        replicas=int(cfg.get("model_replicas") or "4"),
        stage=cfg.get("model_stage") or "Staging",
    )

    monitoring = MonitoringConfig(
        prometheus_address=(
            cfg.get("prometheus_address")
            or "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
        ),
    )

    return StackConfig(
        minio=minio,
        mlflow=mlflow,
        model=model,
        monitoring=monitoring,
    )
