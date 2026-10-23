"use client";

import { Menu } from "lucide-react";
import { useEffect, useState } from "react";

import type { Session } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Composer } from "@/components/composer";
import { SessionList } from "@/components/session-list";
import { Thread } from "@/components/thread";
import { useChat } from "@/chat/use-chat";
import { useAuth } from "@/lib/auth";

export default function ChatPage() {
  const { api } = useAuth();
  const { state, sessionId, loading, open, send } = useChat(api);
  const [drawer, setDrawer] = useState(false); // the chat list on a phone
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
        <div className="flex items-center border-b px-2 py-1.5 md:hidden">
          <Sheet open={drawer} onOpenChange={setDrawer}>
            <SheetTrigger render={<Button variant="ghost" size="sm" />}>
              <Menu /> Chats
            </SheetTrigger>
            <SheetContent side="left" className="p-0">
              <SheetHeader className="sr-only">
                <SheetTitle>Chats</SheetTitle>
                <SheetDescription>
                  Your earlier chats, and a button to start a new one.
                </SheetDescription>
              </SheetHeader>
              <div className="pt-10">
                <SessionList
                  sessions={sessions}
                  activeId={sessionId}
                  onOpen={(id) => {
                    setDrawer(false);
                    void open(id);
                  }}
                  onNew={() => {
                    setDrawer(false);
                    void open(null);
                  }}
                />
              </div>
            </SheetContent>
          </Sheet>
        </div>
        <Thread
          messages={state.messages}
          turn={state.turn}
          error={state.error}
          loading={loading}
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
