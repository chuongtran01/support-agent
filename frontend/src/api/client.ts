import { readEvents } from "./sse";
import type {
  Decision,
  DecisionResult,
  LoginResponse,
  Message,
  PendingRefund,
  Profile,
  Session,
  StreamEvent,
} from "./types";

/** A failed request. `code` is set for the 409s the backend gives a machine-readable code. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiOptions {
  baseUrl: string;
  getToken: () => string | null;
  /** Called on any 401, so the app can drop the token and go to the login page. */
  onUnauthorized: () => void;
}

/** FastAPI's `detail` is a string, `{code, message}` or, for a 422, a list of problems. */
async function toError(response: Response): Promise<ApiError> {
  let detail: unknown;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  if (typeof detail === "string") return new ApiError(response.status, detail);
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string } | undefined;
    return new ApiError(response.status, first?.msg ?? "The request was not valid.", "invalid");
  }
  if (typeof detail === "object" && detail !== null) {
    const { code, message } = detail as { code?: string; message?: string };
    return new ApiError(response.status, message ?? response.statusText, code);
  }
  return new ApiError(response.status, response.statusText || "The request failed.");
}

export function createApi({ baseUrl, getToken, onUnauthorized }: ApiOptions) {
  async function send(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body) headers.set("Content-Type", "application/json");
    const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
    if (response.ok) return response;
    // A wrong password on the login form is also a 401, but there is no token to drop.
    if (response.status === 401 && token) onUnauthorized();
    throw await toError(response);
  }

  async function json<T>(path: string, init?: RequestInit): Promise<T> {
    return (await send(path, init)).json() as Promise<T>;
  }

  const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

  return {
    login: (email: string, password: string) =>
      json<LoginResponse>("/auth/login", post({ email, password })),
    me: () => json<Profile>("/me"),
    listSessions: () => json<Session[]>("/sessions"),
    createSession: () => json<Session>("/sessions", { method: "POST" }),
    messages: (sessionId: string) => json<Message[]>(`/sessions/${sessionId}/messages`),

    /** Sends a message and yields the turn's events as they arrive. */
    async *sendMessage(
      sessionId: string,
      text: string,
      signal?: AbortSignal,
    ): AsyncGenerator<StreamEvent> {
      const response = await send(`/sessions/${sessionId}/messages`, { ...post({ text }), signal });
      if (!response.body) throw new ApiError(response.status, "The answer had no body.");
      yield* readEvents(response.body);
    },

    pendingRefunds: () => json<PendingRefund[]>("/approvals"),
    decide: (refundId: number, decision: Decision) =>
      json<DecisionResult>(`/approvals/${refundId}/decision`, post({ decision })),
  };
}

export type Api = ReturnType<typeof createApi>;
