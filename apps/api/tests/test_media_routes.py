from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from baserender_api.app import app
from baserender_api.media.provider import CloudMediaObject, ListObjectsResult
from baserender_api.media.routes import media_provider_dependency
from baserender_api.media.s3 import MediaStorageError


class FakeProvider:
    id = "fake"

    def __init__(self) -> None:
        self.prefixes: list[str] = []

    def list_objects(
        self,
        prefix: str,
        *,
        continuation_token: str | None = None,
        max_keys: int | None = None,
    ) -> ListObjectsResult:
        self.prefixes.append(prefix)
        return ListObjectsResult(
            objects=(
                CloudMediaObject(key=f"{prefix}Shot_A.mov", size=123),
                CloudMediaObject(key=f"{prefix}Other.mov", size=456),
            ),
            next_continuation_token=None,
        )


class MixedMediaProvider(FakeProvider):
    def list_objects(
        self,
        prefix: str,
        *,
        continuation_token: str | None = None,
        max_keys: int | None = None,
    ) -> ListObjectsResult:
        self.prefixes.append(prefix)
        return ListObjectsResult(
            objects=(
                CloudMediaObject(key=f"{prefix}test/", size=0),
                CloudMediaObject(key=f"{prefix}nested/Shot_A.MOV", size=123),
                CloudMediaObject(key=f"{prefix}nested/Other.mov", size=456),
                CloudMediaObject(key=f"{prefix}nested/plate.png", size=234),
                CloudMediaObject(key=f"{prefix}metadata.json", size=789),
            ),
            next_continuation_token=None,
        )


class BrokenProvider(FakeProvider):
    def list_objects(
        self,
        prefix: str,
        *,
        continuation_token: str | None = None,
        max_keys: int | None = None,
    ) -> ListObjectsResult:
        raise MediaStorageError(
            "The configured S3 bucket 'missing-bucket' does not exist. "
            "Check BASERENDER_S3_BUCKET in the API environment."
        )


def test_get_media_config_uses_media_prefix_from_defaults_config(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo/")

    response = authenticated_client.get("/media/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["allowed_prefix"] == "projects/demo/"
    assert payload["default_media_prefix"] == "projects/demo/"


def test_get_media_config_includes_default_output_prefix(
    authenticated_client: TestClient,
    monkeypatch,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo", output_path="outputs/output.mp4")
    monkeypatch.setenv("BASERENDER_OUTPUT_PREFIX", "outputs")

    response = authenticated_client.get("/media/config")

    assert response.status_code == 200
    assert response.json()["allowed_prefix"] == "projects/demo"
    assert response.json()["default_output_prefix"] == "outputs"
    assert response.json()["default_output_path"] == "outputs/output.mp4"
    assert response.json()["default_width"] == 1920
    assert response.json()["default_height"] == 1080
    assert response.json()["default_fps"] == 24.0
    assert response.json()["default_container"] == "mp4"
    assert response.json()["default_video_codec"] == "h264"
    assert response.json()["default_video_bitrate"] == 8_000_000
    assert response.json()["default_video_preset"] == "faster"
    assert response.json()["default_video_faststart"] is True
    assert response.json()["default_audio_codec"] == "aac"
    assert response.json()["default_audio_bitrate"] == 192_000


def test_get_media_config_includes_defaults_from_config(
    authenticated_client: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    from baserender.defaults import CONFIG_ENV_VAR, _load_defaults

    config_path = tmp_path / "defaults.json"
    config_path.write_text(
        json.dumps(
            {
                "output_path": "outputs/episode-1/final.mp4",
                "container": "mov",
                "width": 3840,
                "height": 2160,
                "fps": 29.97,
                "video_codec": "hevc",
                "video_bitrate": 20_000_000,
                "video_preset": "slow",
                "video_faststart": False,
                "audio_codec": "pcm",
                "audio_bitrate": 1_536_000,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))
    _load_defaults.cache_clear()

    try:
        response = authenticated_client.get("/media/config")

        assert response.status_code == 200
        payload = response.json()
        assert payload["default_output_path"] == "outputs/episode-1/final.mp4"
        assert payload["default_container"] == "mov"
        assert payload["default_width"] == 3840
        assert payload["default_height"] == 2160
        assert payload["default_fps"] == 29.97
        assert payload["default_video_codec"] == "hevc"
        assert payload["default_video_bitrate"] == 20_000_000
        assert payload["default_video_preset"] == "slow"
        assert payload["default_video_faststart"] is False
        assert payload["default_audio_codec"] == "pcm"
        assert payload["default_audio_bitrate"] == 1_536_000
    finally:
        _load_defaults.cache_clear()


def test_list_media_objects_uses_empty_prefix_at_bucket_root_when_media_prefix_unset(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="")
    provider = FakeProvider()
    app.dependency_overrides[media_provider_dependency] = lambda: provider

    try:
        response = authenticated_client.get("/media/objects")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["prefix"] == ""
    assert provider.prefixes == [""]


def test_list_media_objects_uses_allowed_prefix(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    provider = FakeProvider()
    app.dependency_overrides[media_provider_dependency] = lambda: provider

    try:
        response = authenticated_client.get("/media/objects", params={"prefix": "day1/"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "fake"
    assert payload["prefix"] == "projects/demo/day1/"
    assert provider.prefixes == ["projects/demo/day1/"]
    assert payload["objects"][0]["key"] == "projects/demo/day1/Shot_A.mov"


def test_list_media_objects_returns_media_only_with_count(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    provider = MixedMediaProvider()
    app.dependency_overrides[media_provider_dependency] = lambda: provider

    try:
        response = authenticated_client.get("/media/objects", params={"prefix": "day1/"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [obj["key"] for obj in payload["objects"]] == [
        "projects/demo/day1/nested/Shot_A.MOV",
        "projects/demo/day1/nested/Other.mov",
        "projects/demo/day1/nested/plate.png",
    ]
    assert payload["object_count"] == 3
    assert payload["truncated"] is False


def test_create_media_linking_response_returns_suggestions(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    app.dependency_overrides[media_provider_dependency] = lambda: FakeProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={
                "prefix": "day1/",
                "otio_content_base64": _base64_text(_resolve_style_otio_text()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["prefix"] == "projects/demo/day1/"
    assert payload["references"][0]["clip_name"] == "Shot A"
    assert payload["references"][0]["normalized_url"] == "/Volumes/Raid/Shot_A.mov"
    assert payload["references"][0]["suggestions"][0]["key"] == "projects/demo/day1/Shot_A.mov"


def test_create_media_linking_response_suggests_nested_media_only(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    app.dependency_overrides[media_provider_dependency] = lambda: MixedMediaProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={
                "prefix": "day1/",
                "otio_content_base64": _base64_text(_multi_reference_otio_text()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [obj["key"] for obj in payload["objects"]] == [
        "projects/demo/day1/nested/Shot_A.MOV",
        "projects/demo/day1/nested/Other.mov",
        "projects/demo/day1/nested/plate.png",
    ]
    assert payload["object_count"] == 3
    assert payload["references"][0]["suggestions"][0]["key"] == "projects/demo/day1/nested/Shot_A.MOV"
    assert payload["references"][1]["suggestions"][0]["key"] == "projects/demo/day1/nested/Other.mov"


def test_create_media_linking_response_deduplicates_source_urls(
    authenticated_client: TestClient,
    defaults_config,
) -> None:
    defaults_config(media_prefix="projects/demo")
    app.dependency_overrides[media_provider_dependency] = lambda: FakeProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={
                "prefix": "day1/",
                "otio_content_base64": _base64_text(_multi_reference_otio_text()),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    references = payload["references"]
    assert [reference["normalized_url"] for reference in references] == [
        "/Volumes/Raid/Shot_A.mov",
        "/Volumes/Raid/Other.mov",
    ]
    assert [reference["clip_count"] for reference in references] == [2, 1]
    assert references[0]["suggestions"][0]["key"] == "projects/demo/day1/Shot_A.mov"
    assert references[1]["suggestions"][0]["key"] == "projects/demo/day1/Other.mov"


def test_create_media_linking_response_rejects_invalid_otio_content(
    authenticated_client: TestClient,
) -> None:
    app.dependency_overrides[media_provider_dependency] = lambda: FakeProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={"otio_content_base64": "not-valid-base64"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Could not load OTIO timeline" in response.json()["detail"]


def test_create_media_linking_response_rejects_malformed_otio_content(
    authenticated_client: TestClient,
) -> None:
    app.dependency_overrides[media_provider_dependency] = lambda: FakeProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={"otio_content_base64": _base64_text("{}")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "Could not load OTIO timeline" in response.json()["detail"]


def test_create_media_linking_response_requires_timeline_source(
    authenticated_client: TestClient,
) -> None:
    app.dependency_overrides[media_provider_dependency] = lambda: FakeProvider()

    try:
        response = authenticated_client.post("/media/linking", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_create_media_linking_response_returns_storage_error_detail(
    authenticated_client: TestClient,
) -> None:
    app.dependency_overrides[media_provider_dependency] = lambda: BrokenProvider()

    try:
        response = authenticated_client.post(
            "/media/linking",
            json={"otio_content_base64": _base64_text(_resolve_style_otio_text())},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "does not exist" in response.json()["detail"]
    assert "BASERENDER_S3_BUCKET" in response.json()["detail"]


def _base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _resolve_style_otio_text() -> str:
    return json.dumps(
        {
            "OTIO_SCHEMA": "Timeline.1",
            "metadata": {},
            "name": "Resolve Inventory",
            "global_start_time": None,
            "tracks": {
                "OTIO_SCHEMA": "Stack.1",
                "metadata": {},
                "name": "",
                "source_range": None,
                "effects": [],
                "markers": [],
                "enabled": True,
                "children": [
                    {
                        "OTIO_SCHEMA": "Track.1",
                        "metadata": {},
                        "name": "Video 1",
                        "source_range": None,
                        "effects": [],
                        "markers": [],
                        "enabled": True,
                        "children": [
                            {
                                "OTIO_SCHEMA": "Clip.2",
                                "metadata": {},
                                "name": "Shot A",
                                "source_range": None,
                                "effects": [],
                                "markers": [],
                                "enabled": True,
                                "media_references": {
                                    "DEFAULT_MEDIA": {
                                        "OTIO_SCHEMA": "ExternalReference.1",
                                        "metadata": {},
                                        "name": "Shot_A.mov",
                                        "available_range": None,
                                        "available_image_bounds": None,
                                        "target_url": "file:///Volumes/Raid/Shot_A.mov",
                                    }
                                },
                                "active_media_reference_key": "DEFAULT_MEDIA",
                            }
                        ],
                        "kind": "Video",
                    }
                ],
            },
        }
    )


def _multi_reference_otio_text() -> str:
    return json.dumps(
        {
            "OTIO_SCHEMA": "Timeline.1",
            "metadata": {},
            "name": "Resolve Inventory",
            "global_start_time": None,
            "tracks": {
                "OTIO_SCHEMA": "Stack.1",
                "metadata": {},
                "name": "",
                "source_range": None,
                "effects": [],
                "markers": [],
                "enabled": True,
                "children": [
                    {
                        "OTIO_SCHEMA": "Track.1",
                        "metadata": {},
                        "name": "Video 1",
                        "source_range": None,
                        "effects": [],
                        "markers": [],
                        "enabled": True,
                        "children": [
                            _clip_json("Shot A", "file:///Volumes/Raid/Shot_A.mov"),
                            _clip_json("Shot A Replay", "file:///Volumes/Raid/Shot_A.mov"),
                            _clip_json("Other", "file:///Volumes/Raid/Other.mov"),
                        ],
                        "kind": "Video",
                    }
                ],
            },
        }
    )


def _clip_json(name: str, target_url: str) -> dict:
    return {
        "OTIO_SCHEMA": "Clip.2",
        "metadata": {},
        "name": name,
        "source_range": None,
        "effects": [],
        "markers": [],
        "enabled": True,
        "media_references": {
            "DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "metadata": {},
                "name": target_url.rsplit("/", 1)[-1],
                "available_range": None,
                "available_image_bounds": None,
                "target_url": target_url,
            }
        },
        "active_media_reference_key": "DEFAULT_MEDIA",
    }
