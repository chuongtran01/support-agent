"use client";

import { Button } from "@/components/ui/button";

// Shown when a page fails while rendering (not when a request fails: those have their own states).
export default function ErrorPage({
  retry,
}: {
  error: unknown;
  retry: () => void;
}) {
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col items-start justify-center gap-3 p-6">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="text-muted-foreground">
        The page could not be shown. Try again, or reload it.
      </p>
      <Button onClick={() => retry()}>Try again</Button>
    </main>
  );
}
