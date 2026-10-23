import type { Page, Route } from "@playwright/test";

import type {
  Citation,
  Message,
  Order,
  PendingRefund,
  Profile,
  Session,
} from "../src/api/types";

export const API = "http://localhost:8000";

export const ALEX: Profile = {
  name: "Alex Rivera",
  role: "customer",
  membership: "basic",
};
export const PRIYA: Profile = {
  name: "Priya Shah",
  role: "customer",
  membership: "plus",
};
export const TAYLOR: Profile = {
  name: "Taylor Brooks",
  role: "approver",
  membership: "none",
};

export const SHIPPING: Citation = {
  document_id: "shipping",
  title: "Shipping",
  heading_path: "Shipping > Free shipping",
};

export const frame = (event: string, data: object) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

/** The frames of a short cited answer, as the real backend sends them. */
export const ANSWER = [
  frame("tool", { name: "search_knowledge", phase: "started", status: null }),
  frame("tool", { name: "search_knowledge", phase: "finished", status: "ok" }),
  frame("text", { text: "Free shipping starts at $75 " }),
  frame("text", { text: "[shipping]." }),
  frame("done", { message_id: 8, citations: [SHIPPING], dropped_citations: 0 }),
].join("");

export interface FakeBackend {
  /** Who each token belongs to. A login with a known email gets that person's token. */
  people?: Record<string, Profile>;
  sessions?: Session[];
  messages?: Record<string, Message[]>;
  refunds?: PendingRefund[];
  orders?: Order[];
  /** The response to a sent message. Defaults to a short cited answer. */
  send?: (route: Route, text: string) => Promise<void> | void;
  /** The response to a decision. Defaults to success. */
  decide?: (
    route: Route,
    refundId: number,
    decision: string,
  ) => Promise<void> | void;
}

export const SESSION_ID = "11111111-1111-1111-1111-111111111111";

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, content-type",
  "access-control-allow-methods": "GET, POST",
};

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({
    status,
    headers: CORS,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

/** Answers every call the app makes to the backend, in the browser, with no server behind. */
export async function mockBackend(page: Page, fake: FakeBackend = {}) {
  const people = fake.people ?? {
    "alex@example.com": ALEX,
    "approver@example.com": TAYLOR,
    "priya@example.com": PRIYA,
  };
  let refunds = [...(fake.refunds ?? [])];
  const sessions = [...(fake.sessions ?? [])];

  await page.route(`${API}/**`, async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS")
      return route.fulfill({ status: 204, headers: CORS });
    const path = new URL(request.url()).pathname;
    const token = request.headers()["authorization"]?.replace("Bearer ", "");
    const me = token
      ? Object.values(people).find((p) => `token-${p.name}` === token)
      : undefined;

    if (path === "/auth/login") {
      const { email } = request.postDataJSON();
      const profile = people[email];
      return profile
        ? json(route, 200, {
            token: `token-${profile.name}`,
            token_type: "bearer",
            customer: profile,
          })
        : json(route, 401, { detail: "Wrong email or password" });
    }
    if (!me) return json(route, 401, { detail: "Invalid token" });
    if (path === "/me") return json(route, 200, me);
    if (path === "/sessions" && request.method() === "GET")
      return json(route, 200, sessions);
    if (path === "/sessions" && request.method() === "POST") {
      const created = {
        id: SESSION_ID,
        created_at: new Date().toISOString(),
        preview: null,
      };
      sessions.unshift(created);
      return json(route, 201, created);
    }
    const chat = path.match(/^\/sessions\/([^/]+)\/messages$/);
    if (chat && request.method() === "GET") {
      const found = fake.messages?.[chat[1]];
      return found
        ? json(route, 200, found)
        : json(route, 404, { detail: "Chat not found" });
    }
    if (chat && request.method() === "POST") {
      const { text } = request.postDataJSON();
      if (fake.send) return fake.send(route, text);
      return route.fulfill({
        status: 200,
        headers: { ...CORS, "content-type": "text/event-stream" },
        body: ANSWER,
      });
    }
    if (path === "/orders") return json(route, 200, fake.orders ?? []);
    if (path === "/approvals") return json(route, 200, refunds);
    const decision = path.match(/^\/approvals\/(\d+)\/decision$/);
    if (decision) {
      const id = Number(decision[1]);
      const { decision: choice } = request.postDataJSON();
      if (fake.decide) return fake.decide(route, id, choice);
      refunds = refunds.filter((r) => r.refund_id !== id);
      return json(route, 200, {
        status: choice === "approve" ? "approved" : "rejected",
        refund_id: id,
      });
    }
    return json(route, 404, { detail: "Not found" });
  });
  return {
    json: (route: Route, status: number, body: unknown) =>
      json(route, status, body),
    CORS,
  };
}

export const TENT_REFUND: PendingRefund = {
  refund_id: 7,
  order_id: 3,
  customer_name: "Priya Shah",
  item_name: "Trail tent",
  quantity: 1,
  amount_cents: 42105,
  reason: "changed_mind",
  created_at: "2026-10-20T10:00:00Z",
  is_own: false,
};

export async function signIn(page: Page, who: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(who) }).click();
  await page.waitForURL(/\/(chat|approvals)$/); // the token is stored by the time the page moves
}

/** The alerts of the app itself, not Next.js's hidden route announcer, which is also an alert. */
export const alerts = (page: Page) =>
  page.locator('[role="alert"]:not(#__next-route-announcer__)');

/** Opens the menu on the avatar at the bottom of the sidebar and logs out (the sidebar is a drawer on a phone). */
export async function signOut(page: Page) {
  const toggle = page.getByRole("button", { name: "Toggle Sidebar" });
  if (
    (await toggle.first().isVisible()) &&
    (await page.viewportSize())!.width < 768
  )
    await toggle.first().click();
  await page.getByRole("button", { name: /Rivera|Brooks|Shah/ }).click();
  await page.getByRole("menuitem", { name: "Log out" }).click();
}

/** On a phone the sidebar is hidden until its button is pressed. On a desktop this does nothing. */
export async function openSidebar(page: Page) {
  if ((await page.viewportSize())!.width < 768)
    await page.getByRole("button", { name: "Toggle Sidebar" }).first().click();
}

const BOOTS = {
  order_item_id: 1,
  sku: "BOOT-TR",
  name: "Trail boots",
  price_cents: 12900,
  quantity: 1,
  category: "footwear",
  is_final_sale: false,
  units_refunded: 0,
};

export const DELIVERED_ORDER: Order = {
  id: 14,
  status: "delivered",
  placed_at: "2026-10-01T10:00:00Z",
  delivered_at: "2026-10-05T10:00:00Z",
  total_cents: 12900,
  items: [BOOTS],
  refunds: [],
};

export const SHIPPED_ORDER: Order = {
  ...DELIVERED_ORDER,
  id: 20,
  status: "shipped",
  delivered_at: null,
  items: [
    { ...BOOTS, order_item_id: 5, name: "Water bottle", price_cents: 1800 },
  ],
  total_cents: 1800,
};

export const REFUNDED_ORDER: Order = {
  ...DELIVERED_ORDER,
  id: 9,
  items: [
    {
      ...BOOTS,
      order_item_id: 2,
      name: "Trail socks",
      price_cents: 1400,
      quantity: 3,
      units_refunded: 1,
    },
  ],
  refunds: [
    {
      refund_id: 5,
      order_item_id: 2,
      quantity: 1,
      amount_cents: 1400,
      status: "completed",
      created_at: "2026-10-07T10:00:00Z",
    },
  ],
  total_cents: 4200,
};
