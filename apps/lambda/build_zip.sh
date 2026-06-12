#!/usr/bin/env bash
set -euo pipefail

# Build the unified Lambda function zip: baserender + baserender_api +
# baserender_lambda plus third-party dependencies.
#
# Third-party packages are installed as manylinux/cp312 wheels so zips built on
# macOS (or any host) run on the Lambda x86_64 python3.12 runtime. boto3 is
# omitted (the Lambda runtime provides it); uvicorn is dev-only.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_DIR="$SCRIPT_DIR/build/package"
ZIP_PATH="$SCRIPT_DIR/build/baserender-lambda.zip"

rm -rf "$BUILD_DIR" "$ZIP_PATH"
mkdir -p "$BUILD_DIR"

# Local packages: pure Python, install without deps so host-built artifacts
# never leak into the bundle.
pip install --target "$BUILD_DIR" --no-deps \
  "$ROOT/packages/baserender" \
  "$ROOT/apps/api" \
  "$SCRIPT_DIR"

# Third-party deps pinned to the Lambda runtime platform.
pip install --target "$BUILD_DIR" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  "opentimelineio>=0.17,<1" \
  "fastapi" \
  "pydantic" \
  "mangum>=0.19" \
  "rapidfuzz" \
  "python-dotenv>=1.0.0"

(
  cd "$BUILD_DIR"
  zip -qr "$ZIP_PATH" .
)

echo "Built $ZIP_PATH"
echo "Handler: baserender_lambda.unified.lambda_handler (unified HTTP + EventBridge + direct invoke)"
echo "Legacy handlers (still packaged): baserender_lambda.handler / baserender_lambda.notifier"
echo "Attach an FFmpeg Lambda layer that places ffmpeg/ffprobe on PATH (e.g. /opt/bin)."
