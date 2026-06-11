#!/usr/bin/env python3
"""Update notifier Lambda with the public API URL for EventBridge callbacks."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("api_base_url", help="Public HTTPS URL for FastAPI (no trailing slash)")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
    from baserender_api.env import load_local_env

    load_local_env()

    import boto3

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    worker_token = os.getenv("BASERENDER_WORKER_TOKEN", "")
    if not worker_token:
        raise SystemExit("Set BASERENDER_WORKER_TOKEN in apps/api/.env")

    lambda_client = boto3.client("lambda", region_name=region)
    config = lambda_client.get_function_configuration(FunctionName="baserender-notifier")
    env = config.get("Environment", {}).get("Variables") or {}
    env["BASERENDER_API_BASE_URL"] = args.api_base_url.rstrip("/")
    env["BASERENDER_WORKER_TOKEN"] = worker_token

    lambda_client.update_function_configuration(
        FunctionName="baserender-notifier",
        Environment={"Variables": env},
    )
    print(f"Updated baserender-notifier BASERENDER_API_BASE_URL={args.api_base_url.rstrip('/')}")


if __name__ == "__main__":
    main()
