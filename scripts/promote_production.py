"""Promote Staging model to Production (validates accuracy first)."""
import os, sys
import mlflow

mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
client = mlflow.tracking.MlflowClient()

versions = client.get_latest_versions("mnist-classifier", stages=["Staging"])
if not versions:
    print("❌ No model in Staging"); sys.exit(1)

v = versions[0]
run = client.get_run(v.run_id)
acc = float(run.data.metrics.get("test_accuracy", 0))

if acc < 0.95:
    print(f"❌ Accuracy {acc:.4f} < 0.95 threshold"); sys.exit(1)

client.transition_model_version_stage(
    "mnist-classifier", v.version, "Production",
    archive_existing_versions=True
)
print(f"✅ v{v.version} (accuracy={acc:.4f}) → Production")
