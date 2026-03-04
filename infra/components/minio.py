"""
MinIO component.
Deploys MinIO S3-compatible object storage with:
  - Credentials stored as Pulumi secret (not in plain YAML)
  - PVC for persistent storage
  - Setup Job that creates mlflow-artifacts bucket
  - Credentials Secret replicated to mlflow + ml-serving namespaces
"""
import pulumi
import pulumi_kubernetes as k8s

from config import MinioConfig


# Internal cluster endpoint used by other components
MINIO_ENDPOINT = "http://minio.minio.svc.cluster.local:9000"


class MinioComponent(pulumi.ComponentResource):

    def __init__(
        self,
        name: str,
        cfg: MinioConfig,
        provider: k8s.Provider,
        namespace_deps: list,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("ml-pet-project:infra:MinioComponent", name, {}, opts)

        child_opts = pulumi.ResourceOptions(
            parent=self,
            provider=provider,
            depends_on=namespace_deps,
        )

        # ── Credentials secret (minio namespace) ─────────────────
        self.secret = self._make_secret("minio", cfg, child_opts)

        # ── Credentials replicated to mlflow + ml-serving ─────────
        self.secret_mlflow = self._make_secret(
            "mlflow",
            cfg,
            pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=namespace_deps,
            ),
        )
        self.secret_ml_serving = self._make_secret(
            "ml-serving",
            cfg,
            pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=namespace_deps,
            ),
        )

        # ── PersistentVolumeClaim ─────────────────────────────────
        self.pvc = k8s.core.v1.PersistentVolumeClaim(
            "minio-pvc",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="minio-pvc",
                namespace=cfg.namespace,
            ),
            spec=k8s.core.v1.PersistentVolumeClaimSpecArgs(
                access_modes=["ReadWriteOnce"],
                resources=k8s.core.v1.VolumeResourceRequirementsArgs(
                    requests={"storage": cfg.storage_size},
                ),
                storage_class_name="standard",
            ),
            opts=child_opts,
        )

        # ── Deployment ────────────────────────────────────────────
        self.deployment = k8s.apps.v1.Deployment(
            "minio-deployment",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="minio",
                namespace=cfg.namespace,
                labels={"app": "minio"},
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=1,
                selector=k8s.meta.v1.LabelSelectorArgs(
                    match_labels={"app": "minio"},
                ),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(
                        labels={"app": "minio"},
                    ),
                    spec=k8s.core.v1.PodSpecArgs(
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="minio",
                                image=cfg.image,
                                command=[
                                    "minio", "server", "/data",
                                    "--console-address", ":9001",
                                ],
                                ports=[
                                    k8s.core.v1.ContainerPortArgs(name="api", container_port=9000),
                                    k8s.core.v1.ContainerPortArgs(name="console", container_port=9001),
                                ],
                                env=self._minio_env(cfg.namespace),
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"memory": "256Mi", "cpu": "100m"},
                                    limits={"memory": "1Gi", "cpu": "500m"},
                                ),
                                liveness_probe=k8s.core.v1.ProbeArgs(
                                    http_get=k8s.core.v1.HTTPGetActionArgs(
                                        path="/minio/health/live",
                                        port=9000,
                                    ),
                                    initial_delay_seconds=30,
                                    period_seconds=20,
                                ),
                                readiness_probe=k8s.core.v1.ProbeArgs(
                                    http_get=k8s.core.v1.HTTPGetActionArgs(
                                        path="/minio/health/ready",
                                        port=9000,
                                    ),
                                    initial_delay_seconds=10,
                                    period_seconds=10,
                                ),
                                volume_mounts=[
                                    k8s.core.v1.VolumeMountArgs(
                                        name="minio-storage",
                                        mount_path="/data",
                                    )
                                ],
                            )
                        ],
                        volumes=[
                            k8s.core.v1.VolumeArgs(
                                name="minio-storage",
                                persistent_volume_claim=k8s.core.v1.PersistentVolumeClaimVolumeSourceArgs(
                                    claim_name="minio-pvc",
                                ),
                            )
                        ],
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[self.pvc, self.secret],
            ),
        )

        # ── Service ───────────────────────────────────────────────
        self.service = k8s.core.v1.Service(
            "minio-service",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="minio",
                namespace=cfg.namespace,
            ),
            spec=k8s.core.v1.ServiceSpecArgs(
                selector={"app": "minio"},
                ports=[
                    k8s.core.v1.ServicePortArgs(name="api",     port=9000, target_port=9000),
                    k8s.core.v1.ServicePortArgs(name="console", port=9001, target_port=9001),
                ],
            ),
            opts=child_opts,
        )

        # ── Setup Job: creates mlflow-artifacts bucket ────────────
        self.setup_job = k8s.batch.v1.Job(
            "minio-setup-job",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="minio-setup",
                namespace=cfg.namespace,
                annotations={
                    # Re-run on every pulumi up if bucket setup changes
                    "pulumi.com/replaceOnChanges": "spec",
                },
            ),
            spec=k8s.batch.v1.JobSpecArgs(
                backoff_limit=3,
                ttl_seconds_after_finished=600,
                template=k8s.core.v1.PodTemplateSpecArgs(
                    spec=k8s.core.v1.PodSpecArgs(
                        restart_policy="OnFailure",
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="mc",
                                image=cfg.mc_image,
                                command=["/bin/sh", "-c", f"""
until mc alias set local {MINIO_ENDPOINT} \
  "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; do
  echo "Waiting for MinIO..."; sleep 3
done
mc mb local/{cfg.bucket} --ignore-existing
mc anonymous set download local/{cfg.bucket}
echo "Bucket {cfg.bucket} ready"
"""],
                                env=self._minio_env(cfg.namespace),
                            )
                        ],
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self,
                provider=provider,
                depends_on=[self.deployment, self.service],
            ),
        )

        self.endpoint = MINIO_ENDPOINT

        self.register_outputs({
            "endpoint": self.endpoint,
            "bucket": cfg.bucket,
        })

    def _make_secret(
        self,
        namespace: str,
        cfg: MinioConfig,
        opts: pulumi.ResourceOptions,
    ) -> k8s.core.v1.Secret:
        """Create minio-credentials secret in the given namespace."""
        return k8s.core.v1.Secret(
            f"minio-secret-{namespace}",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="minio-credentials",
                namespace=namespace,
            ),
            type="Opaque",
            string_data={
                "root-user":     cfg.user,
                "root-password": cfg.password,
            },
            opts=opts,
        )

    def _minio_env(self, namespace: str) -> list:
        """Env vars that reference minio-credentials secret."""
        return [
            k8s.core.v1.EnvVarArgs(
                name="MINIO_ROOT_USER",
                value_from=k8s.core.v1.EnvVarSourceArgs(
                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                        name="minio-credentials",
                        key="root-user",
                    ),
                ),
            ),
            k8s.core.v1.EnvVarArgs(
                name="MINIO_ROOT_PASSWORD",
                value_from=k8s.core.v1.EnvVarSourceArgs(
                    secret_key_ref=k8s.core.v1.SecretKeySelectorArgs(
                        name="minio-credentials",
                        key="root-password",
                    ),
                ),
            ),
        ]
