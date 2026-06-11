import { NextResponse } from "next/server"

import { auth } from "@/auth"

/** Shared secret the FastAPI backend accepts in place of its own session cookie
 * (`BASERENDER_PROXY_TOKEN` on both sides). The Cognito session checked below is
 * what actually gates access; this header just carries that decision across the
 * rewrite proxy to the API. */
const PROXY_TOKEN = process.env["BASERENDER_PROXY_TOKEN"]

/** Requests that next.config.ts rewrites to the FastAPI backend. `/transcode` is
 * both a page (GET) and an API endpoint (POST) — only the POST is proxied. */
function isProxiedApiRequest(pathname: string, method: string): boolean {
  if (pathname === "/media" || pathname.startsWith("/media/")) return true
  if (pathname === "/jobs" || pathname.startsWith("/jobs/")) return true
  if (pathname === "/transcode" && method !== "GET") return true
  return false
}

// Gate every route: a session must exist AND still carry a role for this app. The
// role is dropped by the jwt callback when access is revoked (~1h after a group
// change), so checking it here is what enforces revocation, not just "is logged in".
export default auth((req) => {
  const apiRequest = isProxiedApiRequest(req.nextUrl.pathname, req.method)

  if (req.auth?.user?.role) {
    if (apiRequest && PROXY_TOKEN) {
      const headers = new Headers(req.headers)
      headers.set("authorization", `Bearer ${PROXY_TOKEN}`)
      return NextResponse.next({ request: { headers } })
    }
    return NextResponse.next()
  }

  // fetch() calls expect JSON, not a redirect to the sign-in page.
  if (apiRequest) {
    return NextResponse.json({ detail: "Authentication required." }, { status: 401 })
  }

  const signInUrl = new URL("/api/auth/signin", req.nextUrl.origin)
  signInUrl.searchParams.set("callbackUrl", req.nextUrl.href)
  return NextResponse.redirect(signInUrl)
})

export const config = {
  // Everything except Next internals, static assets, and the auth endpoints.
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico).*)"],
}
