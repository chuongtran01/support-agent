import type { components } from "./schema";

// Request and response types come from the backend's OpenAPI spec (`make api-types`).
type Schemas = components["schemas"];

export type Profile = Schemas["Profile"];
export type LoginResponse = Schemas["LoginResponse"];
export type Session = Schemas["SessionOut"];
export type Citation = Schemas["CitationOut"];
export type Message = Schemas["MessageOut"];
export type Order = Schemas["OrderView"];
export type OrderItem = Schemas["OrderItemView"];
export type OrderRefund = Schemas["RefundView"];
export type PendingRefund = Schemas["PendingRefund"];
export type Decision = Schemas["Decision"]["decision"];
export type DecisionResult = Schemas["DecisionOut"];

// The turn stream is server-sent events, which OpenAPI does not describe, so these follow
// `sse_frame` in backend/app/api/sse.py by hand.
export type StreamEvent =
  | { type: "text"; text: string }
  | {
      type: "tool";
      name: string;
      phase: "started" | "finished";
      status: string | null;
    }
  | {
      type: "done";
      messageId: number;
      citations: Citation[];
      droppedCitations: number;
    }
  | { type: "error"; message: string };
