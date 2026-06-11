"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";

import { cn } from "@/lib/utils";

export function AppNav() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-4 text-sm">
      <Link
        href="/"
        className={cn(
          "text-muted-foreground transition-colors hover:text-foreground",
          pathname === "/" && "font-medium text-foreground",
        )}
      >
        Render
      </Link>
      <Link
        href="/transcode"
        className={cn(
          "text-muted-foreground transition-colors hover:text-foreground",
          pathname === "/transcode" && "font-medium text-foreground",
        )}
      >
        Transcode
      </Link>
      <button
        type="button"
        onClick={() => void signOut({ callbackUrl: "/" })}
        className="text-muted-foreground transition-colors hover:text-foreground"
      >
        Sign out
      </button>
    </nav>
  );
}
