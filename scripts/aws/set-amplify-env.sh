#!/usr/bin/env bash
# Push env vars from a KEY=VALUE file to an Amplify app in one shot, merging with
# whatever is already set in the console (existing keys are overwritten, others kept).
#
# Usage:
#   scripts/set-amplify-env.sh <amplify-app-id> <env-file>
#
# Typical flow:
#   scripts/setup-cognito.sh my-app https://main.xxxx.amplifyapp.com > .env.amplify
#   scripts/set-amplify-env.sh dxxxxxxxxxxxx .env.amplify
#
# Remember: Amplify only applies env vars on the next build — redeploy after.
# Override defaults with env vars: REGION, PROFILE.
set -euo pipefail

AMPLIFY_APP_ID=${1:?usage: set-amplify-env.sh <amplify-app-id> <env-file>}
ENV_FILE=${2:?usage: set-amplify-env.sh <amplify-app-id> <env-file>}

REGION=${REGION:-eu-west-1}
PROFILE=${PROFILE:-admin}

if grep -q 'AUTH_URL=http://localhost' "$ENV_FILE"; then
  echo "WARNING: ${ENV_FILE} sets AUTH_URL to localhost — production needs the Amplify domain." >&2
fi

EXISTING=$(aws amplify get-app --app-id "$AMPLIFY_APP_ID" \
  --query 'app.environmentVariables' --output json \
  --region "$REGION" --profile "$PROFILE")

MERGED=$(python3 - "$ENV_FILE" "$EXISTING" <<'PY'
import json, sys

merged = json.loads(sys.argv[2]) or {}
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        merged[key.strip()] = value.strip()
print(json.dumps(merged))
PY
)

aws amplify update-app --app-id "$AMPLIFY_APP_ID" \
  --environment-variables "$MERGED" \
  --query 'app.environmentVariables' --output table \
  --region "$REGION" --profile "$PROFILE"

echo "Done. Trigger a redeploy for the new vars to take effect." >&2
