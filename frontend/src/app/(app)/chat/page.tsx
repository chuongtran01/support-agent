"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Composer } from "@/components/composer";
import { Thread } from "@/components/thread";
import { useChat } from "@/chat/use-chat";
import { useAuth } from "@/lib/auth";
import { useSessions } from "@/lib/sessions";

function Chat() {
  const { api } = useAuth();
  const router = useRouter();
  const { refresh } = useSessions();
  // The open chat is in the address (?session=<id>), so the sidebar's links, Back and a reload
  // all open the right one. A chat made by the first message is written there as it is made.
  const requested = useSearchParams().get("session");
  const { state, sessionId, loading, open, isOpen, send } = useChat(api, {
    onCreated: (id) =>
      window.history.replaceState(null, "", `/chat?session=${id}`),
    onGone: () => router.replace("/chat"),
  });
  const [draft, setDraft] = useState("");
  const running = state.turn !== null;

  useEffect(() => {
    if (!isOpen(requested)) void open(requested);
  }, [requested, isOpen, open]);

  // The sidebar's list changes when a chat starts and when an answer ends (its preview is
  // the first message).
  useEffect(() => {
    if (!running) refresh();
  }, [running, sessionId, refresh]);

  async function onSend(text: string) {
    setDraft("");
    if (!(await send(text))) setDraft(text); // the message was not taken: give it back
  }

  function startNewChat() {
    void open(null);
    router.push("/chat");
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Thread
        messages={state.messages}
        turn={state.turn}
        error={state.error}
        loading={loading}
        onNewChat={startNewChat}
      />
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={(text) => void onSend(text)}
        disabled={running}
      />
    </div>
  );
}

export default function ChatPage() {
  // useSearchParams needs a Suspense boundary above it
  return (
    <Suspense fallback={null}>
      <Chat />
    </Suspense>
  );
}
