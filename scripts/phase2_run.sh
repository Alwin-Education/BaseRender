#!/usr/bin/env bash
# Phase 2 orchestration: local secrets, AWS setup (when configured), validation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

source .venv/bin/activate

python scripts/aws/bootstrap_local_env.py
python scripts/generate_phase2_fixtures.py

if aws sts get-caller-identity >/dev/null 2>&1; then
  if [[ -z "${BASERENDER_S3_BUCKET:-}" ]] || [[ "${AWS_ACCESS_KEY_ID:-}" == "your-access-key-id" ]]; then
    BUCKET="${BASERENDER_DEV_BUCKET:-baserender-dev-$(date +%s)}"
    echo "Provisioning CloudFormation stack with bucket ${BUCKET}..."
    python scripts/aws/provision_stack.py --bucket-name "$BUCKET"
  fi
  python scripts/aws/setup_dev.py
else
  echo "AWS credentials not configured. Run: aws configure"
  echo "Then: python scripts/aws/provision_stack.py --bucket-name baserender-dev-UNIQUE"
  echo "      python scripts/aws/setup_dev.py"
fi

npm run phase2:test

echo "Starting validation (local checks; add --skip flags if AWS not ready)..."
python scripts/phase2_validate.py --skip-transcode --skip-cloud-jobs "$@"
