import type { Session } from "@/api/types";

export interface SessionGroup {
  label: string;
  sessions: Session[];
}

const DAY = 24 * 60 * 60 * 1000;

/** Whole days from `then` to `now` by the calendar, in local time (so 11pm to 1am is one day). */
function daysAgo(then: Date, now: Date): number {
  const start = (d: Date) =>
    Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((start(now) - start(then)) / DAY);
}

/** Chats under Today, Yesterday, Previous 7 days and Older. Each keeps the order it came in. */
export function groupSessions(
  sessions: Session[],
  now: Date = new Date(),
): SessionGroup[] {
  const groups: SessionGroup[] = [
    { label: "Today", sessions: [] },
    { label: "Yesterday", sessions: [] },
    { label: "Previous 7 days", sessions: [] },
    { label: "Older", sessions: [] },
  ];
  for (const session of sessions) {
    const days = daysAgo(new Date(session.created_at), now);
    const group = days <= 0 ? 0 : days === 1 ? 1 : days <= 7 ? 2 : 3;
    groups[group].sessions.push(session);
  }
  return groups.filter((g) => g.sessions.length > 0);
}
