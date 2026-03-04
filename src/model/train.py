"""
MNIST Digit Classifier — TensorFlow + MLflow + MinIO (S3)
"""

import os
import argparse
import numpy as np
import tensorflow as tf
import mlflow
import mlflow.tensorflow
from mlflow.models.signature import infer_signature


DEFAULT_CONFIG = {
    "epochs": 5,
    "batch_size": 128,
    "learning_rate": 0.001,
    "dropout_rate": 0.3,
    "conv_filters": [32, 64],
    "dense_units": 128,
}


def configure_s3():
    """Настройка boto3 для работы с MinIO."""
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("MLFLOW_S3_ENDPOINT_URL", "http://localhost:9000")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "minioadmin")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin123")

    # Устанавливаем env vars для MLflow S3 клиента
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = endpoint
    os.environ["AWS_ACCESS_KEY_ID"] = access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = secret_key
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket(bucket: str = "mlflow-artifacts"):
    """Создаёт bucket если не существует."""
    try:
        s3 = configure_s3()
        existing = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        if bucket not in existing:
            s3.create_bucket(Bucket=bucket)
            print(f"✅ Created bucket: {bucket}")
        else:
            print(f"✅ Bucket exists: {bucket}")
    except Exception as e:
        print(f"⚠️  Could not check/create bucket: {e}")


def _upload_dir_to_s3(s3_client, local_dir: str, bucket: str, prefix: str):
    """Рекурсивно загружает папку в S3/MinIO."""
    import os
    for root, dirs, files in os.walk(local_dir):
        for filename in files:
            local_path = os.path.join(root, filename)
            relative = os.path.relpath(local_path, local_dir)
            s3_key = f"{prefix}/{relative}"
            s3_client.upload_file(local_path, bucket, s3_key)
    print(f"✅ Uploaded {local_dir} → s3://{bucket}/{prefix}")


def load_data():
    (X_train, y_train), (X_test, y_test) = tf.keras.datasets.mnist.load_data()
    X_train = np.expand_dims(X_train.astype(np.float32) / 255.0, -1)
    X_test = np.expand_dims(X_test.astype(np.float32) / 255.0, -1)
    return (X_train, y_train), (X_test, y_test)


def build_model(config: dict) -> tf.keras.Model:
    inputs = tf.keras.Input(shape=(28, 28, 1), name="image")
    x = inputs
    for filters in config["conv_filters"]:
        x = tf.keras.layers.Conv2D(filters, (3, 3), activation="relu", padding="same")(x)
        x = tf.keras.layers.MaxPooling2D((2, 2))(x)
        x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Flatten()(x)
    x = tf.keras.layers.Dense(config["dense_units"], activation="relu")(x)
    x = tf.keras.layers.Dropout(config["dropout_rate"])(x)
    outputs = tf.keras.layers.Dense(10, activation="softmax", name="predictions")(x)

    model = tf.keras.Model(inputs=inputs, outputs=outputs, name="mnist_cnn")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=config["learning_rate"]),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def train(config: dict = None):
    config = config or DEFAULT_CONFIG

    # Настраиваем S3/MinIO
    configure_s3()
    ensure_bucket("mlflow-artifacts")

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("mnist-classifier")

    (X_train, y_train), (X_test, y_test) = load_data()

    with mlflow.start_run(run_name="mnist-cnn") as run:
        run_id = run.info.run_id
        print(f"MLflow Run ID: {run_id}")

        mlflow.log_params(config)
        mlflow.log_param("train_samples", len(X_train))
        mlflow.log_param("test_samples", len(X_test))
        mlflow.log_param("tf_version", tf.__version__)

        model = build_model(config)
        model.summary()

        class MLflowCallback(tf.keras.callbacks.Callback):
            def on_epoch_end(self, epoch, logs=None):
                logs = logs or {}
                mlflow.log_metrics({
                    "train_loss": logs.get("loss", 0),
                    "train_accuracy": logs.get("accuracy", 0),
                    "val_loss": logs.get("val_loss", 0),
                    "val_accuracy": logs.get("val_accuracy", 0),
                }, step=epoch)

        model.fit(
            X_train, y_train,
            epochs=config["epochs"],
            batch_size=config["batch_size"],
            validation_split=0.1,
            callbacks=[
                MLflowCallback(),
                tf.keras.callbacks.EarlyStopping(patience=3, restore_best_weights=True),
            ],
            verbose=1,
        )

        test_loss, test_acc = model.evaluate(X_test, y_test, verbose=0)
        mlflow.log_metrics({"test_loss": test_loss, "test_accuracy": test_acc})
        print(f"\nTest accuracy: {test_acc:.4f}")

        # export() создаёт SavedModel формат (совместим с TFServing)
        import shutil
        shutil.rmtree("/tmp/saved_model", ignore_errors=True)
        model.export("/tmp/saved_model")

        # Загружаем напрямую в MinIO через boto3 (обходим MLflow artifact proxy)
        s3 = configure_s3()
        s3_prefix = f"artifacts/{run_id}/saved_model"
        _upload_dir_to_s3(s3, "/tmp/saved_model", "mlflow-artifacts", s3_prefix)
        s3_uri = f"s3://mlflow-artifacts/{s3_prefix}"
        mlflow.log_param("model_s3_uri", s3_uri)
        print(f"✅ Model uploaded to MinIO: {s3_uri}")

        # Регистрируем модель в Registry с прямым S3 URI
        mv = mlflow.register_model(s3_uri, "mnist-classifier")
        print(f"✅ Registered as mnist-classifier v{mv.version}")

        # Promote to Staging если accuracy >= порога
        client = mlflow.tracking.MlflowClient()
        if test_acc >= 0.95:
            client.transition_model_version_stage(
                name="mnist-classifier",
                version=mv.version,
                stage="Staging",
                archive_existing_versions=False,
            )
            print(f"✅ Promoted to Staging (accuracy={test_acc:.4f})")
        else:
            print(f"⚠️  Accuracy {test_acc:.4f} < 0.95, staying in None stage")

        mlflow.set_tags({
            "model_type": "CNN",
            "dataset": "MNIST",
            "framework": "tensorflow",
        })

        return run_id, test_acc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs",     type=int,   default=DEFAULT_CONFIG["epochs"])
    parser.add_argument("--batch-size", type=int,   default=DEFAULT_CONFIG["batch_size"])
    parser.add_argument("--lr",         type=float, default=DEFAULT_CONFIG["learning_rate"])
    args = parser.parse_args()

    cfg = {
        **DEFAULT_CONFIG,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.lr,
    }
    run_id, acc = train(cfg)
    print(f"\nDone! Run ID: {run_id}, Accuracy: {acc:.4f}")
