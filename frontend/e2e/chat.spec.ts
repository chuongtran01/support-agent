import { expect, test } from "@playwright/test";

import {
  alerts,
  DELIVERED_ORDER,
  SHIPPED_ORDER,
  frame,
  mockBackend,
  openSidebar,
  SESSION_ID,
  SHIPPING,
  signIn,
} from "./backend";

const saved = {
  sessions: [
    {
      id: SESSION_ID,
      created_at: "2026-10-22T10:00:00Z",
      preview: "What is the minimum order for free shipping?",
    },
  ],
  messages: {
    [SESSION_ID]: [
      {
        id: 1,
        role: "user" as const,
        text: "What is the minimum order for free shipping?",
        created_at: "2026-10-22T10:00:00Z",
        citations: [],
        dropped_citations: 0,
      },
      {
        id: 2,
        role: "assistant" as const,
        text: "Free shipping starts at $75 [shipping].",
        created_at: "2026-10-22T10:00:01Z",
        citations: [SHIPPING],
        dropped_citations: 0,
      },
    ],
  },
};

test("a question streams in with its source", async ({ page }) => {
  await mockBackend(page);
  await signIn(page, "Alex Rivera");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("What is the minimum order for free shipping?");
  await box.press("Enter");

  await expect(
    page.getByText("What is the minimum order for free shipping?"),
  ).toBeVisible();
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  await expect(box).toBeEnabled();
  await expect(box).toBeFocused();
  await expect(page).toHaveURL(/\?session=/);

  await page.getByRole("button", { name: "Source 1: Shipping" }).click();
  await expect(page.getByText("Shipping > Free shipping")).toBeVisible();
});

test("Shift+Enter adds a line and Enter sends", async ({ page }) => {
  await mockBackend(page);
  await signIn(page, "Alex Rivera");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("one");
  await box.press("Shift+Enter");
  await box.pressSequentially("two");
  await expect(box).toHaveValue("one\ntwo");
});

test("a message sent while another turn runs is given back", async ({
  page,
}) => {
  await mockBackend(page, {
    send: (route) =>
      route.fulfill({
        status: 409,
        headers: { "access-control-allow-origin": "*" },
        contentType: "application/json",
        body: JSON.stringify({
          detail: { code: "turn_in_progress", message: "Wait for the answer." },
        }),
      }),
  });
  await signIn(page, "Alex Rivera");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("Hello");
  await box.press("Enter");
  await expect(alerts(page)).toHaveText("Still answering the last message.");
  await expect(box).toHaveValue("Hello");
});

test("a full chat offers a new one", async ({ page }) => {
  await mockBackend(page, {
    ...saved,
    send: (route) =>
      route.fulfill({
        status: 409,
        headers: { "access-control-allow-origin": "*" },
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "chat_full",
            message: "This chat is full. Please start a new chat.",
          },
        }),
      }),
  });
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("One more");
  await box.press("Enter");
  await page.getByRole("button", { name: "Start a new chat" }).click();
  await expect(alerts(page)).toHaveCount(0);
  await expect(
    page.getByRole("log").getByText("What is the minimum order"),
  ).toHaveCount(0);
});

test("an error in the stream is shown and the box stays usable", async ({
  page,
}) => {
  await mockBackend(page, {
    send: (route) =>
      route.fulfill({
        status: 200,
        headers: {
          "access-control-allow-origin": "*",
          "content-type": "text/event-stream",
        },
        body:
          frame("text", { text: "Part of an" }) +
          frame("error", {
            message: "Something went wrong. Please try again.",
          }),
      }),
  });
  await signIn(page, "Alex Rivera");
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill("Hello");
  await box.press("Enter");
  await expect(alerts(page)).toHaveText(
    "Something went wrong. Please try again.",
  );
  await expect(box).toBeEnabled();
});

test("a saved chat comes back after a reload, with its source", async ({
  page,
}) => {
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Source 1: Shipping" }),
  ).toBeVisible();
});

test("a chat that no longer exists is replaced by a new one", async ({
  page,
}) => {
  await mockBackend(page);
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(alerts(page)).toContainText("That chat was not found");
  await expect(page).not.toHaveURL(/session=/);
});

test("the sidebar lists the chats, and a click opens one", async ({ page }) => {
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  await openSidebar(page);
  await page.getByRole("link", { name: /What is the minimum order/ }).click();
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`session=${SESSION_ID}`));
  await openSidebar(page);
  await expect(
    page.getByRole("link", { name: /What is the minimum order/ }),
  ).toHaveAttribute("data-active", "");
});

test("New chat in the sidebar leaves the open chat for an empty one", async ({
  page,
}) => {
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  await openSidebar(page);
  await page.getByRole("link", { name: "New chat" }).click();
  await expect(page.getByText("Ask about an order")).toBeVisible();
  await expect(page).not.toHaveURL(/session=/);
});

test("the chats are grouped by date", async ({ page }) => {
  await mockBackend(page, {
    sessions: [
      {
        id: "today-1",
        created_at: new Date().toISOString(),
        preview: "Where is my order?",
      },
      {
        id: "old-1",
        created_at: "2020-01-01T10:00:00Z",
        preview: "Old question",
      },
    ],
  });
  await signIn(page, "Alex Rivera");
  await openSidebar(page);
  await expect(page.getByText("Today")).toBeVisible();
  await expect(page.getByText("Older")).toBeVisible();
});

test("the sidebar collapses to icons on a desktop", async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, "a phone shows the sidebar as a drawer");
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  await expect(page.getByText("Northwind Outfitters").first()).toBeVisible();
  await page.getByRole("button", { name: "Toggle Sidebar" }).first().click();
  await expect(page.getByText("Northwind Outfitters").first()).toBeHidden();
  await expect(page.getByRole("link", { name: "New chat" })).toBeVisible(); // still there, as an icon
  await expect(
    page.getByRole("link", { name: /What is the minimum order/ }),
  ).toBeHidden();
});

test("an approver reaches Approvals from the sidebar, and the chats stay listed", async ({
  page,
}) => {
  await mockBackend(page, saved);
  await signIn(page, "Taylor Brooks");
  await openSidebar(page);
  await expect(
    page.getByRole("link", { name: /What is the minimum order/ }),
  ).toBeVisible();
  await page.getByRole("link", { name: "New chat" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  await openSidebar(page);
  await page.getByRole("link", { name: "Approvals" }).click();
  await expect(page).toHaveURL(/\/approvals$/);
});

test("the page does not scroll sideways", async ({ page }) => {
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("an empty chat suggests questions, including ones about the person's orders", async ({
  page,
}) => {
  await mockBackend(page, { orders: [SHIPPED_ORDER, DELIVERED_ORDER] });
  await signIn(page, "Alex Rivera");
  await expect(
    page.getByRole("heading", { name: "How can I help?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "What's the minimum order for free shipping?",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "I'd like to return the Trail boots from order #14.",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Where is order #20?" }),
  ).toBeVisible();
});

test("picking a suggestion fills the box, ready to send", async ({ page }) => {
  await mockBackend(page, { orders: [DELIVERED_ORDER] });
  await signIn(page, "Alex Rivera");
  await page
    .getByRole("button", { name: "How long do I have to return something?" })
    .click();
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveValue("How long do I have to return something?");
  await expect(box).toBeFocused();
  await box.press("Enter");
  await expect(
    page.getByRole("log").getByText("How long do I have to return something?"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "How can I help?" }),
  ).toHaveCount(0); // gone once the chat starts
});

test("opening the chat asks the backend for each thing once", async ({
  page,
}) => {
  // Run on a production build, so React's development double-run of effects does not count.
  await mockBackend(page, { ...saved, orders: [DELIVERED_ORDER] });
  await signIn(page, "Alex Rivera");
  // let the page we signed in to finish its own requests, so they are not counted
  await expect(
    page.getByRole("button", { name: /return the Trail boots/ }),
  ).toBeVisible();
  const calls: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === "http://localhost:8000")
      calls.push(`${request.method()} ${url.pathname}`);
  });
  await page.reload(); // a fresh load of /chat with the token already stored
  await expect(
    page.getByRole("button", {
      name: "How long do I have to return something?",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /return the Trail boots/ }),
  ).toBeVisible();
  expect(calls.sort()).toEqual(["GET /me", "GET /orders", "GET /sessions"]);
});
