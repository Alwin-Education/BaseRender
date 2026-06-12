#!/usr/bin/env python3
"""Phase 2 AWS dev setup: S3, MediaConvert role, Lambdas, EventBridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAMBDA_DIR = ROOT / "apps" / "lambda"
STATE_PATH = Path(__file__).resolve().parent / "dev-state.json"


def _load_env() -> None:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
    from baserender_api.env import load_local_env

    load_local_env()


def _require_env(*names: str) -> dict[str, str]:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise SystemExit(
            f"Missing env vars in apps/api/.env: {', '.join(missing)}\n"
            "Configure AWS credentials and BASERENDER_* values first."
        )
    return {name: os.environ[name] for name in names}


def _clients(region: str):
    import boto3

    return {
        "sts": boto3.client("sts", region_name=region),
        "s3": boto3.client("s3", region_name=region),
        "iam": boto3.client("iam", region_name=region),
        "lambda": boto3.client("lambda", region_name=region),
        "events": boto3.client("events", region_name=region),
        "mediaconvert": boto3.client("mediaconvert", region_name=region),
    }


def _ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"S3 bucket exists: {bucket}")
        return
    except Exception:
        pass

    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    print(f"Created S3 bucket: {bucket}")


def _ensure_mediaconvert_role(iam, account_id: str, bucket: str) -> str:
    role_name = "BaseRenderMediaConvertRole"
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "mediaconvert.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket}",
                    f"arn:aws:s3:::{bucket}/*",
                ],
            }
        ],
    }

    try:
        iam.get_role(RoleName=role_name)
        print(f"MediaConvert role exists: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="MediaConvert service role for BaseRender dev",
        )
        print(f"Created MediaConvert role: {role_name}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="BaseRenderMediaConvertS3Access",
        PolicyDocument=json.dumps(policy),
    )
    return role_arn


def _mediaconvert_queue_arn(mediaconvert, account_id: str, region: str) -> str:
    endpoint = os.getenv("BASERENDER_MEDIACONVERT_ENDPOINT")
    if not endpoint:
        endpoints = mediaconvert.describe_endpoints(MaxResults=1)
        endpoint = endpoints["Endpoints"][0]["Url"]
        os.environ["BASERENDER_MEDIACONVERT_ENDPOINT"] = endpoint

    import boto3

    mc = boto3.client("mediaconvert", region_name=region, endpoint_url=endpoint)
    queues = mc.list_queues(MaxResults=20)
    queue_list = queues.get("Queues") or []
    if not queue_list:
        raise SystemExit("No MediaConvert queues found. Open the MediaConvert console once to activate the account.")
    return queue_list[0]["Arn"]


def _generate_test_clip(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    print(f"Generated test clip: {path.name}")


def _upload_test_media(s3, bucket: str, prefix: str) -> None:
    media_dir = ROOT / "fixtures" / "media"
    clip_a = media_dir / "Shot_A.mov"
    clip_b = media_dir / "Shot_B.mov"
    lut = ROOT / "packages" / "baserender" / "tests" / "test.cube"

    _generate_test_clip(clip_a)
    _generate_test_clip(clip_b)

    uploads = [
        (clip_a, f"{prefix}Shot_A.mov"),
        (clip_b, f"{prefix}Shot_B.mov"),
        (lut, f"{prefix}looks/a.cube"),
        (lut, f"{prefix}looks/b.cube"),
    ]
    for local_path, key in uploads:
        s3.upload_file(str(local_path), bucket, key)
        print(f"Uploaded s3://{bucket}/{key}")


def _build_lambda_zip() -> Path:
    zip_path = LAMBDA_DIR / "build" / "baserender-lambda.zip"
    if zip_path.is_file() and zip_path.stat().st_mtime > time.time() - 3600:
        print(f"Using existing Lambda zip: {zip_path}")
        return zip_path

    subprocess.run(["bash", str(LAMBDA_DIR / "build_zip.sh")], check=True, cwd=ROOT)
    return zip_path


def _ensure_ffmpeg_layer(lambda_client, region: str) -> str | None:
    layer_name = "baserender-ffmpeg"
    try:
        versions = lambda_client.list_layer_versions(LayerName=layer_name, MaxItems=1)
        latest = versions.get("LayerVersions") or []
        if latest:
            arn = latest[0]["LayerVersionArn"]
            print(f"Using FFmpeg layer: {arn}")
            return arn
    except lambda_client.exceptions.ResourceNotFoundException:
        pass

    layer_dir = LAMBDA_DIR / "build" / "ffmpeg-layer"
    bin_dir = layer_dir / "bin"
    if not (bin_dir / "ffmpeg").is_file():
        print("Building FFmpeg Lambda layer (requires Docker)...")
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{layer_dir}:/out",
                "amazonlinux:2023",
                "bash",
                "-lc",
                "dnf install -y tar xz wget && "
                "mkdir -p /out/bin && cd /tmp && "
                "wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && "
                "tar xf ffmpeg-release-amd64-static.tar.xz && "
                "cp ffmpeg-*-amd64-static/ffmpeg ffmpeg-*-amd64-static/ffprobe /out/bin/",
            ],
            check=True,
        )

    zip_path = LAMBDA_DIR / "build" / "ffmpeg-layer.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for binary in ("ffmpeg", "ffprobe"):
            path = bin_dir / binary
            archive.write(path, f"bin/{binary}")

    # Publish via S3: the zipped layer exceeds the 70 MB direct-upload request limit.
    import boto3

    bucket = os.environ["BASERENDER_S3_BUCKET"]
    layer_key = "baserender/layers/ffmpeg-layer.zip"
    boto3.client("s3", region_name=region).upload_file(str(zip_path), bucket, layer_key)
    response = lambda_client.publish_layer_version(
        LayerName=layer_name,
        Description="Static FFmpeg binaries for BaseRender render Lambda",
        Content={"S3Bucket": bucket, "S3Key": layer_key},
        CompatibleRuntimes=["python3.11", "python3.12", "python3.13"],
    )
    arn = response["LayerVersionArn"]
    print(f"Published FFmpeg layer: {arn}")
    return arn


def _deploy_lambda(
    lambda_client,
    *,
    name: str,
    handler: str,
    zip_bytes: bytes,
    role_arn: str,
    env: dict[str, str],
    layers: list[str] | None = None,
    timeout: int = 300,
    memory: int = 1024,
    ephemeral_mb: int = 512,
) -> str:
    layers = layers or []
    try:
        existing = lambda_client.get_function(FunctionName=name)
        lambda_client.update_function_code(FunctionName=name, ZipFile=zip_bytes)
        waiter = lambda_client.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=name)
        lambda_client.update_function_configuration(
            FunctionName=name,
            Handler=handler,
            Role=role_arn,
            Timeout=timeout,
            MemorySize=memory,
            Environment={"Variables": env},
            Layers=layers,
            EphemeralStorage={"Size": ephemeral_mb},
        )
        waiter.wait(FunctionName=name)
        print(f"Updated Lambda: {name}")
    except lambda_client.exceptions.ResourceNotFoundException:
        lambda_client.create_function(
            FunctionName=name,
            Runtime="python3.12",
            Role=role_arn,
            Handler=handler,
            Code={"ZipFile": zip_bytes},
            Timeout=timeout,
            MemorySize=memory,
            Environment={"Variables": env},
            Layers=layers,
            EphemeralStorage={"Size": ephemeral_mb},
            Publish=True,
        )
        waiter = lambda_client.get_waiter("function_active_v2")
        waiter.wait(FunctionName=name)
        print(f"Created Lambda: {name}")

    return lambda_client.get_function(FunctionName=name)["Configuration"]["FunctionArn"]


def _ensure_function_url(lambda_client, function_name: str) -> str:
    try:
        config = lambda_client.create_function_url_config(
            FunctionName=function_name,
            AuthType="NONE",
            InvokeMode="BUFFERED",
        )
        print(f"Created Function URL for {function_name}")
    except lambda_client.exceptions.ResourceConflictException:
        config = lambda_client.get_function_url_config(FunctionName=function_name)

    # Function URLs created since Oct 2025 need BOTH InvokeFunctionUrl and
    # InvokeFunction (scoped to URL invocations) in the resource policy.
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId="function-url-public",
            Action="lambda:InvokeFunctionUrl",
            Principal="*",
            FunctionUrlAuthType="NONE",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId="function-url-public-invoke",
            Action="lambda:InvokeFunction",
            Principal="*",
            InvokedViaFunctionUrl=True,
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass

    return config["FunctionUrl"]


def _lambda_execution_role(iam, account_id: str, bucket: str, event_bus: str) -> str:
    role_name = "BaseRenderLambdaExecutionRole"
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": f"arn:aws:s3:::{bucket}/*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket}",
            },
            {
                "Effect": "Allow",
                "Action": "events:PutEvents",
                "Resource": f"arn:aws:events:*:{account_id}:event-bus/{event_bus}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "mediaconvert:CreateJob",
                    "mediaconvert:GetJob",
                    "mediaconvert:DescribeEndpoints",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": f"arn:aws:iam::{account_id}:role/BaseRenderMediaConvertRole",
                "Condition": {
                    "StringEquals": {"iam:PassedToService": "mediaconvert.amazonaws.com"}
                },
            },
        ],
    }
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="BaseRender Lambda execution role",
        )
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="BaseRenderLambdaAccess",
        PolicyDocument=json.dumps(policy),
    )
    time.sleep(10)
    return role_arn


def _allow_eventbridge_invoke(lambda_client, function_name: str, rule_arn: str) -> None:
    statement_id = f"eventbridge-{rule_arn.split('/')[-1]}"
    try:
        lambda_client.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=rule_arn,
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass


def _ensure_eventbridge_rules(
    events,
    lambda_client,
    *,
    region: str,
    account_id: str,
    event_source: str,
    backend_arn: str,
) -> None:
    bus = os.getenv("BASERENDER_EVENT_BUS", "default")

    # All rules target the unified backend Lambda; it routes by detail-type.
    rules = [
        (
            "baserender-mc-complete",
            {
                "source": ["aws.mediaconvert"],
                "detail-type": ["MediaConvert Job State Change"],
                "detail": {"status": ["COMPLETE", "ERROR", "CANCELED"]},
            },
            backend_arn,
        ),
        (
            "baserender-shot-complete",
            {
                "source": [event_source],
                "detail-type": ["BaseRender Shot Complete"],
            },
            backend_arn,
        ),
        (
            "baserender-lambda-shot",
            {
                "source": [event_source],
                "detail-type": ["BaseRender Lambda Shot"],
            },
            backend_arn,
        ),
    ]

    for name, pattern, target_arn in rules:
        events.put_rule(
            Name=name,
            EventBusName=bus,
            EventPattern=json.dumps(pattern),
            State="ENABLED",
        )
        rule_arn = f"arn:aws:events:{region}:{account_id}:rule/{name}"
        events.put_targets(
            Rule=name,
            EventBusName=bus,
            Targets=[{"Id": "1", "Arn": target_arn}],
        )
        _allow_eventbridge_invoke(lambda_client, target_arn.split(":")[-1], rule_arn)
        print(f"EventBridge rule ready: {name} -> {target_arn.split(':')[-1]}")


def _update_env_file(updates: dict[str, str]) -> None:
    env_path = ROOT / "apps" / "api" / ".env"
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in remaining:
                new_lines.append(f"{key}={remaining.pop(key)}")
                continue
        new_lines.append(line)

    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Updated {env_path.relative_to(ROOT)}")


def _validate_credentials(sts) -> str:
    try:
        return sts.get_caller_identity()["Account"]
    except Exception as exc:
        raise SystemExit(
            "AWS credentials are missing or invalid.\n\n"
            "Option A — new account (recommended):\n"
            "  1. aws configure   # use admin or power-user keys once\n"
            "  2. python scripts/aws/provision_stack.py --bucket-name baserender-dev-YOURNAME\n"
            "  3. python scripts/aws/setup_dev.py\n\n"
            "Option B — paste keys into apps/api/.env, then re-run this script.\n\n"
            f"Original error: {exc}"
        ) from exc


def _secret_env(name: str, *, length: int = 32) -> str:
    """Read a secret from env or generate one (persisted later via _update_env_file)."""
    import secrets

    value = os.getenv(name, "")
    if not value:
        value = secrets.token_hex(length)
        os.environ[name] = value
        print(f"Generated {name}")
    return value


def _cleanup_legacy(lambda_client) -> None:
    for name in ("baserender-render", "baserender-notifier"):
        try:
            lambda_client.delete_function(FunctionName=name)
            print(f"Deleted legacy Lambda: {name}")
        except lambda_client.exceptions.ResourceNotFoundException:
            print(f"Legacy Lambda already gone: {name}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cleanup-legacy",
        action="store_true",
        help="Delete the legacy baserender-render/baserender-notifier Lambdas and exit.",
    )
    args = parser.parse_args()

    _load_env()
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    os.environ.setdefault("AWS_REGION", region)

    _require_env("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    bucket = os.getenv("BASERENDER_S3_BUCKET") or f"baserender-dev-{int(time.time())}"

    clients = _clients(region)
    account_id = _validate_credentials(clients["sts"])
    print(f"AWS account: {account_id} ({region})")

    if args.cleanup_legacy:
        _cleanup_legacy(clients["lambda"])
        return

    _ensure_bucket(clients["s3"], bucket, region)
    mc_role_arn = _ensure_mediaconvert_role(clients["iam"], account_id, bucket)
    queue_arn = _mediaconvert_queue_arn(clients["mediaconvert"], account_id, region)

    media_prefix = "test/"
    _upload_test_media(clients["s3"], bucket, media_prefix)

    lambda_role_arn = _lambda_execution_role(
        clients["iam"],
        account_id,
        bucket,
        os.getenv("BASERENDER_EVENT_BUS", "default"),
    )

    zip_path = _build_lambda_zip()
    zip_bytes = zip_path.read_bytes()
    ffmpeg_layer = _ensure_ffmpeg_layer(clients["lambda"], region)

    event_env = {
        "BASERENDER_EVENT_BUS": os.getenv("BASERENDER_EVENT_BUS", "default"),
        "BASERENDER_EVENT_SOURCE": os.getenv("BASERENDER_EVENT_SOURCE", "baserender"),
    }
    worker_token = _secret_env("BASERENDER_WORKER_TOKEN")
    proxy_token = _secret_env("BASERENDER_PROXY_TOKEN")
    auth_password = _secret_env("BASERENDER_AUTH_PASSWORD", length=16)
    session_secret = _secret_env("BASERENDER_SESSION_SECRET")

    backend_env = {
        "BASERENDER_S3_BUCKET": bucket,
        "BASERENDER_RENDER_BACKEND": "cloud",
        "BASERENDER_MEDIACONVERT_ROLE_ARN": mc_role_arn,
        "BASERENDER_MEDIACONVERT_QUEUE_ARN": queue_arn,
        "BASERENDER_MEDIACONVERT_ENDPOINT": os.getenv("BASERENDER_MEDIACONVERT_ENDPOINT", ""),
        **event_env,
        "BASERENDER_PROXY_TOKEN": proxy_token,
        "BASERENDER_WORKER_TOKEN": worker_token,
        "BASERENDER_AUTH_PASSWORD": auth_password,
        "BASERENDER_SESSION_SECRET": session_secret,
        "BASERENDER_AUTH_SECURE_COOKIE": "true",
        "BASERENDER_DISABLE_WORKER_ROUTES": "1",
    }
    # AWS_REGION is reserved on Lambda (provided by the runtime); drop empties.
    backend_env = {key: value for key, value in backend_env.items() if value}

    backend_arn = _deploy_lambda(
        clients["lambda"],
        name="baserender-backend",
        handler="baserender_lambda.unified.lambda_handler",
        zip_bytes=zip_bytes,
        role_arn=lambda_role_arn,
        env=backend_env,
        layers=[ffmpeg_layer] if ffmpeg_layer else [],
        timeout=900,
        memory=2048,
        ephemeral_mb=4096,
    )
    function_url = _ensure_function_url(clients["lambda"], "baserender-backend")

    _ensure_eventbridge_rules(
        clients["events"],
        clients["lambda"],
        region=region,
        account_id=account_id,
        event_source=event_env["BASERENDER_EVENT_SOURCE"],
        backend_arn=backend_arn,
    )

    env_updates = {
        "BASERENDER_S3_BUCKET": bucket,
        "BASERENDER_RENDER_BACKEND": "cloud",
        "AWS_REGION": region,
        "BASERENDER_MEDIACONVERT_ROLE_ARN": mc_role_arn,
        "BASERENDER_MEDIACONVERT_QUEUE_ARN": queue_arn,
        "BASERENDER_EVENT_BUS": event_env["BASERENDER_EVENT_BUS"],
        "BASERENDER_EVENT_SOURCE": event_env["BASERENDER_EVENT_SOURCE"],
        "BASERENDER_WORKER_TOKEN": worker_token,
        "BASERENDER_PROXY_TOKEN": proxy_token,
        "BASERENDER_AUTH_PASSWORD": auth_password,
        "BASERENDER_SESSION_SECRET": session_secret,
    }
    _update_env_file(env_updates)

    state = {
        "account_id": account_id,
        "region": region,
        "bucket": bucket,
        "mediaconvert_role_arn": mc_role_arn,
        "mediaconvert_queue_arn": queue_arn,
        "backend_lambda_arn": backend_arn,
        "function_url": function_url,
        "ffmpeg_layer": ffmpeg_layer,
    }
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {STATE_PATH.relative_to(ROOT)}")

    print(f"\nBackend Function URL: {function_url}")
    print(
        "Set BASERENDER_API_PROXY_TARGET to this URL in the Amplify env and redeploy.\n"
        "After end-to-end verification, retire the legacy Lambdas with: "
        "python scripts/aws/setup_dev.py --cleanup-legacy"
    )


if __name__ == "__main__":
    main()
