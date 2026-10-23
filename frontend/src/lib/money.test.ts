import { expect, it } from "vitest";

import { formatCents } from "./money";

it("shows cents as dollars", () => {
  expect(formatCents(42105)).toBe("$421.05");
  expect(formatCents(795)).toBe("$7.95");
  expect(formatCents(0)).toBe("$0.00");
  expect(formatCents(123456)).toBe("$1,234.56");
});
