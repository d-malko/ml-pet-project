"""Promote latest model version to Staging."""
import os, sys
import mlflow

mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
client = mlflow.tracking.MlflowClient()

versions = client.search_model_versions("name='mnist-classifier'")
if not versions:
    print("❌ No model versions found"); sys.exit(1)

latest = sorted(versions, key=lambda v: int(v.version), reverse=True)[0]
client.transition_model_version_stage(
    "mnist-classifier", latest.version, "Staging",
    archive_existing_versions=False
)
print(f"✅ v{latest.version} → Staging")
