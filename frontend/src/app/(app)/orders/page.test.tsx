import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Order } from "@/api/types";
import { AuthProvider } from "@/lib/auth";
import { setToken } from "@/lib/token";
import OrdersPage from "./page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const alex = { name: "Alex Rivera", role: "customer", membership: "basic" };

const boots: Order = {
  id: 14,
  status: "delivered",
  placed_at: "2026-10-01T10:00:00Z",
  delivered_at: "2026-10-05T10:00:00Z",
  total_cents: 12900,
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
  refunds: [],
};
const socks: Order = {
  id: 9,
  status: "delivered",
  placed_at: "2026-09-20T10:00:00Z",
  delivered_at: "2026-09-24T10:00:00Z",
  total_cents: 4200,
  items: [
    {
      order_item_id: 2,
      sku: "SOCK",
      name: "Trail socks",
      price_cents: 1400,
      quantity: 3,
      category: "apparel",
      is_final_sale: false,
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
      created_at: "2026-09-26T10:00:00Z",
    },
  ],
};
const shipped: Order = {
  ...boots,
  id: 20,
  status: "shipped",
  delivered_at: null,
};
const parka: Order = {
  ...boots,
  id: 11,
  items: [
    {
      ...boots.items[0],
      order_item_id: 3,
      name: "Insulated parka",
      price_cents: 24000,
      units_refunded: 1,
    },
  ],
  refunds: [
    {
      refund_id: 6,
      order_item_id: 3,
      quantity: 1,
      amount_cents: 24000,
      status: "pending",
      created_at: "2026-10-20T10:00:00Z",
    },
  ],
};
const finalSale: Order = {
  ...boots,
  id: 12,
  items: [
    {
      ...boots.items[0],
      order_item_id: 4,
      name: "Rain shell (last season)",
      is_final_sale: true,
    },
  ],
};

function serve(orders: Order[] | Response) {
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith("/me")) return json(200, alex);
    if (url.endsWith("/orders"))
      return orders instanceof Response ? orders.clone() : json(200, orders);
    throw new Error(`unexpected ${url}`);
  });
}

function open(orders: Order[] | Response) {
  setToken("tok");
  serve(orders);
  render(
    <AuthProvider>
      <OrdersPage />
    </AuthProvider>,
  );
}

const card = (name: RegExp) =>
  screen.getByText(name).closest("[data-slot=card]") as HTMLElement;

beforeEach(() => sessionStorage.clear());
afterEach(() => fetchMock.mockReset());

describe("OrdersPage", () => {
  it("shows each order with its status, date, total and items", async () => {
    open([boots, socks]);
    const order = card(
      await screen.findByText("Order #14").then(() => /Order #14/),
    );
    expect(
      within(order).getByText(/Delivered Oct 5, 2026/),
    ).toBeInTheDocument();
    expect(
      within(order).getByText(/\$129\.00/, { selector: "p" }),
    ).toBeInTheDocument();
    expect(within(order).getByText("Trail boots")).toBeInTheDocument();
    const second = card(/Order #9/);
    expect(within(second).getByText(/× 3/)).toBeInTheDocument();
    expect(within(second).getByText(/\$14\.00 each/)).toBeInTheDocument();
  });

  it("shows a refund's status and amount on its item", async () => {
    open([socks, parka]);
    await screen.findByText("Order #9");
    expect(
      within(card(/Order #9/)).getByText("Refunded $14.00"),
    ).toBeInTheDocument();
    expect(
      within(card(/Order #11/)).getByText("Waiting for approval $240.00"),
    ).toBeInTheDocument();
  });

  it("offers a return question that opens the chat with it in the box", async () => {
    open([boots]);
    const link = await screen.findByRole("link", {
      // jsdom drops the space before the hidden part of the name; a browser keeps it
      name: /^Ask about a return\s?for Trail boots from order 14$/,
    });
    const href = link.getAttribute("href")!;
    expect(href.startsWith("/chat?ask=")).toBe(true);
    expect(new URLSearchParams(href.split("?")[1]).get("ask")).toBe(
      "I'd like to return the Trail boots from order #14.",
    );
  });

  it("offers a question about the order itself", async () => {
    open([shipped]);
    const link = await screen.findByRole("link", {
      name: /^Ask about this order\s?#20$/,
    });
    expect(
      new URLSearchParams(link.getAttribute("href")!.split("?")[1]).get("ask"),
    ).toBe("Where is order #20?");
  });

  it("does not offer a return for an order that has not arrived, a final sale or a fully refunded item", async () => {
    open([shipped, finalSale, parka]);
    await screen.findByText("Order #20");
    expect(
      screen.queryByRole("link", { name: /Ask about a return/ }),
    ).not.toBeInTheDocument();
    expect(
      within(card(/Order #12/)).getByText("Final sale"),
    ).toBeInTheDocument();
  });

  it("offers a return for a part-refunded item that still has units", async () => {
    open([socks]);
    expect(
      await screen.findByRole("link", {
        name: /Ask about a return\s?for Trail socks/,
      }),
    ).toBeInTheDocument();
  });

  it("says so when there are no orders", async () => {
    open([]);
    expect(
      await screen.findByText("You have no orders yet."),
    ).toBeInTheDocument();
  });

  it("shows a load failure with a way to try again", async () => {
    open(json(500, { detail: "Something went wrong." }));
    expect(
      await screen.findByText("Something went wrong."),
    ).toBeInTheDocument();
    serve([boots]);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Order #14")).toBeInTheDocument();
  });
});
