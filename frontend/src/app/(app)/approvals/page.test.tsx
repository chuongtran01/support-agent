import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PendingRefund } from "@/api/types";
import { AuthProvider } from "@/lib/auth";
import { setToken } from "@/lib/token";
import ApprovalsPage from "./page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const taylor = { name: "Taylor Brooks", role: "approver", membership: "none" };
const priya: PendingRefund = {
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
const mine: PendingRefund = {
  ...priya,
  refund_id: 8,
  customer_name: "Taylor Brooks",
  item_name: "Rain shell",
  amount_cents: 25000,
  reason: "defect",
  is_own: true,
};

/** Serves the approver, the pending list, and the given answer to a decision. */
function backend(
  list: PendingRefund[] | Response,
  decision?: (id: string, body: unknown) => Response,
) {
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const path = url.replace("http://localhost:8000", "");
    if (path === "/me") return json(200, taylor);
    if (path === "/approvals")
      return list instanceof Response ? list.clone() : json(200, list);
    const match = path.match(/^\/approvals\/(\d+)\/decision$/);
    if (match) return decision!(match[1], JSON.parse(init!.body as string));
    throw new Error(`unexpected ${path}`);
  });
}

const decisions = () =>
  fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/decision"))
    .length;

function open() {
  setToken("tok");
  render(
    <AuthProvider>
      <ApprovalsPage />
    </AuthProvider>,
  );
}

beforeEach(() => sessionStorage.clear());
afterEach(() => fetchMock.mockReset());

describe("ApprovalsPage", () => {
  it("lists the pending refunds with who, what, how much and why", async () => {
    backend([priya]);
    open();
    const row = (await screen.findByText("Priya Shah")).closest("tr")!;
    expect(within(row).getByText("Trail tent × 1")).toBeInTheDocument();
    expect(within(row).getByText("$421.05")).toBeInTheDocument();
    expect(within(row).getByText("Changed mind")).toBeInTheDocument();
  });

  it("says so when nothing is waiting", async () => {
    backend([]);
    open();
    expect(
      await screen.findByText("No refunds are waiting for approval."),
    ).toBeInTheDocument();
  });

  it("asks for confirmation, then approves and removes the row", async () => {
    backend([priya], () => json(200, { status: "approved", refund_id: 7 }));
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Approve refund for Priya Shah",
      }),
    );

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Approve this refund?");
    expect(dialog).toHaveTextContent("Priya Shah: Trail tent × 1, $421.05");
    expect(decisions()).toBe(0); // nothing is sent until it is confirmed

    await userEvent.click(
      within(dialog).getByRole("button", { name: "Approve refund" }),
    );
    expect(
      await screen.findByText("Approved the $421.05 refund for Priya Shah."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByText("Trail tent × 1")).not.toBeInTheDocument();
    const [, init] = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith("/7/decision"),
    )!;
    expect(JSON.parse(init.body)).toEqual({ decision: "approve" });
  });

  it("rejects after confirmation", async () => {
    backend([priya], () => json(200, { status: "rejected", refund_id: 7 }));
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Reject refund for Priya Shah",
      }),
    );
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Reject this refund?");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Reject refund" }),
    );
    expect(
      await screen.findByText("Rejected the $421.05 refund for Priya Shah."),
    ).toBeInTheDocument();
  });

  it("does nothing when the dialog is cancelled", async () => {
    backend([priya]);
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Approve refund for Priya Shah",
      }),
    );
    await userEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Cancel",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    expect(decisions()).toBe(0);
    expect(screen.getByText("Priya Shah")).toBeInTheDocument();
  });

  it("shows an approver's own refund but does not let them decide it", async () => {
    backend([mine]);
    open();
    expect(await screen.findByText("Rain shell × 1")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Approve refund for Taylor Brooks" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reject refund for Taylor Brooks" }),
    ).toBeDisabled();
    expect(screen.getByText(/Your own refund/)).toBeInTheDocument();
  });

  it("removes a refund someone else already decided, and says so", async () => {
    backend([priya], () =>
      json(409, { detail: "This refund is already approved." }),
    );
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Approve refund for Priya Shah",
      }),
    );
    await userEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Approve refund",
      }),
    );
    expect(
      await screen.findByText(
        /was not decided\. This refund is already approved\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Trail tent × 1")).not.toBeInTheDocument();
  });

  it("removes a refund that no longer exists", async () => {
    backend([priya], () => json(404, { detail: "Refund not found." }));
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Reject refund for Priya Shah",
      }),
    );
    await userEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Reject refund",
      }),
    );
    expect(await screen.findByText(/Refund not found\./)).toBeInTheDocument();
    expect(screen.queryByText("Trail tent × 1")).not.toBeInTheDocument();
  });

  it("keeps the row when the backend refuses with 403", async () => {
    backend([priya], () =>
      json(403, { detail: "An approver cannot decide their own refund." }),
    );
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Approve refund for Priya Shah",
      }),
    );
    await userEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Approve refund",
      }),
    );
    expect(
      await screen.findByText(/An approver cannot decide their own refund\./),
    ).toBeInTheDocument();
    expect(screen.getByText("Trail tent × 1")).toBeInTheDocument();
  });

  it("stays in the dialog on a failure and lets the approver try again", async () => {
    let calls = 0;
    backend([priya], () =>
      ++calls === 1
        ? json(500, { detail: "Something went wrong." })
        : json(200, { status: "approved", refund_id: 7 }),
    );
    open();
    await userEvent.click(
      await screen.findByRole("button", {
        name: "Approve refund for Priya Shah",
      }),
    );
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Approve refund" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Something went wrong.",
    );
    expect(screen.getByText("Priya Shah")).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Approve refund" }),
    );
    expect(
      await screen.findByText("Approved the $421.05 refund for Priya Shah."),
    ).toBeInTheDocument();
  });

  it("shows a load failure with a way to try again", async () => {
    backend(json(500, { detail: "Something went wrong." }));
    open();
    expect(
      await screen.findByText("Something went wrong."),
    ).toBeInTheDocument();
    backend([priya]);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Priya Shah")).toBeInTheDocument();
    expect(screen.queryByText("Something went wrong.")).not.toBeInTheDocument();
  });
});
