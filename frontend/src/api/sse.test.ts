import { describe, expect, it } from "vitest";
import { readEvents, SseParser, toStreamEvent } from "./sse";

const frame = (event: string, data: object) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

function body(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>) {
  const events = [];
  for await (const event of readEvents(stream)) events.push(event);
  return events;
}

describe("SseParser", () => {
  it("reads several frames from one chunk", () => {
    const frames = new SseParser().push(frame("text", { text: "a" }) + frame("text", { text: "b" }));
    expect(frames.map((f) => f.data)).toEqual(['{"text":"a"}', '{"text":"b"}']);
    expect(frames.every((f) => f.event === "text")).toBe(true);
  });

  it("keeps a frame split across chunks until it is complete", () => {
    const parser = new SseParser();
    const whole = frame("text", { text: "hello" });
    expect(parser.push(whole.slice(0, 12))).toEqual([]);
    expect(parser.push(whole.slice(12, -1))).toEqual([]);
    expect(parser.push(whole.slice(-1))).toEqual([{ event: "text", data: '{"text":"hello"}' }]);
  });

  it("handles \\r\\n line ends, even when split between chunks", () => {
    const parser = new SseParser();
    expect(parser.push("event: text\r\ndata: x\r")).toEqual([]);
    expect(parser.push("\n\r\n")).toEqual([{ event: "text", data: "x" }]);
  });

  it("joins several data lines with a newline", () => {
    expect(new SseParser().push("data: one\ndata: two\n\n")).toEqual([
      { event: "message", data: "one\ntwo" },
    ]);
  });

  it("allows no space after the colon, and ignores comments and unknown fields", () => {
    expect(new SseParser().push(": keep-alive\nid: 7\nevent:text\ndata:x\n\n")).toEqual([
      { event: "text", data: "x" },
    ]);
  });

  it("does not make a frame from a blank line with no data", () => {
    expect(new SseParser().push("\n\nevent: text\n\n")).toEqual([]);
  });

  it("drops a frame cut short by the end of the stream", () => {
    const parser = new SseParser();
    expect(parser.push("event: text\ndata: x\n")).toEqual([]);
    parser.end();
    expect(parser.push("\n")).toEqual([]);
  });
});

describe("toStreamEvent", () => {
  it("reads each event the backend sends", () => {
    expect(toStreamEvent({ event: "text", data: '{"text":"Hi"}' })).toEqual({ type: "text", text: "Hi" });
    expect(
      toStreamEvent({ event: "tool", data: '{"name":"lookup_order","phase":"started","status":null}' }),
    ).toEqual({ type: "tool", name: "lookup_order", phase: "started", status: null });
    expect(toStreamEvent({ event: "error", data: '{"message":"Sorry"}' })).toEqual({
      type: "error",
      message: "Sorry",
    });
  });

  it("renames the done fields", () => {
    const citation = { document_id: "returns-policy", title: "Returns", heading_path: "Returns > Window" };
    expect(
      toStreamEvent({
        event: "done",
        data: JSON.stringify({ message_id: 9, citations: [citation], dropped_citations: 1 }),
      }),
    ).toEqual({ type: "done", messageId: 9, citations: [citation], droppedCitations: 1 });
  });

  it("ignores an unknown event and data that is not a JSON object", () => {
    expect(toStreamEvent({ event: "ping", data: "{}" })).toBeNull();
    expect(toStreamEvent({ event: "text", data: "not json" })).toBeNull();
    expect(toStreamEvent({ event: "text", data: "3" })).toBeNull();
  });
});

describe("readEvents", () => {
  it("yields a whole turn in order, however the bytes are cut", () => {
    const text = [
      frame("tool", { name: "search_knowledge", phase: "started", status: null }),
      frame("text", { text: "Free shipping starts at $75." }),
      frame("done", { message_id: 4, citations: [], dropped_citations: 0 }),
    ].join("");
    const cut = [text.slice(0, 7), text.slice(7, 90), text.slice(90)];
    return collect(body(...cut)).then((events) => {
      expect(events.map((e) => e.type)).toEqual(["tool", "text", "done"]);
    });
  });

  it("keeps a multi-byte character that is split across reads", async () => {
    const bytes = new TextEncoder().encode(frame("text", { text: "café ☕" }));
    const at = bytes.indexOf(0xc3) + 1; // between the two bytes of "é"
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, at));
        controller.enqueue(bytes.slice(at));
        controller.close();
      },
    });
    expect(await collect(stream)).toEqual([{ type: "text", text: "café ☕" }]);
  });

  it("yields nothing for a stream that ends mid-frame", async () => {
    expect(await collect(body('event: text\ndata: {"te'))).toEqual([]);
  });
});
