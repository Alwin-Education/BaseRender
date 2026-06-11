/**
 * Per-app, role-based access derived from Cognito groups, with an email-domain
 * fallback.
 *
 * Identity lives in one shared Cognito user pool for the whole account; each app
 * authorises against its own groups named `<appId>:<role>` (e.g. `baserender:admin`).
 * A user's groups travel in the `cognito:groups` claim, so no database lookup is
 * needed — the token is the permission. Users with no group but a verified email
 * on an allowed domain get `DOMAIN_FALLBACK_ROLE`; see `resolveRole`.
 */

/** This app's identifier; the prefix of every group that grants access here.
 * Must match the `<appId>:*` Cognito groups created by
 * scripts/aws/setup-cognito.sh. */
export const APP_ID = "baserender"

/** Roles, highest privilege first. The order also defines precedence. */
export const ROLE_PRECEDENCE = ["admin", "editor", "viewer"] as const

export type Role = (typeof ROLE_PRECEDENCE)[number]

const GROUP_PREFIX = `${APP_ID}:`

function isRole(value: string): value is Role {
  return (ROLE_PRECEDENCE as readonly string[]).includes(value)
}

/** All valid roles this app's groups grant the user, in no particular order. */
export function rolesFromGroups(groups: readonly string[] | undefined): Role[] {
  if (!groups) {
    return []
  }
  return groups
    .filter((group) => group.startsWith(GROUP_PREFIX))
    .map((group) => group.slice(GROUP_PREFIX.length))
    .filter(isRole)
}

/** The highest-precedence role the user holds for this app, or null if none. */
export function highestRole(groups: readonly string[] | undefined): Role | null {
  const roles = rolesFromGroups(groups)
  for (const role of ROLE_PRECEDENCE) {
    if (roles.includes(role)) {
      return role
    }
  }
  return null
}

/** Email domains whose verified users get `DOMAIN_FALLBACK_ROLE` without a group.
 * Empty the list to require explicit group membership for everyone. */
export const ALLOWED_EMAIL_DOMAINS = ["alwineducation.com"] as const

/** Role granted by an allowed email domain alone; groups grant/override the rest. */
export const DOMAIN_FALLBACK_ROLE: Role = "viewer"

/** Lowercased part after the last `@`, or null if the value is not an email. */
export function emailDomain(email: string | null | undefined): string | null {
  const at = email?.lastIndexOf("@") ?? -1
  const domain = at > 0 ? email!.slice(at + 1).toLowerCase() : ""
  return domain ? domain : null
}

/** Whether the email's domain is on the allowlist (exact match, never a suffix). */
export function hasAllowedEmailDomain(email: string | null | undefined): boolean {
  const domain = emailDomain(email)
  return domain !== null && (ALLOWED_EMAIL_DOMAINS as readonly string[]).includes(domain)
}

/**
 * The user's effective role: the highest `<appId>:*` group wins; otherwise a
 * verified email on an allowed domain earns `DOMAIN_FALLBACK_ROLE`; otherwise
 * null (no access). `emailVerified` may be undefined when Cognito omits the
 * claim — email-OTP sign-in already proves ownership, so only an explicit
 * `false` blocks the fallback.
 */
export function resolveRole(
  groups: readonly string[] | undefined,
  email: string | null | undefined,
  emailVerified: boolean | undefined,
): Role | null {
  return (
    highestRole(groups) ??
    (emailVerified !== false && hasAllowedEmailDomain(email) ? DOMAIN_FALLBACK_ROLE : null)
  )
}
