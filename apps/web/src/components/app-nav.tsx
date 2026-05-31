import { NavLink } from "react-router-dom";

import { cn } from "@/lib/utils";

export function AppNav() {
  return (
    <nav className="flex gap-4 text-sm">
      <NavLink
        to="/"
        end
        className={({ isActive }) =>
          cn(
            "text-muted-foreground transition-colors hover:text-foreground",
            isActive && "font-medium text-foreground",
          )
        }
      >
        Render
      </NavLink>
      <NavLink
        to="/transcode"
        className={({ isActive }) =>
          cn(
            "text-muted-foreground transition-colors hover:text-foreground",
            isActive && "font-medium text-foreground",
          )
        }
      >
        Transcode
      </NavLink>
    </nav>
  );
}
