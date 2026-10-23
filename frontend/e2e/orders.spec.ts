import { expect, test } from "@playwright/test";

import {
  DELIVERED_ORDER,
  mockBackend,
  openSidebar,
  REFUNDED_ORDER,
  SHIPPED_ORDER,
  signIn,
} from "./backend";

const orders = [SHIPPED_ORDER, DELIVERED_ORDER, REFUNDED_ORDER];

test("the orders page lists the orders with their items and refunds", async ({
  page,
}) => {
  await mockBackend(page, { orders });
  await signIn(page, "Alex Rivera");
  await openSidebar(page);
  await page.getByRole("link", { name: "Orders" }).click();
  await expect(page).toHaveURL(/\/orders$/);
  await expect(page.getByText("Order #14", { exact: true })).toBeVisible();
  await expect(page.getByText("Trail boots", { exact: true })).toBeVisible();
  await expect(page.getByText("Delivered Oct 5, 2026").first()).toBeVisible();
  await expect(page.getByText("Refunded $14.00")).toBeVisible();
  await expect(page.getByText("Shipped", { exact: true })).toBeVisible();
});

test("asking about a return opens the chat with the question in the box", async ({
  page,
}) => {
  await mockBackend(page, { orders });
  await signIn(page, "Alex Rivera");
  await page.goto("/orders");
  await page
    .getByRole("link", {
      name: "Ask about a return for Trail boots from order 14",
    })
    .click();
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveValue(
    "I'd like to return the Trail boots from order #14.",
  );
  await expect(box).toBeFocused();
  await expect(page).toHaveURL(/\/chat$/); // the question is not left in the address
  // not sent: the chat is still empty, so its suggestions are still showing
  await expect(
    page.getByRole("heading", { name: "How can I help?" }),
  ).toBeVisible();
});

test("the question can be sent as it is", async ({ page }) => {
  await mockBackend(page, { orders });
  await signIn(page, "Alex Rivera");
  await page.goto("/orders");
  await page.getByRole("link", { name: "Ask about this order #20" }).click();
  await page.getByRole("textbox", { name: "Message" }).press("Enter");
  await expect(
    page.getByRole("log").getByText("Where is order #20?"),
  ).toBeVisible();
  await expect(page.getByText("Free shipping starts at $75")).toBeVisible();
});

test("an order that has not arrived has no return button", async ({ page }) => {
  await mockBackend(page, { orders: [SHIPPED_ORDER] });
  await signIn(page, "Alex Rivera");
  await page.goto("/orders");
  await expect(page.getByText("Order #20", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Ask about a return/ }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Ask about this order #20" }),
  ).toBeVisible();
});

test("a customer with no orders is told so", async ({ page }) => {
  await mockBackend(page, { orders: [] });
  await signIn(page, "Alex Rivera");
  await page.goto("/orders");
  await expect(page.getByText("You have no orders yet.")).toBeVisible();
});

test("the page does not scroll sideways", async ({ page }) => {
  await mockBackend(page, { orders });
  await signIn(page, "Alex Rivera");
  await page.goto("/orders");
  await expect(page.getByText("Order #14", { exact: true })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
