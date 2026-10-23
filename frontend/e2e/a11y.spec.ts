import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  mockBackend,
  SESSION_ID,
  SHIPPING,
  signIn,
  TENT_REFUND,
} from "./backend";

// WCAG 2.x A and AA rules, in a real browser (so colour contrast is checked too).
async function violations(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  return result.violations.map(
    (v) =>
      `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`,
  );
}

test("the login page has no accessibility violations", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/login");
  await expect(page.getByRole("button", { name: /Alex Rivera/ })).toBeVisible();
  expect(await violations(page)).toEqual([]);
});

test("the chat has no accessibility violations", async ({ page }) => {
  await mockBackend(page, {
    sessions: [
      {
        id: SESSION_ID,
        created_at: "2026-10-22T10:00:00Z",
        preview: "Shipping",
      },
    ],
    messages: {
      [SESSION_ID]: [
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
          citations: [SHIPPING],
          dropped_citations: 0,
        },
      ],
    },
  });
  await signIn(page, "Alex Rivera");
  await page.goto(`/chat?session=${SESSION_ID}`);
  await expect(
    page.getByRole("button", { name: "Source 1: Shipping" }),
  ).toBeVisible();
  expect(await violations(page)).toEqual([]);
});

test("the approvals page and its dialog have no accessibility violations", async ({
  page,
}) => {
  await mockBackend(page, {
    refunds: [
      TENT_REFUND,
      {
        ...TENT_REFUND,
        refund_id: 8,
        customer_name: "Taylor Brooks",
        is_own: true,
      },
    ],
  });
  await signIn(page, "Taylor Brooks");
  await expect(page.getByText("Trail tent × 1").first()).toBeVisible();
  expect(await violations(page)).toEqual([]);
  await page
    .getByRole("button", { name: "Approve refund for Priya Shah" })
    .click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.waitForTimeout(400); // let the dialog finish fading in before contrast is measured
  expect(await violations(page)).toEqual([]);
});
