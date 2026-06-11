#!/usr/bin/env bash
# One-shot Cognito setup for a new app on the shared user pool:
#   - creates the <app-id>:admin/editor/viewer groups (skips ones that exist)
#   - creates an app client (code grant + refresh, 1h tokens, managed login branding)
#   - prints the six env vars ready for .env.local / set-amplify-env.sh
#
# Usage:
#   scripts/setup-cognito.sh <app-id> [https://prod-domain]
#
# Run it AFTER creating the Amplify app so you can pass the production domain;
# with no domain it registers only the localhost callbacks (add prod later with
# `aws cognito-idp update-user-pool-client`).
#
# Override defaults with env vars: POOL_ID, REGION, PROFILE.
set -euo pipefail

APP_ID=${1:?usage: setup-cognito.sh <app-id> [https://prod-domain]}
PROD_URL=${2:-}

POOL_ID=${POOL_ID:-eu-west-1_FJ7wwJmRs}
REGION=${REGION:-eu-west-1}
PROFILE=${PROFILE:-admin}

AWS=(aws cognito-idp --region "$REGION" --profile "$PROFILE")

echo "== Groups (${APP_ID}:admin/editor/viewer) on pool ${POOL_ID}" >&2
precedence=1
for role in admin editor viewer; do
  if "${AWS[@]}" create-group \
    --user-pool-id "$POOL_ID" \
    --group-name "${APP_ID}:${role}" \
    --precedence "$precedence" >/dev/null 2>&1; then
    echo "  created ${APP_ID}:${role}" >&2
  else
    echo "  ${APP_ID}:${role} already exists, skipping" >&2
  fi
  precedence=$((precedence + 1))
done

CALLBACKS=("http://localhost:3000/api/auth/callback/cognito")
LOGOUTS=("http://localhost:3000")
if [[ -n "$PROD_URL" ]]; then
  CALLBACKS+=("${PROD_URL}/api/auth/callback/cognito")
  LOGOUTS+=("$PROD_URL")
fi

echo "== App client '${APP_ID}'" >&2
read -r CLIENT_ID CLIENT_SECRET < <("${AWS[@]}" create-user-pool-client \
  --user-pool-id "$POOL_ID" \
  --client-name "$APP_ID" \
  --generate-secret \
  --supported-identity-providers COGNITO \
  --callback-urls "${CALLBACKS[@]}" \
  --logout-urls "${LOGOUTS[@]}" \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email profile \
  --allowed-o-auth-flows-user-pool-client \
  --explicit-auth-flows ALLOW_USER_AUTH ALLOW_REFRESH_TOKEN_AUTH \
  --token-validity-units AccessToken=hours,IdToken=hours,RefreshToken=days \
  --access-token-validity 1 \
  --id-token-validity 1 \
  --refresh-token-validity 30 \
  --query 'UserPoolClient.[ClientId,ClientSecret]' --output text)
echo "  client id: ${CLIENT_ID}" >&2

# New app clients need a managed-login branding style or the hosted sign-in page
# 404s; reuse Cognito's default look.
if "${AWS[@]}" create-managed-login-branding \
  --user-pool-id "$POOL_ID" \
  --client-id "$CLIENT_ID" \
  --use-cognito-provided-values >/dev/null 2>&1; then
  echo "  managed login branding attached" >&2
else
  echo "  managed login branding: skipped (already set or classic hosted UI)" >&2
fi

AUTH_SECRET=$(openssl rand -base64 32)

echo "" >&2
echo "== Env vars (paste into .env.local, or > .env.amplify for set-amplify-env.sh)" >&2
cat <<EOF
COGNITO_CLIENT_ID=${CLIENT_ID}
COGNITO_CLIENT_SECRET=${CLIENT_SECRET}
COGNITO_ISSUER=https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}
AUTH_SECRET=${AUTH_SECRET}
AUTH_URL=${PROD_URL:-http://localhost:3000}
AUTH_TRUST_HOST=true
EOF

echo "" >&2
echo "Next: grant yourself admin —" >&2
echo "  aws cognito-idp admin-add-user-to-group --user-pool-id ${POOL_ID} \\" >&2
echo "    --username you@example.com --group-name ${APP_ID}:admin --region ${REGION} --profile ${PROFILE}" >&2
