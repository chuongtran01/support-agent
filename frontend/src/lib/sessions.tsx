"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { Session } from "@/api/types";
import { useLoad } from "@/lib/use-load";

interface Sessions {
  sessions: Session[];
  /** Fetch the list again, for after a chat starts or an answer finishes. */
  refresh: () => void;
}

const SessionsContext = createContext<Sessions | null>(null);

/** The customer's chats, shared by the sidebar and the chat page. */
export function SessionsProvider({ children }: { children: ReactNode }) {
  // The list is a convenience, so a failure to fetch it is not shown: the chat works without it.
  const { data, reload } = useLoad((api) => api.listSessions());
  return (
    <SessionsContext value={{ sessions: data ?? [], refresh: reload }}>
      {children}
    </SessionsContext>
  );
}

export function useSessions(): Sessions {
  const value = useContext(SessionsContext);
  if (!value)
    throw new Error("useSessions must be used inside SessionsProvider");
  return value;
}
