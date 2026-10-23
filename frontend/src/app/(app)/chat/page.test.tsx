import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { SessionsProvider } from "@/lib/sessions";
import { setToken } from "@/lib/token";
import ChatPage from "./page";

// The address is the real one in jsdom. The router writes to it, as Next.js does.
const toAddress = (url: string) => window.history.replaceState(null, "", url);
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: toAddress, push: toAddress }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const frame = (event: string, data: object) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const sse = (...frames: string[]) =>
  new Response(frames.join(""), { status: 200 });

const alex = { name: "Alex Rivera", role: "customer", membership: "basic" };
const shipping = {
  document_id: "shipping",
  title: "Shipping",
  heading_path: "Shipping > Free shipping",
};
const SESSION = "11111111-1111-1111-1111-111111111111";

interface Backend {
  sessions?: unknown[];
  orders?: unknown[];
  messages?: unknown[] | Response;
  send?: () => Response;
}

function backend({
  sessions = [],
  orders = [],
  messages = [],
  send,
}: Backend = {}) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const path = url.replace("http://localhost:8000", "");
    const method = init?.method ?? "GET";
    if (path === "/me") return json(200, alex);
    if (path === "/orders") return json(200, orders);
    if (path === "/sessions" && method === "GET") return json(200, sessions);
    if (path === "/sessions" && method === "POST") {
      return json(201, {
        id: SESSION,
        created_at: "2026-10-22T10:00:00Z",
        preview: null,
      });
    }
    if (path === `/sessions/${SESSION}/messages` && method === "GET") {
      return messages instanceof Response ? messages : json(200, messages);
    }
    if (path === `/sessions/${SESSION}/messages` && method === "POST")
      return send!();
    throw new Error(`unexpected ${method} ${path}`);
  });
}

const sent = () =>
  fetchMock.mock.calls
    .filter(
      ([url, init]) =>
        String(url).endsWith("/messages") && init?.method === "POST",
    )
    .map(([, init]) => JSON.parse(init.body as string).text);

async function open() {
  setToken("tok");
  render(
    <AuthProvider>
      <SessionsProvider>
        <ChatPage />
      </SessionsProvider>
    </AuthProvider>,
  );
  return screen.findByRole("textbox", { name: "Message" });
}

beforeEach(() => {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/chat");
});
afterEach(() => fetchMock.mockReset());

describe("ChatPage", () => {
  it("sends a message and shows the answer with its source", async () => {
    backend({
      send: () =>
        sse(
          frame("tool", {
            name: "search_knowledge",
            phase: "started",
            status: null,
          }),
          frame("tool", {
            name: "search_knowledge",
            phase: "finished",
            status: "ok",
          }),
          frame("text", { text: "Free shipping starts at $75 " }),
          frame("text", { text: "[shipping]." }),
          frame("done", {
            message_id: 8,
            citations: [shipping],
            dropped_citations: 0,
          }),
        ),
    });
    const box = await open();
    await userEvent.type(box, "What is the free shipping minimum?{Enter}");

    expect(
      await screen.findByText(/Free shipping starts at \$75/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("What is the free shipping minimum?"),
    ).toBeInTheDocument();
    expect(sent()).toEqual(["What is the free shipping minimum?"]);
    expect(
      screen.getByRole("button", { name: "Source 1: Shipping" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\[shipping\]/)).not.toBeInTheDocument();
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).toHaveValue("");
    expect(window.location.search).toBe(`?session=${SESSION}`);
  });

  it("opens the source to show its title and heading", async () => {
    backend({
      send: () =>
        sse(
          frame("text", { text: "It is $75 [shipping]." }),
          frame("done", {
            message_id: 8,
            citations: [shipping],
            dropped_citations: 0,
          }),
        ),
    });
    await userEvent.type(await open(), "Hi{Enter}");
    await userEvent.click(
      await screen.findByRole("button", { name: "Source 1: Shipping" }),
    );
    expect(
      await screen.findByText("Shipping > Free shipping"),
    ).toBeInTheDocument();
  });

  it("adds a new line on Shift+Enter instead of sending", async () => {
    backend();
    const box = await open();
    await userEvent.type(box, "one{Shift>}{Enter}{/Shift}two");
    expect(box).toHaveValue("one\ntwo");
    expect(sent()).toEqual([]);
  });

  it("does not send an empty message", async () => {
    backend();
    await open();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("gives the message back when another turn is still running", async () => {
    backend({
      send: () =>
        json(409, {
          detail: { code: "turn_in_progress", message: "Wait for the answer." },
        }),
    });
    const box = await open();
    await userEvent.type(box, "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Still answering the last message.",
    );
    expect(box).toHaveValue("Hello");
    expect(
      screen.queryByText("Hello", { selector: "div" }),
    ).not.toBeInTheDocument();
  });

  it("offers a new chat when this one is full", async () => {
    backend({
      send: () =>
        json(409, {
          detail: {
            code: "chat_full",
            message: "This chat is full. Please start a new chat.",
          },
        }),
    });
    await userEvent.type(await open(), "Hello{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This chat is full");
    await userEvent.click(
      within(alert).getByRole("button", { name: "Start a new chat" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
    );
    expect(window.location.search).toBe("");
  });

  it("shows an error from the stream and leaves the box usable", async () => {
    backend({
      send: () =>
        sse(
          frame("text", { text: "Part of an" }),
          frame("error", {
            message: "Something went wrong. Please try again.",
          }),
        ),
    });
    const box = await open();
    await userEvent.type(box, "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    await waitFor(() => expect(box).toBeEnabled());
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("says so when the server cannot be reached", async () => {
    backend({
      send: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const box = await open();
    await userEvent.type(box, "Hello{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /can't reach the server/i,
    );
    expect(box).toHaveValue("Hello");
  });

  it("opens the chat named in the address, with its sources", async () => {
    window.history.replaceState(null, "", `/chat?session=${SESSION}`);
    backend({
      sessions: [
        {
          id: SESSION,
          created_at: "2026-10-22T10:00:00Z",
          preview: "Shipping question",
        },
      ],
      messages: [
        {
          id: 1,
          role: "user",
          text: "Free shipping?",
          created_at: "2026-10-22T10:00:00Z",
          citations: [],
          dropped_citations: 0,
        },
        {
          id: 2,
          role: "assistant",
          text: "From $75 [shipping].",
          created_at: "2026-10-22T10:00:01Z",
          citations: [shipping],
          dropped_citations: 0,
        },
      ],
    });
    await open();
    expect(
      await screen.findByText("Free shipping?", { selector: "div" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Source 1: Shipping" }),
    ).toBeInTheDocument();
  });

  it("opens with a question from the orders page in the box, not sent, and out of the address", async () => {
    window.history.replaceState(
      null,
      "",
      `/chat?ask=${encodeURIComponent("Where is order #20?")}`,
    );
    backend();
    const box = await open();
    expect(box).toHaveValue("Where is order #20?");
    expect(box).toHaveFocus();
    expect(sent()).toEqual([]);
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("keeps a question from the address to the length the backend accepts", async () => {
    window.history.replaceState(null, "", `/chat?ask=${"x".repeat(3000)}`);
    backend();
    expect(((await open()) as HTMLTextAreaElement).value).toHaveLength(2000);
  });

  it("suggests questions in an empty chat, with ones about the person's own orders", async () => {
    backend({
      orders: [
        {
          id: 20,
          status: "shipped",
          placed_at: "2026-10-01T10:00:00Z",
          delivered_at: null,
          total_cents: 1800,
          refunds: [],
          items: [
            {
              order_item_id: 5,
              sku: "BTL",
              name: "Water bottle",
              price_cents: 1800,
              quantity: 1,
              category: "other",
              is_final_sale: false,
              units_refunded: 0,
            },
          ],
        },
        {
          id: 14,
          status: "delivered",
          placed_at: "2026-10-01T10:00:00Z",
          delivered_at: "2026-10-05T10:00:00Z",
          total_cents: 12900,
          refunds: [],
          items: [
            {
              order_item_id: 1,
              sku: "BOOT",
              name: "Trail boots",
              price_cents: 12900,
              quantity: 1,
              category: "footwear",
              is_final_sale: false,
              units_refunded: 0,
            },
          ],
        },
      ],
    });
    await open();
    expect(
      screen.getByRole("button", {
        name: "What's the minimum order for free shipping?",
      }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", {
        name: "I'd like to return the Trail boots from order #14.",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Where is order #20?" }),
    ).toBeInTheDocument();
  });

  it("puts a picked suggestion in the box and does not send it", async () => {
    backend();
    const box = await open();
    await userEvent.click(
      screen.getByRole("button", {
        name: "How long do I have to return something?",
      }),
    );
    expect(box).toHaveValue("How long do I have to return something?");
    expect(box).toHaveFocus();
    expect(sent()).toEqual([]);
  });

  it("shows no suggestions once the chat has messages", async () => {
    window.history.replaceState(null, "", `/chat?session=${SESSION}`);
    backend({
      messages: [
        {
          id: 1,
          role: "user",
          text: "Hi",
          created_at: "2026-10-22T10:00:00Z",
          citations: [],
          dropped_citations: 0,
        },
        {
          id: 2,
          role: "assistant",
          text: "Hello.",
          created_at: "2026-10-22T10:00:01Z",
          citations: [],
          dropped_citations: 0,
        },
      ],
    });
    await open();
    expect(await screen.findByText("Hello.")).toBeInTheDocument();
    expect(screen.queryByText("How can I help?")).not.toBeInTheDocument();
  });

  it("starts a new chat when the saved one is gone", async () => {
    window.history.replaceState(null, "", `/chat?session=${SESSION}`);
    backend({ messages: json(404, { detail: "Chat not found" }) });
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That chat was not found",
    );
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});
