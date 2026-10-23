import { ApiError, type Http } from "./http";
import { readEvents } from "./sse";
import type { Message, Session, StreamEvent } from "./types";

export const chatApi = ({ json, send, post }: Http) => ({
  listSessions: () => json<Session[]>("/sessions"),
  createSession: () => json<Session>("/sessions", { method: "POST" }),
  messages: (sessionId: string) =>
    json<Message[]>(`/sessions/${sessionId}/messages`),

  /** Sends a message and yields the turn's events as they arrive. */
  async *sendMessage(
    sessionId: string,
    text: string,
    signal?: AbortSignal,
  ): AsyncGenerator<StreamEvent> {
    const response = await send(`/sessions/${sessionId}/messages`, {
      ...post({ text }),
      signal,
    });
    if (!response.body)
      throw new ApiError(response.status, "The answer had no body.");
    yield* readEvents(response.body);
  },
});
