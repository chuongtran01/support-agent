"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

const MEMBERSHIPS: Record<string, string> = {
  basic: "Basic member",
  plus: "Plus member",
};

export function Header() {
  const { state, logout } = useAuth();
  const pathname = usePathname();
  if (state.status !== "signedIn") return null;
  const { name, role, membership } = state.profile;
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-3">
      <span className="font-semibold">Northwind Outfitters</span>
      <nav aria-label="Main" className="flex gap-3 text-sm">
        <Link
          href="/chat"
          aria-current={pathname === "/chat" ? "page" : undefined}
          className="underline-offset-4 hover:underline aria-[current=page]:font-medium aria-[current=page]:underline"
        >
          Chat
        </Link>
        {role === "approver" && (
          <Link
            href="/approvals"
            aria-current={pathname === "/approvals" ? "page" : undefined}
            className="underline-offset-4 hover:underline aria-[current=page]:font-medium aria-[current=page]:underline"
          >
            Approvals
          </Link>
        )}
      </nav>
      <div className="ml-auto flex items-center gap-3 text-sm">
        <span>
          {name}
          {MEMBERSHIPS[membership] && (
            <span className="text-muted-foreground">
              {" "}
              · {MEMBERSHIPS[membership]}
            </span>
          )}
        </span>
        <Button variant="outline" size="sm" onClick={logout}>
          Log out
        </Button>
      </div>
    </header>
  );
}
