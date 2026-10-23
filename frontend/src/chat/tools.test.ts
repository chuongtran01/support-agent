import { describe, expect, it } from "vitest";

import { toolStatus } from "./tools";

describe("toolStatus", () => {
  it("says what each tool is doing in plain words", () => {
    expect(toolStatus("list_orders")).toBe("Looking through your orders…");
    expect(toolStatus("lookup_order")).toBe("Looking up your order…");
    expect(toolStatus("search_knowledge")).toBe(
      "Looking through our help articles…",
    );
  });

  it("has something to say for a tool it does not know", () => {
    expect(toolStatus("brand_new_tool")).toBe("Working on it…");
  });
});
