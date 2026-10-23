import { Plus } from "lucide-react";

import type { Session } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const when = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export function SessionList({
  sessions,
  activeId,
  onOpen,
  onNew,
}: {
  sessions: Session[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <nav aria-label="Chats" className="flex h-full flex-col gap-2 p-3">
      <Button variant="outline" onClick={onNew}>
        <Plus /> New chat
      </Button>
      <ul className="flex flex-col gap-1 overflow-y-auto">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              type="button"
              onClick={() => onOpen(session.id)}
              aria-current={session.id === activeId ? "page" : undefined}
              className={cn(
                "w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                session.id === activeId && "bg-muted",
              )}
            >
              <span className="block truncate">
                {session.preview ?? "New chat"}
              </span>
              <span className="block text-xs text-muted-foreground">
                {when.format(new Date(session.created_at))}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
