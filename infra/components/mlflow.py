"""
MLflow component.
Deploys MLflow tracking server with:
  - SQLite backend store on PVC
  - MinIO (S3) as artifact store
  - --serve-artifacts flag for HTTP artifact proxy
"""
import pulumi
import pulumi_kubernetes as k8s

from config import MlflowConfig
from components.minio import MINIO_ENDPOINT


class MlflowComponent(pulumi.ComponentResource):

    def __init__(
        self,
        name: str,
        cfg: MlflowConfig,
        provider: k8s.Provider,
        minio: "MinioComponent",  # noqa: F821
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("ml-pet-project:infra:MlflowComponent", name, {}, opts)

        # MLflow must start after MinIO bucket is created
        base_opts = pulumi.ResourceOptions(
            parent=self,
            provider=provider,
            depends_on=[minio.setup_job],
        )

        # ── PVC for SQLite backend ────────────────────────────────
        self.pvc = k8s.core.v1.PersistentVolumeClaim(
            "mlflow-pvc",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="mlflow-pvc",
                namespace=cfg.namespace,
            ),
            spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
                access_modes=["ReadWriteOnce"],
                resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                    requests={"storage": cfg.storage_size},
                ),
                storage_class_name="standard",
            ),
            opts=base_opts,
        )

        # ── Deployment ────────────────────────────────────────────
        self.deployment = k8s.apps.v1.Deployment(
            "mlflow-deployment",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="mlflow",
                namespace=cfg.namespace,
                labels={"app": "mlflow"},
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=1,
                selector=k8s.meta.v1.LabelSelectorArgs(
                    match_labels={"app": "mlflow"},
                ),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(
                        labels={"app": "mlflow"},
                    ),
                    spec=k8s.core.v1.PodSpecArgs(
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="mlflow",
                                image=cfg.image,
                                command=[
                                    "mlflow", "server",
                                    "--host=0.0.0.0",
                                    f"--port={cfg.port}",
                                    "--backend-store-uri=sqlite:///mlflow/mlflow.db",
                                    "--default-artifact-root=s3://mlflow-artifacts/",
                                    "--serve-artifacts",
                                ],
                                ports=[
                                    k8s.core.v1.ContainerPortArgs(
                                        container_port=cfg.port,
                                    )
                                ],
                                env=[
                                    k8s.core.v1.EnvVarArgs(
                                        name="MLFLOW_S3_ENDPOINT_URL",
                                        value=MINIO_ENDPOINT,
                                    ),
                                    k8s.core.v1.EnvVarArgs(
                                        name="AWS_DEFAULT_REGION",
                                        value="us-east-1",
                                    ),
                                    # Credentials from secret (same secret as MinIO)
                                    k8s.core.v1.EnvVarArgs(
                                        name="AWS_ACCESS_KEY_ID",
                                        value_from=k8s.core.v1.EnvVarSourceArgs(
                                            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                                name="minio-credentials",
                                                key="root-user",
                                            ),
                                        ),
                                    ),
                                    k8s.core.v1.EnvVarArgs(
                                        name="AWS_SECRET_ACCESS_KEY",
                                        value_from=k8s.core.v1.EnvVarSourceArgs(
                                            secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                                                name="minio-credentials",
                                                key="root-password",
                                            ),
                                        ),
                                    ),
                                ],
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"memory": "512Mi", "cpu": "100m"},
                                    limits={"memory": "1Gi",   "cpu": "500m"},
                                ),
                                volume_mounts=[
                                    k8s.core.v1.VolumeMountArgs(
                                        name="mlflow-storage",
                                        mount_path="/mlflow",
                                    )
                                ],
                            )
                        ],
                        volumes=[
                            k8s.core.v1.VolumeArgs(
                                name="mlflow-storage",
                                persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                    claim_name="mlflow-pvc",
                                ),
                            )
                        ],
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[self.pvc, minio.secret_mlflow],
            ),
        )

        # ── Service ───────────────────────────────────────────────
        self.service = k8s.core.v1.Service(
            "mlflow-service",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="mlflow",
                namespace=cfg.namespace,
            ),
            spec=k8s.core.v1.ServiceSpecArgs(
                selector={"app": "mlflow"},
                ports=[
                    k8s.core.v1.ServicePortArgs(
                        port=cfg.port,
                        target_port=cfg.port,
                    )
                ],
                type="ClusterIP",
            ),
            opts=base_opts,
        )

        # Internal cluster URI used by training jobs and init containers
        self.tracking_uri = (
            f"http://mlflow.{cfg.namespace}.svc.cluster.local:{cfg.port}"
        )

        self.register_outputs({"tracking_uri": self.tracking_uri})
