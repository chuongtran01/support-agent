"use client";

import { ArrowUp } from "lucide-react";
import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export const MAX_CHARACTERS = 2000; // the backend refuses a longer message
const COUNT_FROM = 1800;

export function Composer({
  value,
  onChange,
  onSend,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
  disabled: boolean;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  const wasDisabled = useRef(disabled);

  // After an answer, the cursor goes back to the box.
  useEffect(() => {
    if (wasDisabled.current && !disabled) field.current?.focus();
    wasDisabled.current = disabled;
  }, [disabled]);

  const text = value.trim();
  const canSend = !disabled && text.length > 0;

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (canSend) onSend(text);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter adds a line. Enter while composing (IME) picks a character.
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form onSubmit={submit} className="px-3 pt-2 pb-4">
      {/* One rounded box with the field and the send button inside it, floating above the thread. */}
      <div
        data-disabled={disabled || undefined}
        className="mx-auto flex max-w-2xl items-end gap-2 rounded-3xl border bg-background py-2 pr-2 pl-4 shadow-sm transition-shadow focus-within:ring-3 focus-within:ring-ring/30 data-disabled:opacity-60"
      >
        <Textarea
          ref={field}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          maxLength={MAX_CHARACTERS}
          rows={1}
          placeholder="Write a message…"
          aria-label="Message"
          disabled={disabled}
          className="max-h-40 min-h-9 resize-none border-0 bg-transparent px-0 py-1.5 shadow-none focus-visible:border-0 focus-visible:ring-0 disabled:bg-transparent disabled:opacity-100 dark:bg-transparent"
        />
        <Button
          type="submit"
          size="icon"
          aria-label="Send"
          disabled={!canSend}
          className="size-9 shrink-0 rounded-full"
        >
          <ArrowUp />
        </Button>
      </div>
      {value.length >= COUNT_FROM && (
        <p className="mx-auto mt-1 max-w-2xl text-right text-xs text-muted-foreground">
          {value.length} / {MAX_CHARACTERS}
        </p>
      )}
    </form>
  );
}
