import { describe, expect, it } from "vitest";

import type { Citation } from "@/api/types";
import { answerParts } from "./answer";

const returns: Citation = {
  document_id: "returns-policy",
  title: "Returns",
  heading_path: "Returns > Window",
};
const shipping: Citation = {
  document_id: "shipping",
  title: "Shipping",
  heading_path: "Shipping > Free",
};

describe("answerParts", () => {
  it("is one text part when there are no markers", () => {
    expect(answerParts("Free shipping starts at $75.", [])).toEqual([
      { kind: "text", text: "Free shipping starts at $75." },
    ]);
  });

  it("turns a marker for a cited document into a source", () => {
    expect(
      answerParts("Within 30 days [returns-policy] of delivery.", [returns]),
    ).toEqual([
      { kind: "text", text: "Within 30 days" },
      { kind: "source", number: 1, citation: returns },
      { kind: "text", text: " of delivery." },
    ]);
  });

  it("numbers sources by first use and reuses a number for a repeat", () => {
    const parts = answerParts("A [shipping] B [returns-policy] C [shipping]", [
      returns,
      shipping,
    ]);
    expect(
      parts
        .filter((p) => p.kind === "source")
        .map((p) => p.kind === "source" && p.number),
    ).toEqual([1, 2, 1]);
  });

  it("removes a marker the backend did not accept", () => {
    expect(answerParts("It is $75 [made-up-doc].", [])).toEqual([
      { kind: "text", text: "It is $75." },
    ]);
  });

  it("leaves brackets that are not markers alone", () => {
    expect(answerParts("See [1] and [Note] and [a b].", [])).toEqual([
      { kind: "text", text: "See [1] and [Note] and [a b]." },
    ]);
  });

  it("handles a marker at the very start or end", () => {
    expect(answerParts("[shipping]", [shipping])).toEqual([
      { kind: "source", number: 1, citation: shipping },
    ]);
  });
});
