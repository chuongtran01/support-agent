import { describe, expect, it } from "vitest";

import type { Order } from "@/api/types";
import { GENERAL_QUESTIONS, suggestionsFor } from "./suggestions";

const item = (over = {}) => ({
  order_item_id: 1,
  sku: "BOOT",
  name: "Trail boots",
  price_cents: 12900,
  quantity: 1,
  category: "footwear",
  is_final_sale: false,
  units_refunded: 0,
  ...over,
});
const order = (id: number, over: Partial<Order> = {}): Order => ({
  id,
  status: "delivered",
  placed_at: "2026-10-01T10:00:00Z",
  delivered_at: "2026-10-05T10:00:00Z",
  total_cents: 12900,
  items: [item()],
  refunds: [],
  ...over,
});

describe("suggestionsFor", () => {
  it("is just the general questions when there are no orders", () => {
    expect(suggestionsFor([])).toEqual(GENERAL_QUESTIONS);
  });

  it("adds a return question about the newest order that can be returned", () => {
    const orders = [
      order(20, { status: "shipped", delivered_at: null }),
      order(14, { items: [item({ units_refunded: 1 })] }), // already refunded
      order(9, { items: [item({ name: "Trail socks" })] }),
      order(3),
    ];
    const asked = suggestionsFor(orders);
    expect(asked).toContain(
      "I'd like to return the Trail socks from order #9.",
    );
    expect(asked.filter((s) => s.includes("return the"))).toHaveLength(1);
  });

  it("adds a where-is-it question about an order that has not arrived", () => {
    const asked = suggestionsFor([
      order(20, { status: "processing", delivered_at: null }),
      order(14),
    ]);
    expect(asked).toContain("Where is order #20?");
    expect(asked).toContain(
      "I'd like to return the Trail boots from order #14.",
    );
  });

  it("offers no return for final sale, and no order question when everything has arrived", () => {
    const asked = suggestionsFor([
      order(12, { items: [item({ is_final_sale: true })] }),
    ]);
    expect(asked).toEqual(GENERAL_QUESTIONS);
  });

  it("never offers more than four", () => {
    const orders = [
      order(20, { status: "shipped", delivered_at: null }),
      order(1),
      order(2),
      order(3),
    ];
    expect(suggestionsFor(orders)).toHaveLength(4);
  });
});
