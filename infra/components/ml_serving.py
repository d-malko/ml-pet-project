"""
ML Serving component.
Deploys the ml-serving stack:
  - Services (stable / canary / public)
  - Istio Gateway + VirtualService + DestinationRule
  - Argo Rollouts Canary with init container (downloads model from MinIO)
"""
import pulumi
import pulumi_kubernetes as k8s

from config import ModelConfig
from components.minio import MINIO_ENDPOINT

# Model loader script — downloads model from MinIO using MLflow Registry
_MODEL_LOADER_SCRIPT = """\
set -e
pip install mlflow boto3 --quiet

python3 - << 'PYEOF'
import mlflow, os, boto3, shutil
from botocore.config import Config

tracking_uri = os.environ["MLFLOW_TRACKING_URI"]
s3_endpoint  = os.environ["MLFLOW_S3_ENDPOINT_URL"]
access_key   = os.environ["AWS_ACCESS_KEY_ID"]
secret_key   = os.environ["AWS_SECRET_ACCESS_KEY"]
stage        = os.environ.get("MODEL_STAGE", "Staging")

mlflow.set_tracking_uri(tracking_uri)
client = mlflow.tracking.MlflowClient()

versions = client.get_latest_versions("mnist-classifier", stages=[stage])
if not versions:
    raise RuntimeError(f"No model in stage '{stage}'")

version = versions[0]
print(f"Downloading mnist-classifier v{version.version} ({stage})")
print(f"Source: {version.source}")

s3_uri = version.source.replace("s3://", "")
bucket, prefix = s3_uri.split("/", 1)

s3 = boto3.client(
    "s3",
    endpoint_url=s3_endpoint,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)

paginator = s3.get_paginator("list_objects_v2")
files = [
    obj["Key"]
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
    for obj in page.get("Contents", [])
]

if not files:
    raise RuntimeError(f"No files at s3://{bucket}/{prefix}")

for key in files:
    relative  = os.path.relpath(key, prefix)
    local_path = os.path.join("/model/saved_model", relative)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    s3.download_file(bucket, key, local_path)

print(f"Model ready at /model/saved_model ({len(files)} files)")
PYEOF
"""


def _secret_env(name: str, key: str, optional: bool = False) -> k8s.core.v1.EnvVarArgs:
    """Helper: EnvVar sourced from minio-credentials secret."""
    return k8s.core.v1.EnvVarArgs(
        name=name,
        value_from=k8s.core.v1.EnvVarSourceArgs(
            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                name="minio-credentials",
                key=key,
                optional=optional,
            ),
        ),
    )


def _s3_env(
    mlflow_uri: str,
    model_stage: str,
    optional: bool = False,
) -> list:
    """Common S3 + MLflow env vars for init container and main container."""
    return [
        k8s.core.v1.EnvVarArgs(name="MLFLOW_TRACKING_URI", value=mlflow_uri),
        k8s.core.v1.EnvVarArgs(name="MLFLOW_S3_ENDPOINT_URL", value=MINIO_ENDPOINT),
        k8s.core.v1.EnvVarArgs(name="AWS_DEFAULT_REGION", value="us-east-1"),
        k8s.core.v1.EnvVarArgs(name="MODEL_STAGE", value=model_stage),
        _secret_env("AWS_ACCESS_KEY_ID",     "root-user",     optional=optional),
        _secret_env("AWS_SECRET_ACCESS_KEY", "root-password", optional=optional),
    ]


class MlServingComponent(pulumi.ComponentResource):

    def __init__(
        self,
        name: str,
        cfg: ModelConfig,
        provider: k8s.Provider,
        mlflow_tracking_uri: str,
        minio_secret: k8s.core.v1.Secret,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("ml-pet-project:infra:MlServingComponent", name, {}, opts)

        base_opts = pulumi.ResourceOptions(
            parent=self,
            provider=provider,
            depends_on=[minio_secret],
        )

        # ── Services ──────────────────────────────────────────────
        svc_spec = k8s.core.v1.ServiceSpecArgs(
            selector={"app": "ml-model"},
            ports=[k8s.core.v1.ServicePortArgs(
                name="http", port=80, target_port=cfg.port,
            )],
            type="ClusterIP",
        )

        self.svc_stable = k8s.core.v1.Service(
            "svc-stable",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-stable",
                namespace=cfg.namespace,
                labels={"app": "ml-model"},
            ),
            spec=svc_spec,
            opts=base_opts,
        )

        self.svc_canary = k8s.core.v1.Service(
            "svc-canary",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-canary",
                namespace=cfg.namespace,
                labels={"app": "ml-model"},
            ),
            spec=svc_spec,
            opts=base_opts,
        )

        self.svc_public = k8s.core.v1.Service(
            "svc-public",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model",
                namespace=cfg.namespace,
                labels={"app": "ml-model"},
                annotations={
                    "prometheus.io/scrape": "true",
                    "prometheus.io/port":   str(cfg.port),
                    "prometheus.io/path":   "/metrics",
                },
            ),
            spec=svc_spec,
            opts=base_opts,
        )

        # ── Istio Gateway ─────────────────────────────────────────
        self.gateway = k8s.apiextensions.CustomResource(
            "istio-gateway",
            api_version="networking.istio.io/v1beta1",
            kind="Gateway",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-gateway",
                namespace=cfg.namespace,
            ),
            spec={
                "selector": {"istio": "ingressgateway"},
                "servers": [{
                    "port": {"number": 80, "name": "http", "protocol": "HTTP"},
                    "hosts": ["*"],
                }],
            },
            opts=base_opts,
        )

        fqdn = f"ml-model.{cfg.namespace}.svc.cluster.local"

        # ── Istio DestinationRule ─────────────────────────────────
        self.destination_rule = k8s.apiextensions.CustomResource(
            "istio-destination-rule",
            api_version="networking.istio.io/v1beta1",
            kind="DestinationRule",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-dr",
                namespace=cfg.namespace,
            ),
            spec={
                "host": fqdn,
                "trafficPolicy": {
                    "connectionPool": {
                        "http": {
                            "http1MaxPendingRequests": 100,
                            "http2MaxRequests": 1000,
                        }
                    },
                    "outlierDetection": {
                        "consecutive5xxErrors": 5,
                        "interval": "30s",
                        "baseEjectionTime": "1m",
                    },
                },
                "subsets": [
                    {"name": "stable", "labels": {"app": "ml-model"}},
                    {"name": "canary", "labels": {"app": "ml-model"}},
                ],
            },
            opts=base_opts,
        )

        # ── Istio VirtualService ──────────────────────────────────
        self.virtual_service = k8s.apiextensions.CustomResource(
            "istio-virtual-service",
            api_version="networking.istio.io/v1beta1",
            kind="VirtualService",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-vs",
                namespace=cfg.namespace,
            ),
            spec={
                "hosts": [fqdn],
                "gateways": [f"{cfg.namespace}/ml-model-gateway", "mesh"],
                "http": [{
                    "name": "primary",
                    "route": [
                        {
                            "destination": {
                                "host": fqdn,
                                "subset": "stable",
                                "port": {"number": 80},
                            },
                            "weight": 100,
                        },
                        {
                            "destination": {
                                "host": fqdn,
                                "subset": "canary",
                                "port": {"number": 80},
                            },
                            "weight": 0,
                        },
                    ],
                }],
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[self.gateway, self.destination_rule],
            ),
        )

        # ── Argo Rollout ──────────────────────────────────────────
        self.rollout = k8s.apiextensions.CustomResource(
            "ml-model-rollout",
            api_version="argoproj.io/v1alpha1",
            kind="Rollout",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model",
                namespace=cfg.namespace,
                labels={"app": "ml-model"},
            ),
            spec={
                "replicas": cfg.replicas,
                "revisionHistoryLimit": 3,
                "selector": {"matchLabels": {"app": "ml-model"}},
                "template": {
                    "metadata": {
                        "labels": {"app": "ml-model"},
                        "annotations": {
                            "prometheus.io/scrape": "true",
                            "prometheus.io/port":   str(cfg.port),
                            "prometheus.io/path":   "/metrics",
                            "proxy.istio.io/config": '{"holdApplicationUntilProxyStarts": true}',
                        },
                    },
                    "spec": {
                        "initContainers": [{
                            "name":    "model-loader",
                            "image":   "python:3.11-slim",
                            "command": ["/bin/sh", "-c", _MODEL_LOADER_SCRIPT],
                            "env": [
                                {"name": "MLFLOW_TRACKING_URI",  "value": mlflow_tracking_uri},
                                {"name": "MLFLOW_S3_ENDPOINT_URL", "value": MINIO_ENDPOINT},
                                {"name": "AWS_DEFAULT_REGION",   "value": "us-east-1"},
                                {"name": "MODEL_STAGE",          "value": cfg.stage},
                                {
                                    "name": "AWS_ACCESS_KEY_ID",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "minio-credentials",
                                        "key": "root-user",
                                        "optional": True,
                                    }},
                                },
                                {
                                    "name": "AWS_SECRET_ACCESS_KEY",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "minio-credentials",
                                        "key": "root-password",
                                        "optional": True,
                                    }},
                                },
                            ],
                            "volumeMounts": [{"name": "model-volume", "mountPath": "/model"}],
                        }],
                        "containers": [{
                            "name":             "ml-model",
                            "image":            cfg.image,
                            "imagePullPolicy":  "Never",
                            "ports": [{"name": "http", "containerPort": cfg.port}],
                            "env": [
                                {"name": "MODEL_PATH",           "value": "/model/saved_model"},
                                {"name": "LOG_FORMAT",           "value": "json"},
                                {"name": "MLFLOW_TRACKING_URI",  "value": mlflow_tracking_uri},
                                {"name": "MLFLOW_S3_ENDPOINT_URL", "value": MINIO_ENDPOINT},
                                {"name": "AWS_DEFAULT_REGION",   "value": "us-east-1"},
                                {
                                    "name": "AWS_ACCESS_KEY_ID",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "minio-credentials",
                                        "key": "root-user",
                                        "optional": True,
                                    }},
                                },
                                {
                                    "name": "AWS_SECRET_ACCESS_KEY",
                                    "valueFrom": {"secretKeyRef": {
                                        "name": "minio-credentials",
                                        "key": "root-password",
                                        "optional": True,
                                    }},
                                },
                            ],
                            "resources": {
                                "requests": {"memory": "512Mi", "cpu": "250m"},
                                "limits":   {"memory": "1Gi",   "cpu": "1000m"},
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/health", "port": cfg.port},
                                "initialDelaySeconds": 30,
                                "periodSeconds": 15,
                                "failureThreshold": 3,
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "/health", "port": cfg.port},
                                "initialDelaySeconds": 15,
                                "periodSeconds": 10,
                                "failureThreshold": 3,
                            },
                            "volumeMounts": [{"name": "model-volume", "mountPath": "/model"}],
                        }],
                        "volumes": [{"name": "model-volume", "emptyDir": {}}],
                    },
                },
                "strategy": {
                    "canary": {
                        "trafficRouting": {
                            "istio": {
                                "virtualService": {
                                    "name":   "ml-model-vs",
                                    "routes": ["primary"],
                                },
                                "destinationRule": {
                                    "name":               "ml-model-dr",
                                    "canarySubsetName":   "canary",
                                    "stableSubsetName":   "stable",
                                },
                            }
                        },
                        "steps": [
                            {"setWeight": 10},
                            {"pause": {"duration": "2m"}},
                            {"setWeight": 30},
                            {"pause": {"duration": "3m"}},
                            {"setWeight": 50},
                            {"pause": {"duration": "5m"}},
                            {"setWeight": 100},
                        ],
                    }
                },
            },
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[
                    self.svc_stable,
                    self.svc_canary,
                    self.virtual_service,
                    self.destination_rule,
                    minio_secret,
                ],
            ),
        )

        self.register_outputs({
            "namespace":   cfg.namespace,
            "model_image": cfg.image,
        })
