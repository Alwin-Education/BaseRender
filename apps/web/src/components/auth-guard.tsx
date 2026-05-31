import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

type AuthGuardProps = {
  children: ReactNode;
};

export function AuthGuard({ children }: AuthGuardProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [status, setStatus] = useState<"loading" | "authenticated" | "unauthenticated">(
    "loading",
  );

  useEffect(() => {
    let cancelled = false;

    async function checkSession() {
      try {
        const response = await fetch("/auth/session", {
          credentials: "include",
          headers: { Accept: "application/json" },
        });

        if (!response.ok) {
          if (!cancelled) {
            setStatus("unauthenticated");
          }
          return;
        }

        const payload = (await response.json()) as { authenticated?: boolean };
        if (!cancelled) {
          setStatus(payload.authenticated ? "authenticated" : "unauthenticated");
        }
      } catch {
        if (!cancelled) {
          setStatus("unauthenticated");
        }
      }
    }

    void checkSession();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (status !== "unauthenticated") {
      return;
    }

    const nextPath = `${location.pathname}${location.search}`;
    const params = new URLSearchParams();
    if (nextPath !== "/") {
      params.set("next", nextPath);
    }
    const query = params.toString();
    navigate(query ? `/login?${query}` : "/login", { replace: true });
  }, [location.pathname, location.search, navigate, status]);

  if (status === "loading") {
    return null;
  }

  if (status === "unauthenticated") {
    return null;
  }

  return children;
}
