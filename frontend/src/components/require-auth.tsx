"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";

/**
 * Shows its children only to a signed-in person, and only to the given role if there is
 * one. This decides what to show; the backend still checks the role on every call.
 */
export function RequireAuth({
  role,
  children,
}: {
  role?: string;
  children: ReactNode;
}) {
  const { state } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (state.status === "anonymous") router.replace("/login");
  }, [state.status, router]);

  if (state.status === "loading" || state.status === "anonymous") {
    return (
      <p role="status" className="p-6 text-muted-foreground">
        Loading…
      </p>
    );
  }
  if (state.status === "unreachable") {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 p-6">
        <p>Can&apos;t reach the server. Check that the backend is running.</p>
        <Button onClick={() => window.location.reload()}>Try again</Button>
      </div>
    );
  }
  if (role && state.profile.role !== role) {
    return (
      <div role="alert" className="p-6">
        <h1 className="text-xl font-semibold">Not allowed</h1>
        <p className="text-muted-foreground">
          Your account can&apos;t open this page.
        </p>
      </div>
    );
  }
  return children;
}
