from __future__ import annotations

import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from baserender_api.app import app

TEST_AUTH_PASSWORD = "test-password"
TEST_S3_BUCKET = "test-bucket"


def set_defaults_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    **values: object,
) -> None:
    from baserender.defaults import CONFIG_ENV_VAR, _load_defaults

    config_path = tmp_path / "defaults.json"
    config_path.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _load_defaults.cache_clear()


@pytest.fixture
def defaults_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def configure(**values: object) -> None:
        set_defaults_config(monkeypatch, tmp_path, **values)

    return configure


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        storage_key = (Bucket, Key)
        if storage_key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
                "GetObject",
            )
        data = self.objects[storage_key]

        class Body:
            def read(self) -> bytes:
                return data

        return {"Body": Body()}

    def put_object(self, *, Bucket: str, Key: str, Body, **_kwargs) -> None:
        if hasattr(Body, "read"):
            data = Body.read()
        elif isinstance(Body, (bytes, bytearray)):
            data = bytes(Body)
        else:
            data = str(Body).encode()
        self.objects[(Bucket, Key)] = data

    def delete_object(self, *, Bucket: str, Key: str, **_kwargs) -> None:
        self.objects.pop((Bucket, Key), None)

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        storage_key = (Bucket, Key)
        if storage_key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NotFound", "Message": "Not Found"}},
                "HeadObject",
            )
        return {"ContentLength": len(self.objects[storage_key])}

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict | None = None,
        ExpiresIn: int | None = None,
        **_kwargs,
    ) -> str:
        params = Params or {}
        bucket = params["Bucket"]
        key = params["Key"]
        if operation == "put_object":
            content_type = params.get("ContentType", "")
            return (
                f"https://fake-s3.test/put?bucket={bucket}&key={key}"
                f"&content_type={content_type}&expires={ExpiresIn}"
            )
        if operation == "get_object":
            return f"https://fake-s3.test/get?bucket={bucket}&key={key}&expires={ExpiresIn}"
        raise ValueError(f"Unsupported operation: {operation}")


@pytest.fixture
def fake_s3_client(monkeypatch: pytest.MonkeyPatch) -> FakeS3Client:
    client = FakeS3Client()
    monkeypatch.setenv("BASERENDER_S3_BUCKET", TEST_S3_BUCKET)
    monkeypatch.setenv("BASERENDER_ARTIFACT_PREFIX", "baserender")
    monkeypatch.setenv("BASERENDER_JOB_STATE_KEY", "baserender/jobs/current.json")
    monkeypatch.setattr(
        "baserender_api.job_store.S3JobStateBackend._create_client",
        lambda self: client,
    )
    monkeypatch.setattr(
        "baserender_api.storage.S3ArtifactStore._create_client",
        lambda self: client,
    )
    monkeypatch.setattr(
        "baserender_api.output_storage.S3OutputStore._create_client",
        lambda self: client,
    )
    monkeypatch.setattr(
        "baserender_api.media.s3.S3MediaProvider._create_client",
        lambda self, **kwargs: client,
    )
    return client


@pytest.fixture(autouse=True)
def clear_defaults_cache() -> None:
    from baserender.defaults import _load_defaults

    yield
    _load_defaults.cache_clear()


@pytest.fixture(autouse=True)
def auth_env(
    monkeypatch: pytest.MonkeyPatch,
    fake_s3_client: FakeS3Client,
) -> FakeS3Client:
    monkeypatch.setenv("BASERENDER_AUTH_PASSWORD", TEST_AUTH_PASSWORD)
    monkeypatch.setenv("BASERENDER_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("BASERENDER_AUTH_SECURE_COOKIE", "false")
    monkeypatch.setenv("BASERENDER_WORKER_TOKEN", "test-worker-token")
    monkeypatch.setenv("BASERENDER_RENDER_BACKEND", "worker")
    return fake_s3_client


@pytest.fixture
def auth_password() -> str:
    return TEST_AUTH_PASSWORD


@pytest.fixture
def authenticated_client() -> TestClient:
    client = TestClient(app)
    response = client.post("/auth/login", json={"password": TEST_AUTH_PASSWORD})
    assert response.status_code == 200
    return client
