import type { Citation, StreamEvent } from "./types";

/** One server-sent event as it arrives: its name and its data lines joined by newlines. */
export interface SseFrame {
  event: string;
  data: string;
}

/**
 * Splits a stream of text chunks into frames. A chunk can end in the middle of a frame, so
 * the unfinished part is kept until the next `push`. Lines end in \n, \r\n or \r, a frame
 * ends at a blank line, and comment lines (starting with ":") are ignored.
 */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];

  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    // A trailing "\r" may be the first half of "\r\n", so it waits for the next chunk.
    const lines = this.buffer.split(/\r\n|\n|\r(?!$)/);
    this.buffer = lines.pop() ?? "";
    for (const line of lines) {
      const frame = this.line(line);
      if (frame) frames.push(frame);
    }
    return frames;
  }

  /** The stream ended. A frame still missing its blank line is dropped, as the spec says. */
  end(): void {
    this.buffer = "";
    this.event = "";
    this.data = [];
  }

  private line(line: string): SseFrame | null {
    if (line === "") {
      const frame = this.data.length
        ? { event: this.event || "message", data: this.data.join("\n") }
        : null;
      this.event = "";
      this.data = [];
      return frame;
    }
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.event = value;
    else if (field === "data") this.data.push(value);
    return null;
  }
}

interface DonePayload {
  message_id: number;
  citations: Citation[];
  dropped_citations: number;
}

/** A frame as a typed event, or null for a frame this client does not know. */
export function toStreamEvent(frame: SseFrame): StreamEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;
  switch (frame.event) {
    case "text": {
      const { text } = payload as { text: string };
      return { type: "text", text };
    }
    case "tool": {
      const { name, phase, status } = payload as {
        name: string;
        phase: "started" | "finished";
        status: string | null;
      };
      return { type: "tool", name, phase, status };
    }
    case "done": {
      const done = payload as DonePayload;
      return {
        type: "done",
        messageId: done.message_id,
        citations: done.citations,
        droppedCitations: done.dropped_citations,
      };
    }
    case "error": {
      const { message } = payload as { message: string };
      return { type: "error", message };
    }
    default:
      return null;
  }
}

/** Reads a response body as typed events until it ends. */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const parser = new SseParser();
  const decoder = new TextDecoder();
  const reader = body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        const event = toStreamEvent(frame);
        if (event) yield event;
      }
    }
    parser.end();
  } finally {
    reader.releaseLock();
  }
}
