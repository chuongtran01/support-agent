import { expect, test } from "@playwright/test";

import { mockBackend, signIn, TENT_REFUND } from "./backend";

const OWN = {
  ...TENT_REFUND,
  refund_id: 8,
  customer_name: "Taylor Brooks",
  item_name: "Rain shell",
  amount_cents: 25000,
  reason: "defect",
  is_own: true,
};

test("the pending refunds show who, what, how much and why", async ({
  page,
}) => {
  await mockBackend(page, { refunds: [TENT_REFUND] });
  await signIn(page, "Taylor Brooks");
  await expect(page.getByText("Priya Shah")).toBeVisible();
  await expect(page.getByText("Trail tent × 1")).toBeVisible();
  await expect(page.getByText("$421.05")).toBeVisible();
  await expect(page.getByText("Changed mind")).toBeVisible();
});

test("the buttons can be reached on any width", async ({ page }) => {
  await mockBackend(page, { refunds: [TENT_REFUND, OWN] });
  await signIn(page, "Taylor Brooks");
  await expect(
    page.getByRole("button", { name: "Approve refund for Priya Shah" }),
  ).toBeInViewport();
  await expect(
    page.getByRole("button", { name: "Reject refund for Priya Shah" }),
  ).toBeInViewport();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("approving asks first, then removes the row", async ({ page }) => {
  await mockBackend(page, { refunds: [TENT_REFUND] });
  await signIn(page, "Taylor Brooks");
  await page
    .getByRole("button", { name: "Approve refund for Priya Shah" })
    .click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Priya Shah: Trail tent × 1, $421.05");
  await dialog.getByRole("button", { name: "Approve refund" }).click();
  await expect(
    page.getByText("Approved the $421.05 refund for Priya Shah."),
  ).toBeVisible();
  await expect(page.getByText("Trail tent × 1")).toHaveCount(0);
});

test("rejecting works the same way", async ({ page }) => {
  await mockBackend(page, { refunds: [TENT_REFUND] });
  await signIn(page, "Taylor Brooks");
  await page
    .getByRole("button", { name: "Reject refund for Priya Shah" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Reject refund" })
    .click();
  await expect(
    page.getByText("Rejected the $421.05 refund for Priya Shah."),
  ).toBeVisible();
});

test("cancelling sends nothing", async ({ page }) => {
  let decisions = 0;
  await mockBackend(page, {
    refunds: [TENT_REFUND],
    decide: (route) => (decisions++, route.abort()),
  });
  await signIn(page, "Taylor Brooks");
  await page
    .getByRole("button", { name: "Approve refund for Priya Shah" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Cancel" })
    .click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  expect(decisions).toBe(0);
  await expect(page.getByText("Priya Shah")).toBeVisible();
});

test("an approver's own refund cannot be decided", async ({ page }) => {
  await mockBackend(page, { refunds: [OWN] });
  await signIn(page, "Taylor Brooks");
  await expect(page.getByText("Rain shell × 1")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve refund for Taylor Brooks" }),
  ).toBeDisabled();
  await expect(page.getByText(/Your own refund/)).toBeVisible();
});

test("a refund someone else decided first leaves the list with a note", async ({
  page,
}) => {
  await mockBackend(page, {
    refunds: [TENT_REFUND],
    decide: (route) =>
      route.fulfill({
        status: 409,
        headers: { "access-control-allow-origin": "*" },
        contentType: "application/json",
        body: JSON.stringify({ detail: "This refund is already approved." }),
      }),
  });
  await signIn(page, "Taylor Brooks");
  await page
    .getByRole("button", { name: "Approve refund for Priya Shah" })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Approve refund" })
    .click();
  await expect(page.getByText(/This refund is already approved/)).toBeVisible();
  await expect(page.getByText("Trail tent × 1")).toHaveCount(0);
});

test("an empty list says so", async ({ page }) => {
  await mockBackend(page, { refunds: [] });
  await signIn(page, "Taylor Brooks");
  await expect(
    page.getByText("No refunds are waiting for approval."),
  ).toBeVisible();
});
