import { type DefaultSession } from "next-auth"

import { type Role } from "@/lib/access"

declare module "next-auth" {
  interface Session {
    user: {
      /** Highest role the user's `<APP_ID>:*` groups grant, or the domain-fallback
       * viewer role; absent once access is revoked. */
      role?: Role
    } & DefaultSession["user"]
    /** Set when group re-validation fails or access was revoked. */
    error?: string
  }
}

// The JWT interface is declared in @auth/core/jwt (next-auth/jwt only re-exports it),
// so the augmentation must target that module to merge.
declare module "@auth/core/jwt" {
  interface JWT {
    role?: Role
    email?: string
    refreshToken?: string
    /** Access-token expiry in epoch milliseconds; drives the ~1h re-validation. */
    expiresAt?: number
    error?: string
  }
}
