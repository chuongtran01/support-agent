"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Composer, MAX_CHARACTERS } from "@/components/composer";
import { Thread } from "@/components/thread";
import { useChat } from "@/chat/use-chat";
import { useApi } from "@/lib/auth";
import { useSessions } from "@/lib/sessions";

function Chat() {
  const api = useApi();
  const router = useRouter();
  const { refresh } = useSessions();
  // The open chat is in the address (?session=<id>), so the sidebar's links, Back and a reload
  // all open the right one. A chat made by the first message is written there as it is made.
  const params = useSearchParams();
  const requested = params.get("session");
  // "Ask about this" on the orders page opens the chat with a question in the box, to send or edit.
  const [asked] = useState(
    () => params.get("ask")?.slice(0, MAX_CHARACTERS) ?? "",
  );
  const { state, loading, open, isOpen, send } = useChat(api, {
    onCreated: (id) =>
      window.history.replaceState(null, "", `/chat?session=${id}`),
    onGone: () => router.replace("/chat"),
  });
  const [draft, setDraft] = useState(asked);
  const [focusSignal, setFocusSignal] = useState(0);
  const running = state.turn !== null;

  useEffect(() => {
    if (asked) router.replace("/chat"); // the question is in the box now; keep it out of the address
  }, [asked, router]);

  useEffect(() => {
    if (!isOpen(requested)) void open(requested);
  }, [requested, isOpen, open]);

  // The sidebar's list changes when an answer ends (a new chat's preview is its first message).
  // It is not fetched on arrival: the sessions provider already did that.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) refresh();
    wasRunning.current = running;
  }, [running, refresh]);

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
        onSuggest={(text) => {
          setDraft(text);
          setFocusSignal((n) => n + 1);
        }}
      />
      <Composer
        value={draft}
        onChange={setDraft}
        onSend={(text) => void onSend(text)}
        disabled={running}
        autoFocus={asked !== ""}
        focusSignal={focusSignal}
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
