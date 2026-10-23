/** Cents as dollars, the way the backend's amounts are shown: 42105 -> "$421.05". */
export function formatCents(cents: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}
