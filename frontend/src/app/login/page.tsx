"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { homeFor, useAuth } from "@/lib/auth";

// The seeded customers (backend/app/seed.py). Every one has the SEED_PASSWORD, "demo" by default.
const DEMO_PASSWORD = "demo";
const DEMO_ACCOUNTS = [
  { email: "alex@example.com", name: "Alex Rivera", note: "Basic member" },
  { email: "priya@example.com", name: "Priya Shah", note: "Plus member" },
  { email: "sam@example.com", name: "Sam Okafor", note: "No membership" },
  { email: "jordan@example.com", name: "Jordan Lee", note: "Basic member" },
  { email: "morgan@example.com", name: "Morgan Chen", note: "Basic member" },
  { email: "approver@example.com", name: "Taylor Brooks", note: "Approver" },
];

export default function LoginPage() {
  const { state, login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Someone who is already signed in has no use for this page.
  useEffect(() => {
    if (state.status === "signedIn") router.replace(homeFor(state.profile));
  }, [state, router]);

  async function signIn(email: string, password: string) {
    setBusy(true);
    setError(null);
    try {
      router.replace(homeFor(await login(email, password)));
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 401
          ? "Wrong email or password."
          : "Can't reach the server. Check that the backend is running.",
      );
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void signIn(email, password);
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-muted/40 p-4">
      <div className="flex w-full max-w-md flex-col gap-4">
        <div className="text-center">
          <h1 className="text-2xl font-semibold tracking-tight">
            Northwind Outfitters
          </h1>
          <p className="text-sm text-muted-foreground">Customer support</p>
        </div>
        {error && (
          <p role="alert" className="text-center text-sm text-destructive">
            {error}
          </p>
        )}
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-base">Demo accounts</CardTitle>
            <CardDescription>
              One click signs in with the seeded password.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {DEMO_ACCOUNTS.map((account) => (
              <Button
                key={account.email}
                variant="outline"
                disabled={busy}
                className="h-auto flex-col items-start gap-0.5 px-3 py-2.5"
                onClick={() => void signIn(account.email, DEMO_PASSWORD)}
              >
                <span>{account.name}</span>
                <span className="text-xs font-normal text-muted-foreground">
                  {account.note}
                </span>
              </Button>
            ))}
          </CardContent>
        </Card>
        <Card className="shadow-sm">
          <CardHeader>
            <CardTitle className="text-base">
              Or sign in with an email
            </CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="flex flex-col gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  className="h-9"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  className="h-9"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <Button type="submit" size="lg" className="mt-1" disabled={busy}>
                Sign in
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
