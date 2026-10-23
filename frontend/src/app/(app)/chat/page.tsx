"use client";

import { useEffect, useState } from "react";

import type { Session } from "@/api/types";
import { Composer } from "@/components/composer";
import { SessionList } from "@/components/session-list";
import { Thread } from "@/components/thread";
import { useChat } from "@/chat/use-chat";
import { useAuth } from "@/lib/auth";

export default function ChatPage() {
  const { api } = useAuth();
  const { state, sessionId, open, send } = useChat(api);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [draft, setDraft] = useState("");
  // The open chat is kept in the address (?session=...) so a reload comes back to it. It is
  // read once; after that the address only follows the state.
  const [fromAddress] = useState(() =>
    typeof window === "undefined"
      ? null
      : new URLSearchParams(window.location.search).get("session"),
  );
  const running = state.turn !== null;

  useEffect(() => {
    if (fromAddress) void open(fromAddress);
  }, [fromAddress, open]);

  useEffect(() => {
    window.history.replaceState(
      null,
      "",
      sessionId ? `?session=${sessionId}` : window.location.pathname,
    );
  }, [sessionId]);

  // The list changes when a chat starts and when an answer finishes (the preview is the first message).
  useEffect(() => {
    if (running) return;
    let current = true;
    api
      .listSessions()
      .then((list) => current && setSessions(list))
      .catch(() => {}); // the list is a convenience; the chat works without it
    return () => {
      current = false;
    };
  }, [api, running, sessionId]);

  async function onSend(text: string) {
    setDraft("");
    if (!(await send(text))) setDraft(text); // the message was not taken: give it back
  }

  return (
    <div className="flex h-full">
      <aside className="hidden w-64 shrink-0 border-r md:block">
        <SessionList
          sessions={sessions}
          activeId={sessionId}
          onOpen={(id) => void open(id)}
          onNew={() => void open(null)}
        />
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <Thread
          messages={state.messages}
          turn={state.turn}
          error={state.error}
          onNewChat={() => void open(null)}
        />
        <Composer
          value={draft}
          onChange={setDraft}
          onSend={(text) => void onSend(text)}
          disabled={running}
        />
      </section>
    </div>
  );
}
