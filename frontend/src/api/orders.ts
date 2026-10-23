import type { Http } from "./http";
import type { Order } from "./types";

export const ordersApi = ({ json }: Http) => ({
  orders: () => json<Order[]>("/orders"),
});
