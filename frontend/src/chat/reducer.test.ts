import { describe, expect, it } from "vitest";

import type { Message, StreamEvent } from "@/api/types";
import {
  chatReducer,
  initialChatState,
  type ChatAction,
  type ChatState,
} from "./reducer";

const run = (actions: ChatAction[], from: ChatState = initialChatState) =>
  actions.reduce(chatReducer, from);
const event = (e: StreamEvent): ChatAction => ({ type: "event", event: e });
const text = (t: string) => event({ type: "text", text: t });
const done = (messageId: number) =>
  event({ type: "done", messageId, citations: [], droppedCitations: 0 });
const tool = (name: string, phase: "started" | "finished") =>
  event({
    type: "tool",
    name,
    phase,
    status: phase === "finished" ? "ok" : null,
  });

const stored: Message[] = [
  {
    id: 1,
    role: "user",
    text: "Hi",
    created_at: "",
    citations: [],
    dropped_citations: 0,
  },
  {
    id: 2,
    role: "assistant",
    text: "Hello [faq]",
    created_at: "",
    citations: [{ document_id: "faq", title: "FAQ", heading_path: "FAQ" }],
    dropped_citations: 0,
  },
];

describe("chatReducer", () => {
  it("shows the customer's message at once and starts a turn", () => {
    const state = run([{ type: "sent", text: "Hello" }]);
    expect(state.messages).toMatchObject([{ role: "user", text: "Hello" }]);
    expect(state.turn).toEqual({ text: "", tool: null });
  });

  it("builds a plain answer from text events and saves it on done", () => {
    const state = run([
      { type: "sent", text: "Hello" },
      text("Hi "),
      text("there"),
      done(9),
    ]);
    expect(state.turn).toBeNull();
    expect(state.messages.map((m) => [m.role, m.text, m.key])).toEqual([
      ["user", "Hello", "local-1"],
      ["assistant", "Hi there", "m9"],
    ]);
  });

  it("shows the tool that is running and clears it when it finishes", () => {
    let state = run([
      { type: "sent", text: "Where is my order?" },
      tool("lookup_order", "started"),
    ]);
    expect(state.turn?.tool).toBe("lookup_order");
    state = run([tool("lookup_order", "finished")], state);
    expect(state.turn?.tool).toBeNull();
  });

  it("drops the words written before a tool call, as a reload would", () => {
    const state = run([
      { type: "sent", text: "Q" },
      text("Let me check. "),
      tool("search_knowledge", "started"),
      tool("search_knowledge", "finished"),
      text("It is $75."),
      done(5),
    ]);
    expect(state.messages.at(-1)?.text).toBe("It is $75.");
  });

  it("keeps the citations from done on the answer", () => {
    const citation = { document_id: "faq", title: "FAQ", heading_path: "FAQ" };
    const state = run([
      { type: "sent", text: "Q" },
      text("A [faq]"),
      event({
        type: "done",
        messageId: 3,
        citations: [citation],
        droppedCitations: 0,
      }),
    ]);
    expect(state.messages.at(-1)?.citations).toEqual([citation]);
  });

  it("does not add an empty answer", () => {
    const state = run([
      { type: "sent", text: "Q" },
      tool("escalate", "started"),
      tool("escalate", "finished"),
      done(4),
    ]);
    expect(state.messages).toHaveLength(1);
    expect(state.turn).toBeNull();
  });

  it("ends the turn with an error and keeps the customer's message", () => {
    const state = run([
      { type: "sent", text: "Q" },
      text("Part of an ans"),
      event({ type: "error", message: "Something went wrong." }),
    ]);
    expect(state.turn).toBeNull();
    expect(state.error).toEqual({ message: "Something went wrong." });
    expect(state.messages).toHaveLength(1);
  });

  it("takes the message back when the request was refused", () => {
    const state = run([
      { type: "sent", text: "Q" },
      {
        type: "rejected",
        error: { message: "Wait.", code: "turn_in_progress" },
      },
    ]);
    expect(state.messages).toEqual([]);
    expect(state.turn).toBeNull();
    expect(state.error?.code).toBe("turn_in_progress");
  });

  it("does not take back a message from the backend when a refusal arrives", () => {
    const state = run([
      { type: "loaded", messages: stored },
      { type: "rejected", error: { message: "Full.", code: "chat_full" } },
    ]);
    expect(state.messages).toHaveLength(2);
  });

  it("keeps everything but the live answer when the stream is lost", () => {
    const state = run([
      { type: "sent", text: "Q" },
      text("Par"),
      { type: "failed", error: { message: "Connection lost." } },
    ]);
    expect(state.turn).toBeNull();
    expect(state.messages).toHaveLength(1);
    expect(state.error?.message).toBe("Connection lost.");
  });

  it("says so when the stream closes before done, and not when it closed after", () => {
    const cut = run([
      { type: "sent", text: "Q" },
      text("Par"),
      { type: "closed" },
    ]);
    expect(cut.turn).toBeNull();
    expect(cut.error?.message).toMatch(/closed before/);
    const finished = run([{ type: "sent", text: "Q" }, text("A"), done(2)]);
    expect(run([{ type: "closed" }], finished)).toBe(finished);
  });

  it("loads a saved chat, with its citations, and clears the old one", () => {
    const state = run(
      [{ type: "loaded", messages: stored }],
      run([
        { type: "sent", text: "old" },
        { type: "rejected", error: { message: "x" } },
      ]),
    );
    expect(state.error).toBeNull();
    expect(state.messages.map((m) => m.key)).toEqual(["m1", "m2"]);
    expect(state.messages[1].citations).toHaveLength(1);
  });

  it("ignores an event that arrives after the turn ended", () => {
    const state = run([{ type: "sent", text: "Q" }, text("A"), done(2)]);
    expect(run([text("late")], state)).toBe(state);
  });

  it("gives each message sent from a view its own key", () => {
    const state = run([
      { type: "sent", text: "1" },
      text("a"),
      done(1),
      { type: "sent", text: "2" },
    ]);
    expect(
      state.messages.filter((m) => m.role === "user").map((m) => m.key),
    ).toEqual(["local-1", "local-2"]);
  });

  it("resets to an empty chat", () => {
    expect(
      run([{ type: "loaded", messages: stored }, { type: "reset" }]),
    ).toEqual(initialChatState);
  });
});
