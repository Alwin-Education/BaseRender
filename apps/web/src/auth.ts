import NextAuth from "next-auth"
import Cognito from "next-auth/providers/cognito"

import { resolveRole } from "@/lib/access"

/** Bracket access so Next.js does not inline undefined at build time on Amplify. */
const clientId = process.env["COGNITO_CLIENT_ID"]
const clientSecret = process.env["COGNITO_CLIENT_SECRET"]
const issuer = process.env["COGNITO_ISSUER"]

const THIRTY_DAYS = 60 * 60 * 24 * 30

/** Decode a JWT payload without verifying — used only on tokens Cognito just
 * handed us over TLS. Edge-safe (no Node Buffer). */
function decodeJwtPayload(jwt: string | undefined): Record<string, unknown> | null {
  if (!jwt) return null
  const part = jwt.split(".")[1]
  if (!part) return null
  try {
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/")
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), "=")
    return JSON.parse(atob(padded)) as Record<string, unknown>
  } catch {
    return null
  }
}

function groupsFromClaim(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []
}

interface AuthClaims {
  groups: string[]
  email?: string
  emailVerified?: boolean
}

/** Pull the claims access control needs from a Cognito id_token. Email and its
 * verified flag come from here rather than `profile` — the userinfo endpoint
 * behind `profile` can stringify `email_verified`, the id_token keeps it boolean. */
function claimsFromIdToken(idToken: string | undefined): AuthClaims {
  const payload = decodeJwtPayload(idToken)
  return {
    groups: groupsFromClaim(payload?.["cognito:groups"]),
    email: typeof payload?.["email"] === "string" ? payload["email"] : undefined,
    emailVerified:
      typeof payload?.["email_verified"] === "boolean" ? payload["email_verified"] : undefined,
  }
}

/** Cognito's OAuth2 token endpoint lives on the hosted domain, which we read from
 * OIDC discovery so we don't need to hard-code another env var. Cached per runtime. */
let tokenEndpointPromise: Promise<string> | null = null
function getTokenEndpoint(): Promise<string> {
  if (!tokenEndpointPromise) {
    tokenEndpointPromise = fetch(`${issuer}/.well-known/openid-configuration`)
      .then((res) => res.json())
      .then((doc: { token_endpoint?: string }) => {
        if (!doc.token_endpoint) throw new Error("No token_endpoint in Cognito discovery doc")
        return doc.token_endpoint
      })
      .catch((err) => {
        tokenEndpointPromise = null // allow retry on next call
        throw err
      })
  }
  return tokenEndpointPromise
}

/** Exchange the stored refresh token for fresh tokens so we pick up the user's
 * current `cognito:groups` and email claims (~1h revocation window). Cognito does
 * not rotate the refresh token here, so we keep the existing one. */
async function refreshClaims(refreshToken: string): Promise<AuthClaims> {
  const tokenEndpoint = await getTokenEndpoint()
  const res = await fetch(tokenEndpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Basic ${btoa(`${clientId}:${clientSecret}`)}`,
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      client_id: clientId ?? "",
      refresh_token: refreshToken,
    }),
  })
  if (!res.ok) {
    throw new Error(`Cognito refresh failed: ${res.status}`)
  }
  const data = (await res.json()) as { id_token?: string }
  return claimsFromIdToken(data.id_token)
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  session: { strategy: "jwt", maxAge: THIRTY_DAYS, updateAge: 60 * 60 * 24 },
  providers: [
    Cognito({
      clientId,
      clientSecret,
      issuer,
      authorization: { params: { scope: "openid email profile" } },
    }),
  ],
  callbacks: {
    // Authoritative gate: refuse to mint a session unless the user has an
    // `<APP_ID>:*` group or a verified email on an allowed domain (see resolveRole).
    async signIn({ account, profile }) {
      const claims = claimsFromIdToken(account?.id_token as string | undefined)
      const groups = claims.groups.length
        ? claims.groups
        : groupsFromClaim(profile?.["cognito:groups"])
      const email = claims.email ?? (profile?.email as string | undefined)
      return resolveRole(groups, email, claims.emailVerified) !== null
    },
    async jwt({ token, account, profile }) {
      // Initial sign-in: capture role, email, and the refresh token.
      if (account) {
        const claims = claimsFromIdToken(account.id_token)
        const groups = claims.groups.length
          ? claims.groups
          : groupsFromClaim(profile?.["cognito:groups"])
        const email = claims.email ?? (profile?.email as string | undefined) ?? token.email
        token.role = resolveRole(groups, email, claims.emailVerified) ?? undefined
        token.email = email
        token.refreshToken = account.refresh_token
        token.expiresAt = account.expires_at ? account.expires_at * 1000 : Date.now()
        token.error = undefined
        return token
      }

      // Still within the access-token lifetime — trust the cached role.
      if (typeof token.expiresAt === "number" && Date.now() < token.expiresAt) {
        return token
      }

      // Access token expired — re-validate against Cognito (~1h cadence).
      if (!token.refreshToken) {
        token.error = "RefreshTokenMissing"
        token.role = undefined
        return token
      }
      try {
        const { groups, email, emailVerified } = await refreshClaims(token.refreshToken)
        if (email) token.email = email
        token.role = resolveRole(groups, email ?? token.email, emailVerified) ?? undefined
        // Cognito ID/access tokens default to a 1h lifetime.
        token.expiresAt = Date.now() + 60 * 60 * 1000
        token.error = token.role ? undefined : "AccessRevoked"
      } catch {
        token.error = "RefreshFailed"
        token.role = undefined
      }
      return token
    },
    async session({ session, token }) {
      session.user.role = token.role
      if (token.email) session.user.email = token.email
      session.error = token.error
      return session
    },
  },
})
