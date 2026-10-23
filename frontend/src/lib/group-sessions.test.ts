import { expect, it } from "vitest";

import type { Session } from "@/api/types";
import { groupSessions } from "./group-sessions";

const NOW = new Date(2026, 9, 22, 15, 0); // Oct 22, 3pm local
const at = (
  id: string,
  ...date: [number, number, number, number?]
): Session => ({
  id,
  created_at: new Date(
    2026,
    date[0],
    date[1],
    date[2],
    date[3] ?? 0,
  ).toISOString(),
  preview: id,
});

it("groups chats by how long ago they were made", () => {
  const groups = groupSessions(
    [
      at("a", 9, 22, 14),
      at("b", 9, 22, 1),
      at("c", 9, 21, 23),
      at("d", 9, 20, 9),
      at("e", 9, 15, 9),
      at("f", 9, 14, 9),
      at("g", 8, 1, 9),
    ],
    NOW,
  );
  expect(groups.map((g) => [g.label, g.sessions.map((s) => s.id)])).toEqual([
    ["Today", ["a", "b"]],
    ["Yesterday", ["c"]],
    ["Previous 7 days", ["d", "e"]],
    ["Older", ["f", "g"]],
  ]);
});

it("counts calendar days, not 24-hour spans", () => {
  // 11pm last night is 16 hours ago but yesterday
  expect(groupSessions([at("late", 9, 21, 23)], NOW)[0].label).toBe(
    "Yesterday",
  );
});

it("leaves out groups with no chats and handles none at all", () => {
  expect(groupSessions([at("a", 9, 22, 9)], NOW).map((g) => g.label)).toEqual([
    "Today",
  ]);
  expect(groupSessions([], NOW)).toEqual([]);
});
