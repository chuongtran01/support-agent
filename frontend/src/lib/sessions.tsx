"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import type { Session } from "@/api/types";
import { useAuth } from "@/lib/auth";

interface Sessions {
  sessions: Session[];
  /** Fetch the list again, for after a chat starts or an answer finishes. */
  refresh: () => void;
}

const SessionsContext = createContext<Sessions | null>(null);

/** The customer's chats, shared by the sidebar and the chat page. */
export function SessionsProvider({ children }: { children: ReactNode }) {
  const { api } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loads, setLoads] = useState(0);

  useEffect(() => {
    let current = true;
    api
      .listSessions()
      .then((list) => current && setSessions(list))
      .catch(() => {}); // the list is a convenience; the chat works without it
    return () => {
      current = false;
    };
  }, [api, loads]);

  const refresh = useCallback(() => setLoads((n) => n + 1), []);
  return (
    <SessionsContext value={{ sessions, refresh }}>{children}</SessionsContext>
  );
}

export function useSessions(): Sessions {
  const value = useContext(SessionsContext);
  if (!value)
    throw new Error("useSessions must be used inside SessionsProvider");
  return value;
}
