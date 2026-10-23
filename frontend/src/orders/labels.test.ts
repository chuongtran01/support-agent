import { describe, expect, it } from "vitest";

import type { Order, OrderItem } from "@/api/types";
import {
  askAboutOrder,
  askAboutReturn,
  canAskAboutReturn,
  chatWith,
  orderStatusLabel,
  refundStatus,
} from "./labels";

const item = (over: Partial<OrderItem> = {}): OrderItem => ({
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
const order = (over: Partial<Order> = {}): Order => ({
  id: 14,
  status: "delivered",
  placed_at: "2026-10-01T10:00:00Z",
  delivered_at: "2026-10-05T10:00:00Z",
  total_cents: 12900,
  items: [item()],
  refunds: [],
  ...over,
});

describe("canAskAboutReturn", () => {
  it("is offered for a delivered item with units left", () => {
    expect(canAskAboutReturn(order(), item())).toBe(true);
    expect(
      canAskAboutReturn(order(), item({ quantity: 3, units_refunded: 2 })),
    ).toBe(true);
  });

  it("is not offered once every unit has a refund, or for final sale", () => {
    expect(canAskAboutReturn(order(), item({ units_refunded: 1 }))).toBe(false);
    expect(canAskAboutReturn(order(), item({ is_final_sale: true }))).toBe(
      false,
    );
  });

  it("is not offered before delivery, or for a cancelled order", () => {
    for (const status of ["processing", "shipped", "cancelled"]) {
      expect(canAskAboutReturn(order({ status }), item())).toBe(false);
    }
  });
});

describe("the questions", () => {
  it("name the order and the item", () => {
    expect(askAboutOrder(order())).toBe("Where is order #14?");
    expect(askAboutReturn(order(), item())).toBe(
      "I'd like to return the Trail boots from order #14.",
    );
  });

  it("go to the chat in the address, encoded", () => {
    const text = askAboutReturn(order(), item({ name: "Tent & pegs" }));
    const url = chatWith(text);
    expect(url.startsWith("/chat?ask=")).toBe(true);
    expect(new URLSearchParams(url.split("?")[1]).get("ask")).toBe(text);
  });
});

describe("labels", () => {
  it("say what a status means, and pass an unknown one through", () => {
    expect(orderStatusLabel("shipped")).toBe("Shipped");
    expect(orderStatusLabel("lost")).toBe("lost");
    expect(refundStatus("pending").label).toBe("Waiting for approval");
    expect(refundStatus("rejected").tone).toBe("destructive");
    expect(refundStatus("weird")).toEqual({ label: "weird", tone: "outline" });
  });
});
