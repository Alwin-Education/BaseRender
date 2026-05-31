#!/usr/bin/env bash
set -euo pipefail

# Build a deployable Lambda function zip with baserender + baserender_lambda.
# Run on Amazon Linux 2023 (or a matching CI image) for production deployment.
# boto3 is omitted because the Lambda Python runtime provides it.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build/package"
ZIP_PATH="$SCRIPT_DIR/build/baserender-lambda.zip"

rm -rf "$BUILD_DIR" "$ZIP_PATH"
mkdir -p "$BUILD_DIR"

pip install --target "$BUILD_DIR" "$ROOT/packages/baserender" "$SCRIPT_DIR"

(
  cd "$BUILD_DIR"
  zip -r "$ZIP_PATH" .
)

echo "Built $ZIP_PATH"
echo "Handlers:"
echo "  Render: baserender_lambda.handler.lambda_handler"
echo "  Notifier: baserender_lambda.notifier.lambda_handler"
echo "Attach an FFmpeg Lambda layer that places ffmpeg/ffprobe on PATH (e.g. /opt/bin)."
