"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { createApi, ApiError, type Api } from "@/api/client";
import type { Profile } from "@/api/types";
import { clearToken, getToken, setToken } from "@/lib/token";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unreachable" }
  | { status: "signedIn"; profile: Profile };

interface Auth {
  state: AuthState;
  api: Api;
  login: (email: string, password: string) => Promise<Profile>;
  logout: () => void;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [api] = useState(() =>
    createApi({
      baseUrl: API_URL,
      getToken,
      // Any 401 means the token is no good: forget it, and the route guards go to /login.
      onUnauthorized: () => {
        clearToken();
        setState({ status: "anonymous" });
      },
    }),
  );

  // Ask the backend who the stored token belongs to.
  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!getToken()) return setState({ status: "anonymous" });
      try {
        const profile = await api.me();
        if (!cancelled) setState({ status: "signedIn", profile });
      } catch (error) {
        if (cancelled) return;
        // A 401 was already handled by onUnauthorized; anything else is a network failure.
        if (!(error instanceof ApiError)) setState({ status: "unreachable" });
        else setState({ status: "anonymous" });
      }
    }
    void restore();
    return () => {
      cancelled = true;
    };
  }, [api]);

  const login = async (email: string, password: string) => {
    const { token, customer } = await api.login(email, password);
    setToken(token);
    setState({ status: "signedIn", profile: customer });
    return customer;
  };

  const logout = () => {
    clearToken();
    setState({ status: "anonymous" });
  };

  return (
    <AuthContext value={{ state, api, login, logout }}>{children}</AuthContext>
  );
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside AuthProvider");
  return auth;
}

/** Where a signed-in person starts. */
export function homeFor(profile: Profile): string {
  return profile.role === "approver" ? "/approvals" : "/chat";
}
