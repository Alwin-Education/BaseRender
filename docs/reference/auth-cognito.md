# Authentication — shared Cognito SSO with per-app, role-based access

The web app (`apps/web`) is gated by [Auth.js v5](https://authjs.dev) (`next-auth@beta`)
federating a **single, account-wide AWS Cognito user pool**. Sign-in is **passwordless
email one-time code** — no passwords. Per-app access is granted via **Cognito groups**,
so the same pool serves every internal tool, each authorising independently.

## BaseRender specifics: the FastAPI bridge

The FastAPI backend keeps its own auth middleware, but the browser no longer logs in
to it. Instead, the Next.js middleware injects `Authorization: Bearer
<BASERENDER_PROXY_TOKEN>` on every API request it proxies (`/media`, `/jobs`,
`POST /transcode`), and FastAPI accepts that shared token in place of its session
cookie (`is_valid_proxy_bearer` in `apps/api/src/baserender_api/auth/session.py`).
Set the same `BASERENDER_PROXY_TOKEN` value in `apps/web/.env.local` (or the Amplify
env) and `apps/api/.env`; leaving it unset on the API disables the bridge. The legacy
`/auth/login` password flow still exists on FastAPI but is unused by the UI, and goes
away entirely with the unified-Lambda cutover (roadmap Phase 3).

## How it works

- **Identity:** one shared Cognito user pool. Each app is its own *app client* in that pool.
- **Sign-in:** email OTP via Cognito's Managed Login. The code proves email ownership.
- **Authorization:** Cognito groups named `<appId>:<role>`, with roles `admin`,
  `editor`, `viewer` (see [`lib/access.ts`](../../apps/web/src/lib/access.ts), where `APP_ID` is set).
  - Access = membership in any `<appId>:*` group, **or** a verified email on an allowed
    domain (`ALLOWED_EMAIL_DOMAINS` in [`lib/access.ts`](../../apps/web/src/lib/access.ts)), which earns
    the default role `viewer`.
  - Role = the highest such group (admin > editor > viewer), surfaced as
    `session.user.role`. Groups always override the domain fallback.
  - A user with neither is authenticated but refused a session here.
- **Session:** rolling **30 days** — active users almost never re-enter a code; explicit
  sign-out forces a fresh one.
- **Revocation ≈ 1 hour:** the Cognito access token expires hourly; Auth.js then uses the
  stored refresh token to fetch a fresh ID token and re-reads the user's current groups
  (see the `jwt` callback in [`auth.ts`](../../apps/web/src/auth.ts)). Removing a user from a group takes
  effect within ~1h, with no per-request DB/API lookup.

Enforcement is layered: the `signIn` callback refuses to mint a session unless
`resolveRole` (groups-or-domain) yields a role; the `jwt` callback drops the role when
access is revoked; and [`middleware.ts`](../../apps/web/src/middleware.ts) gates **every** route on
`session.user.role`. Mutating API routes should additionally require `editor`/`admin`
(BaseRender's API calls are gated again by FastAPI behind the proxy bridge above).

## Per-app Cognito setup

Automated — run [`scripts/aws/setup-cognito.sh`](../../scripts/aws/setup-cognito.sh) once per new
app. It creates the `<appId>:*` groups and the app client (code + refresh grants, scopes
`openid email profile`, 1h token TTL, both localhost and production callback URLs) and
prints the six env vars.

The **shared pool itself** is a one-time, account-level setup (already done): sign-in
attribute email, self-registration enabled, feature tier Essentials, email-OTP sign-in
with Managed Login.

## Granting and managing access

All commands take `--region eu-west-1 --profile admin` plus the shared pool id.

### The domain whitelist

Anyone who signs in with a verified email on an allowed domain automatically gets the
`viewer` role — they just self-register on the sign-in page (email + OTP). To change the
domain list or fallback role, edit [`lib/access.ts`](../../apps/web/src/lib/access.ts) and redeploy.

### Grant a specific email (outside the domain)

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <POOL_ID> \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS \
  --region eu-west-1 --profile admin

aws cognito-idp admin-add-user-to-group \
  --user-pool-id <POOL_ID> \
  --username user@example.com \
  --group-name <appId>:viewer \
  --region eu-west-1 --profile admin
```

`--message-action SUPPRESS` skips the temporary-password invite email, which is
meaningless in a passwordless pool. (If they already self-registered — e.g. for another
app on the pool — skip `admin-create-user` and only add the group.)

### Promote / demote

Add/remove `<appId>:*` groups (`admin-add-user-to-group` / `admin-remove-user-from-group`).
Takes effect within ~1h, or immediately on re-login.

### Revoke

- **Non-domain users:** remove them from all `<appId>:*` groups. Access drops within ~1h.
- **Domain users: group removal is NOT enough** — they fall back to `viewer`. Disable the
  account instead (`admin-disable-user`). Note this revokes them from **every** app on
  the shared pool.

## Environment variables

All server-side only — **never** prefix with `NEXT_PUBLIC_`.
`scripts/aws/setup-cognito.sh` prints all six, ready to use.

| Variable | Value |
| -------- | ----- |
| `COGNITO_CLIENT_ID` | this app's client id |
| `COGNITO_CLIENT_SECRET` | this app's client secret |
| `COGNITO_ISSUER` | `https://cognito-idp.eu-west-1.amazonaws.com/<userPoolId>` |
| `AUTH_SECRET` | `openssl rand -base64 32` — unique per app, never shared |
| `AUTH_URL` | `http://localhost:3000` locally; `https://<amplify-domain>` in prod |
| `AUTH_TRUST_HOST` | `true` (required on Amplify — non-Vercel host) |

The token endpoint for refresh is discovered automatically from `COGNITO_ISSUER`'s
`/.well-known/openid-configuration`, so no extra var is needed.

Locally: put them in `.env.local` (gitignored). On Amplify: push with
[`scripts/aws/set-amplify-env.sh`](../../scripts/aws/set-amplify-env.sh) and **redeploy** —
[`amplify.yml`](../../amplify.yml) writes them into `.env.production` at build time (the
SSR Lambda can't see console vars otherwise).

## Verifying

1. **Ungrouped, non-domain user:** sign in → email verifies but no session / no access.
2. **Domain fallback:** ungrouped allowed-domain email → access with
   `session.user.role === "viewer"` (check `/api/auth/session`).
3. Add a non-domain user to `<appId>:viewer`, sign in → access as `viewer`.
4. Promote to `<appId>:admin`, re-login → role updates.
5. **Revocation:** drop the app client's access-token TTL to its 5-min minimum, remove
   the user's groups, keep the session open → access lost within the TTL without
   re-login. For a **domain** user use `admin-disable-user` instead. Restore TTL to 1h.
6. `GET /media/config` with no session → 401 JSON; signed in → 200 via the proxy bridge.
7. Sign out → routes gated again.
