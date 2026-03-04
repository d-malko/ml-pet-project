"""
Namespaces component.
Creates all required namespaces with proper labels.
Istio sidecar injection is enabled on ml-serving.
"""
import pulumi
import pulumi_kubernetes as k8s


class Namespaces(pulumi.ComponentResource):
    """Creates and exports all project namespaces."""

    def __init__(
        self,
        name: str,
        provider: k8s.Provider,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("ml-pet-project:infra:Namespaces", name, {}, opts)

        child_opts = pulumi.ResourceOptions(parent=self, provider=provider)

        self.ml_serving = k8s.core.v1.Namespace(
            "ns-ml-serving",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-serving",
                labels={
                    # Istio automatic sidecar injection
                    "istio-injection": "enabled",
                },
            ),
            opts=child_opts,
        )

        self.mlflow = k8s.core.v1.Namespace(
            "ns-mlflow",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="mlflow"),
            opts=child_opts,
        )

        self.minio = k8s.core.v1.Namespace(
            "ns-minio",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="minio"),
            opts=child_opts,
        )

        self.register_outputs({
            "ml_serving": self.ml_serving.metadata.name,
            "mlflow": self.mlflow.metadata.name,
            "minio": self.minio.metadata.name,
        })
