import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

const API = process.env.DEMO_API_URL ?? "http://localhost:8001";
const OUT = "e2e-real/results";
const results: Record<string, unknown> = {};

mkdirSync(OUT, { recursive: true });

/** What the backend says about a person's orders, straight from the API (not the screen). */
async function ordersOf(page: Page, email: string) {
  const login = await page.request.post(`${API}/auth/login`, {
    data: { email, password: "demo" },
  });
  const { token } = await login.json();
  const response = await page.request.get(`${API}/orders`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return (await response.json()) as Array<{
    id: number;
    items: Array<{ name: string }>;
    refunds: Array<{
      status: string;
      amount_cents: number;
      order_item_id: number;
    }>;
  }>;
}

async function signIn(page: Page, name: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(name) }).click();
  await page.waitForURL(/\/(chat|approvals)$/);
}

/** Press Enter on what is in the box and wait for the whole answer. Returns the chat's text. */
async function sendAndWait(page: Page) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.press("Enter");
  await page.waitForTimeout(700); // the box is disabled while the agent answers
  await expect(box).toBeEnabled({ timeout: 150_000 });
  return (await page.getByRole("log").innerText()).trim();
}

async function ask(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: "Message" });
  await box.fill(question);
  return sendAndWait(page);
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test.describe.configure({ mode: "serial" });

test.afterAll(() => {
  writeFileSync(`${OUT}/results.json`, JSON.stringify(results, null, 2));
});

test("1. Alex asks about free shipping and gets $75 with a source", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  const log = await ask(page, "What's the minimum order for free shipping?");
  results["1"] = {
    log,
    chips: await page.getByRole("button", { name: /^Source \d/ }).count(),
  };
  await shot(page, "1-free-shipping");
  expect(log).toMatch(/\$75/);
  expect(
    await page.getByRole("button", { name: /^Source \d/ }).count(),
  ).toBeGreaterThan(0);
  expect.soft(log, "must not give the stale $50").not.toMatch(/\$50\b/);
});

test("2. Alex returns the boots from the Orders page: refunded at once, minus the label fee", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  await page.getByRole("link", { name: "Orders" }).click();
  await page
    .getByRole("link", { name: /Ask about a return for Trail boots/ })
    .click();
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveValue(/return the Trail boots from order #\d+/);
  const question = await box.inputValue();
  const log = await sendAndWait(page);
  results["2"] = { question, log };
  await shot(page, "2-boots-refund");
  expect(log).toMatch(/121\.05/);
  expect(log).not.toMatch(/approval|pending/i);
  const boots = (await ordersOf(page, "alex@example.com")).find((o) =>
    o.items.some((i) => i.name === "Trail boots"),
  )!;
  results["2-api"] = boots.refunds;
  expect(boots.refunds.map((r) => [r.status, r.amount_cents])).toEqual([
    ["completed", 12105],
  ]);
});

test("3. Priya returns the tent: the refund waits for approval", async ({
  page,
}) => {
  await signIn(page, "Priya Shah");
  await page.getByRole("link", { name: "Orders" }).click();
  await page
    .getByRole("link", { name: /Ask about a return for Four-season tent/ })
    .click();
  const box = page.getByRole("textbox", { name: "Message" });
  await expect(box).toHaveValue(/return the Four-season tent from order #\d+/);
  const question = await box.inputValue();
  const log = await sendAndWait(page);
  results["3"] = { question, log };
  await shot(page, "3-tent-pending");
  expect(log).toMatch(/approv|review/i);
  const tent = (await ordersOf(page, "priya@example.com")).find((o) =>
    o.items.some((i) => i.name === "Four-season tent"),
  )!;
  results["3-api"] = tent.refunds;
  expect(tent.refunds.map((r) => [r.status, r.amount_cents])).toEqual([
    ["pending", 42105],
  ]);
});

test("4. The approver sees the tent refund and approves it", async ({
  page,
}) => {
  await signIn(page, "Taylor Brooks");
  await expect(page).toHaveURL(/\/approvals$/);
  const row = page.getByRole("row", { name: /Priya Shah.*Four-season tent/ });
  await expect(row).toContainText("$421.05");
  await shot(page, "4-approvals");
  await row
    .getByRole("button", { name: /Approve refund for Priya Shah/ })
    .click();
  await page
    .getByRole("alertdialog")
    .getByRole("button", { name: "Approve refund" })
    .click();
  await expect(
    page.getByText("Approved the $421.05 refund for Priya Shah."),
  ).toBeVisible();
  await shot(page, "4-approved");
  const tent = (await ordersOf(page, "priya@example.com")).find((o) =>
    o.items.some((i) => i.name === "Four-season tent"),
  )!;
  results["4-api"] = tent.refunds;
  expect(tent.refunds.map((r) => r.status)).toEqual(["approved"]);
  results["4-rows-before"] = "Priya Shah / Four-season tent / $421.05";
});

test("5. Priya asks about the tent order and the agent reports it approved", async ({
  page,
}) => {
  await signIn(page, "Priya Shah");
  await page.getByRole("link", { name: "Orders" }).click();
  await expect(page.getByText("Approved $421.05")).toBeVisible();
  await page
    .getByRole("link", { name: /Ask about this order #\d+/ })
    .first()
    .waitFor();
  const tent = (await ordersOf(page, "priya@example.com")).find((o) =>
    o.items.some((i) => i.name === "Four-season tent"),
  )!;
  await page.goto("/chat");
  const log = await ask(
    page,
    `What is the status of the refund on order #${tent.id}?`,
  );
  results["5"] = { log };
  await shot(page, "5-priya-status");
  expect(log).toMatch(/approved/i);
});

test("6. Alex asks about Priya's order and is told it cannot be found", async ({
  page,
}) => {
  const priyaOrder = (await ordersOf(page, "priya@example.com")).find((o) =>
    o.items.some((i) => i.name === "Four-season tent"),
  )!;
  await signIn(page, "Alex Rivera");
  const log = await ask(page, `Where is order #${priyaOrder.id}?`);
  results["6"] = { orderId: priyaOrder.id, log };
  await shot(page, "6-other-customer");
  expect(log).toMatch(
    /couldn.t find|could not find|can.t find|cannot find|not find|no matching|unable to find/i,
  );
  expect(log).not.toMatch(/Four-season|tent|421/i);
});

// The other demo accounts, to check what the login page says about each of them.

async function orderNamed(page: Page, email: string, item: string) {
  return (await ordersOf(page, email)).find((o) =>
    o.items.some((i) => i.name === item),
  )!;
}

test("7. Jordan has three refunds in 90 days: the next return goes to a person", async ({
  page,
}) => {
  const order = await orderNamed(page, "jordan@example.com", "Camp lantern");
  await signIn(page, "Jordan Lee");
  const log = await ask(
    page,
    `I'd like to return the Camp lantern from order #${order.id}.`,
  );
  results["7"] = { log };
  await shot(page, "7-jordan");
  expect(log).toMatch(/person|team|review|specialist|follow up/i);
  // no text check that "a refund was made" is absent: "No refund has been made yet" is a correct answer.
  // The backend state below is the assertion.
  const after = await orderNamed(page, "jordan@example.com", "Camp lantern");
  results["7-api"] = after.refunds;
  expect(after.refunds.map((r) => r.status)).not.toContain("completed");
});

test("8. Morgan has three refunds but only two in 90 days: the return is allowed", async ({
  page,
}) => {
  const order = await orderNamed(page, "morgan@example.com", "Camp mug set");
  await signIn(page, "Morgan Chen");
  const log = await ask(
    page,
    `I'd like to return the Camp mug set from order #${order.id}.`,
  );
  results["8"] = { log };
  await shot(page, "8-morgan");
  const after = await orderNamed(page, "morgan@example.com", "Camp mug set");
  results["8-api"] = after.refunds;
  expect(after.refunds.map((r) => r.status)).toEqual(["completed"]);
});

test("9. Sam's order is still processing: it cannot be returned yet", async ({
  page,
}) => {
  const order = await orderNamed(page, "sam@example.com", "Two-person tent");
  await signIn(page, "Sam Okafor");
  const log = await ask(
    page,
    `I'd like to return the Two-person tent from order #${order.id}.`,
  );
  results["9"] = { log };
  await shot(page, "9-sam");
  const after = await orderNamed(page, "sam@example.com", "Two-person tent");
  results["9-api"] = after.refunds;
  expect(after.refunds).toEqual([]);
});

test("10. Alex returns the jacket without giving an order number: the agent finds the order", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  const log = await ask(
    page,
    "I want to return my down jacket. I changed my mind.",
  );
  results["10"] = { log };
  await shot(page, "10-no-order-number");
  expect(log).toMatch(/151\.05/);
  const jacket = await orderNamed(page, "alex@example.com", "Down jacket");
  results["10-api"] = jacket.refunds;
  expect(jacket.refunds.map((r) => [r.status, r.amount_cents])).toEqual([
    ["completed", 15105],
  ]);
});
