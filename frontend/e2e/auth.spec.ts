import { expect, test } from "@playwright/test";

import { alerts, mockBackend, openSidebar, signIn, signOut } from "./backend";

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

test("a customer signs in with one click and lands in the chat", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  await expect(page).toHaveURL(/\/chat$/);
  await openSidebar(page);
  await expect(page.getByText("Alex Rivera")).toBeVisible();
  await expect(page.getByText("Basic member")).toBeVisible();
  await expect(page.getByRole("link", { name: "Approvals" })).toHaveCount(0);
});

test("an approver lands on the approvals page", async ({ page }) => {
  await signIn(page, "Taylor Brooks");
  await expect(page).toHaveURL(/\/approvals$/);
  await openSidebar(page);
  await expect(page.getByRole("link", { name: "Approvals" })).toBeVisible();
});

test("the email form says so for a wrong password", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(alerts(page)).toHaveText("Wrong email or password.");
  await expect(page).toHaveURL(/\/login$/);
});

test("someone who is not signed in is sent to the login page", async ({
  page,
}) => {
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});

test("a token the backend refuses sends the person to the login page", async ({
  page,
}) => {
  await page.addInitScript(() =>
    sessionStorage.setItem("support-agent.token", "stale"),
  );
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
  expect(
    await page.evaluate(() => sessionStorage.getItem("support-agent.token")),
  ).toBeNull();
});

test("a customer who opens the approvals page is told they are not allowed", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  await page.goto("/approvals");
  await expect(page.getByText("Not allowed")).toBeVisible();
});

test("logging out returns to the login page and forgets the token", async ({
  page,
}) => {
  await signIn(page, "Alex Rivera");
  await signOut(page);
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});

test("the page title says where you are", async ({ page }) => {
  await signIn(page, "Alex Rivera");
  await expect(page).toHaveTitle("Chat · Northwind support");
});

test("an unknown address shows a not-found page", async ({ page }) => {
  await page.goto("/nowhere");
  await expect(
    page.getByRole("heading", { name: "Page not found" }),
  ).toBeVisible();
});

test("each demo account says what it is set up to show", async ({ page }) => {
  await page.goto("/login");
  await expect(
    page.getByText("Return the trail boots (5 days old)"),
  ).toBeVisible();
  await expect(page.getByText(/Return the tent: over \$200/)).toBeVisible();
  await expect(page.getByText(/Three refunds already/)).toBeVisible();
  await expect(
    page.getByText(/Decide Priya's pending parka refund/),
  ).toBeVisible();
});
