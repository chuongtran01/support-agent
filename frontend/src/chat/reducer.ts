import type { Citation, Message, StreamEvent } from "@/api/types";

export interface ChatMessage {
  key: string;
  role: "user" | "assistant";
  text: string;
  citations: Citation[];
}

/** The answer being written right now. */
export interface LiveTurn {
  text: string;
  /** The tool running at the moment, if any. */
  tool: string | null;
}

export interface ChatError {
  message: string;
  /** The backend's code for a 409, such as `chat_full`. */
  code?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  turn: LiveTurn | null;
  error: ChatError | null;
  /** How many messages this view has sent, for the keys of the ones the backend has not numbered yet. */
  sent: number;
}

export type ChatAction =
  | { type: "reset" }
  | { type: "loaded"; messages: Message[] }
  | { type: "sent"; text: string }
  | { type: "event"; event: StreamEvent }
  /** The request was refused before any event, so the backend did not take the message. */
  | { type: "rejected"; error: ChatError }
  /** Something failed and the turn is over; nothing already shown is taken back. For a lost
   *  stream the backend finishes and saves the turn anyway. */
  | { type: "failed"; error: ChatError }
  /** The stream ended. If the turn had not finished, the answer was cut off. */
  | { type: "closed" };

export const initialChatState: ChatState = {
  messages: [],
  turn: null,
  error: null,
  sent: 0,
};

function fromBackend(message: Message): ChatMessage {
  return {
    key: `m${message.id}`,
    role: message.role,
    text: message.text,
    citations: message.citations,
  };
}

function applyEvent(state: ChatState, event: StreamEvent): ChatState {
  const turn = state.turn;
  if (!turn) return state; // an event after the turn ended
  switch (event.type) {
    case "text":
      return { ...state, turn: { ...turn, text: turn.text + event.text } };
    case "tool":
      // The backend does not keep the words written before a tool call, so neither do we:
      // the live answer then matches the chat after a reload.
      return event.phase === "started"
        ? { ...state, turn: { text: "", tool: event.name } }
        : { ...state, turn: { ...turn, tool: null } };
    case "done": {
      const answer: ChatMessage = {
        key: `m${event.messageId}`,
        role: "assistant",
        text: turn.text,
        citations: event.citations,
      };
      return {
        ...state,
        messages: turn.text.trim()
          ? [...state.messages, answer]
          : state.messages,
        turn: null,
      };
    }
    case "error":
      return { ...state, turn: null, error: { message: event.message } };
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset":
      return initialChatState;
    case "loaded":
      return {
        ...initialChatState,
        messages: action.messages.map(fromBackend),
      };
    case "sent": {
      const sent = state.sent + 1;
      const message: ChatMessage = {
        key: `local-${sent}`,
        role: "user",
        text: action.text,
        citations: [],
      };
      return {
        ...state,
        messages: [...state.messages, message],
        turn: { text: "", tool: null },
        error: null,
        sent,
      };
    }
    case "event":
      return applyEvent(state, action.event);
    case "rejected": {
      const last = state.messages.at(-1);
      const untaken = last?.role === "user" && last.key.startsWith("local-");
      return {
        ...state,
        messages: untaken ? state.messages.slice(0, -1) : state.messages,
        turn: null,
        error: action.error,
      };
    }
    case "failed":
      return { ...state, turn: null, error: action.error };
    case "closed":
      return state.turn
        ? {
            ...state,
            turn: null,
            error: {
              message:
                "The connection closed before the answer was finished. Reload the chat to see it.",
            },
          }
        : state;
  }
}
