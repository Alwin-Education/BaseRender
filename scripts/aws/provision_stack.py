#!/usr/bin/env python3
"""Deploy CloudFormation dev stack and write AWS credentials to apps/api/.env."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).resolve().parent / "cloudformation" / "baserender-dev.yaml"
ENV_PATH = ROOT / "apps" / "api" / ".env"


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _update_env(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.is_file() else []
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
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="baserender-dev")
    parser.add_argument("--bucket-name", required=True, help="Globally unique S3 bucket name")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    if not shutil_which("aws"):
        raise SystemExit("AWS CLI not found. Install with: pip install awscli && aws configure")

    _run(
        [
            "aws",
            "cloudformation",
            "deploy",
            "--template-file",
            str(TEMPLATE),
            "--stack-name",
            args.stack_name,
            "--parameter-overrides",
            f"BucketName={args.bucket_name}",
            "--capabilities",
            "CAPABILITY_NAMED_IAM",
            "--region",
            args.region,
        ]
    )

    outputs_raw = _run(
        [
            "aws",
            "cloudformation",
            "describe-stacks",
            "--stack-name",
            args.stack_name,
            "--region",
            args.region,
            "--query",
            "Stacks[0].Outputs",
            "--output",
            "json",
        ]
    )
    outputs = {item["OutputKey"]: item["OutputValue"] for item in json.loads(outputs_raw)}

    _update_env(
        {
            "AWS_ACCESS_KEY_ID": outputs["ApiAccessKeyId"],
            "AWS_SECRET_ACCESS_KEY": outputs["ApiSecretAccessKey"],
            "AWS_REGION": outputs["Region"],
            "BASERENDER_S3_BUCKET": outputs["BucketName"],
            "BASERENDER_MEDIACONVERT_ROLE_ARN": outputs["MediaConvertRoleArn"],
            "BASERENDER_RENDER_BACKEND": "cloud",
        }
    )
    print(f"Stack {args.stack_name} deployed. Updated {ENV_PATH.relative_to(ROOT)}")
    print("Next: python scripts/aws/setup_dev.py")


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


if __name__ == "__main__":
    main()
