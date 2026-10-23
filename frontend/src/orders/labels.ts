import type { Order, OrderItem, OrderRefund } from "@/api/types";

const ORDER_STATUS: Record<string, string> = {
  processing: "Processing",
  shipped: "Shipped",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

export function orderStatusLabel(status: string): string {
  return ORDER_STATUS[status] ?? status;
}

type Tone = "secondary" | "outline" | "destructive";

const REFUND_STATUS: Record<string, { label: string; tone: Tone }> = {
  completed: { label: "Refunded", tone: "secondary" },
  pending: { label: "Waiting for approval", tone: "outline" },
  approved: { label: "Approved", tone: "secondary" },
  rejected: { label: "Rejected", tone: "destructive" },
};

export function refundStatus(status: OrderRefund["status"]): {
  label: string;
  tone: Tone;
} {
  return REFUND_STATUS[status] ?? { label: status, tone: "outline" };
}

/** Whether to offer a return question for an item: it has arrived, has units left that no
 *  refund has used, and is not final sale. Policy still decides; this only hides the pointless. */
export function canAskAboutReturn(order: Order, item: OrderItem): boolean {
  return (
    order.status === "delivered" &&
    item.units_refunded < item.quantity &&
    !item.is_final_sale
  );
}

export function askAboutOrder(order: Order): string {
  return `Where is order #${order.id}?`;
}

export function askAboutReturn(order: Order, item: OrderItem): string {
  return `I'd like to return the ${item.name} from order #${order.id}.`;
}

/** The chat page opens with this text in the box, to read and send or change. */
export function chatWith(text: string): string {
  return `/chat?ask=${encodeURIComponent(text)}`;
}
