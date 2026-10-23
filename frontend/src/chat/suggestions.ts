import type { Order } from "@/api/types";
import {
  askAboutOrder,
  askAboutReturn,
  canAskAboutReturn,
} from "@/orders/labels";

/** Questions anyone can ask. They work from the help articles, so they need no order. */
export const GENERAL_QUESTIONS = [
  "What's the minimum order for free shipping?",
  "How long do I have to return something?",
];

/**
 * What to offer in an empty chat: the general questions, then up to two about the person's
 * own orders, with the real order numbers in them (the agent needs one to look an order up).
 * `orders` are newest first, as the API sends them.
 */
export function suggestionsFor(orders: Order[]): string[] {
  const suggestions = [...GENERAL_QUESTIONS];
  for (const order of orders) {
    const item = order.items.find((i) => canAskAboutReturn(order, i));
    if (item) {
      suggestions.push(askAboutReturn(order, item));
      break;
    }
  }
  const unarrived = orders.find(
    (o) => o.status === "processing" || o.status === "shipped",
  );
  if (unarrived) suggestions.push(askAboutOrder(unarrived));
  return suggestions;
}
