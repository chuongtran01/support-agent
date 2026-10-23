import { expect, test } from "@playwright/test";

import {
  alerts,
  frame,
  mockBackend,
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
  await expect(page.getByRole("log").getByText("What is the minimum order")).toHaveCount(0);
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

test("the list of chats opens another chat (the drawer on a phone)", async ({
  page,
  isMobile,
}) => {
  await mockBackend(page, saved);
  await signIn(page, "Alex Rivera");
  if (isMobile) await page.getByRole("button", { name: "Chats" }).click();
  await page.getByRole("button", { name: /What is the minimum order/ }).click();
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`session=${SESSION_ID}`));
  if (isMobile) await expect(page.getByRole("dialog")).toHaveCount(0); // the drawer closed
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
