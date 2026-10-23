"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, type Api } from "@/api/client";
import { chatReducer, initialChatState, type ChatError } from "./reducer";

const UNREACHABLE =
  "Can't reach the server. Check your connection and try again.";

function toChatError(error: unknown): ChatError {
  if (error instanceof ApiError) {
    if (error.code === "turn_in_progress")
      return { message: "Still answering the last message.", code: error.code };
    return { message: error.message, code: error.code };
  }
  return { message: UNREACHABLE };
}

export interface ChatCallbacks {
  /** A chat was made for the first message. The page puts its id in the address. */
  onCreated: (id: string) => void;
  /** The open chat does not exist (any more). The page takes its id out of the address. */
  onGone: () => void;
}

/** One open chat: its messages, the answer being written, and the calls that change them. */
export function useChat(api: Api, callbacks: ChatCallbacks) {
  const handlers = useRef(callbacks);
  useEffect(() => {
    handlers.current = callbacks;
  });
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false); // a saved chat is being fetched
  const current = useRef<string | null>(null); // the open session, readable inside async code
  const generation = useRef(0); // bumped whenever the chat is switched, so late results are ignored
  const stream = useRef<AbortController | null>(null);

  const select = useCallback((id: string | null) => {
    current.current = id;
    setSessionId(id);
  }, []);

  const isOpen = useCallback((id: string | null) => current.current === id, []);

  /** Switches to a saved chat, or to a new one when `id` is null. */
  const open = useCallback(
    async (id: string | null) => {
      const mine = ++generation.current;
      stream.current?.abort();
      select(id);
      dispatch({ type: "reset" });
      setLoading(id !== null);
      if (!id) return;
      try {
        const messages = await api.messages(id);
        if (mine === generation.current) dispatch({ type: "loaded", messages });
      } catch (error) {
        if (mine !== generation.current) return;
        if (error instanceof ApiError && error.status === 404) {
          select(null);
          handlers.current.onGone();
          dispatch({
            type: "failed",
            error: {
              message: "That chat was not found. Start a new one below.",
            },
          });
        } else {
          dispatch({ type: "failed", error: toChatError(error) });
        }
      } finally {
        if (mine === generation.current) setLoading(false);
      }
    },
    [api, select],
  );

  /** Sends a message and plays the answer into the state. Resolves false if it was not taken. */
  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const mine = ++generation.current;
      const reject = (error: unknown) => {
        if (error instanceof ApiError && error.status === 404) {
          select(null); // the chat is gone, so the next message starts a new one
          handlers.current.onGone();
          dispatch({
            type: "rejected",
            error: {
              message:
                "This chat no longer exists. Send your message again to start a new one.",
            },
          });
        } else {
          dispatch({ type: "rejected", error: toChatError(error) });
        }
        return false;
      };

      dispatch({ type: "sent", text });
      let id = current.current;
      if (!id) {
        try {
          id = (await api.createSession()).id;
        } catch (error) {
          return mine === generation.current ? reject(error) : true;
        }
        if (mine !== generation.current) return true;
        select(id);
        handlers.current.onCreated(id);
      }

      const controller = new AbortController();
      stream.current = controller;
      let started = false;
      try {
        for await (const event of api.sendMessage(
          id,
          text,
          controller.signal,
        )) {
          if (mine !== generation.current) return true;
          started = true;
          dispatch({ type: "event", event });
        }
        if (mine === generation.current) dispatch({ type: "closed" });
      } catch (error) {
        if (mine !== generation.current || controller.signal.aborted)
          return true;
        if (!started) return reject(error); // nothing arrived, so the message was not taken
        dispatch({
          type: "failed",
          error: {
            message:
              "Lost the connection. Reload the chat to see the answer when it is ready.",
          },
        });
      }
      return true;
    },
    [api, select],
  );

  // Leaving the page drops the stream; the backend still finishes and saves the turn.
  useEffect(
    () => () => {
      generation.current++;
      stream.current?.abort();
    },
    [],
  );

  return { state, sessionId, loading, open, isOpen, send };
}
