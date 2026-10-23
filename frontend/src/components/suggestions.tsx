"use client";

import { suggestionsFor, GENERAL_QUESTIONS } from "@/chat/suggestions";
import { Button } from "@/components/ui/button";
import { useLoad } from "@/lib/use-load";

/** Questions to start with, for an empty chat. Picking one is up to the page. */
export function Suggestions({ onPick }: { onPick: (text: string) => void }) {
  // The general questions show at once; the ones about the person's orders join them when the
  // orders arrive. If they never do, the general ones are enough.
  const { data: orders } = useLoad((api) => api.orders());
  const questions = orders ? suggestionsFor(orders) : GENERAL_QUESTIONS;
  return (
    <section
      aria-labelledby="try-asking"
      className="flex flex-col items-center gap-6 py-12"
    >
      <div className="text-center">
        <h2 className="text-xl font-semibold">How can I help?</h2>
        <p id="try-asking" className="text-sm text-muted-foreground">
          Ask about an order, a return, shipping or anything else. Or try one of
          these:
        </p>
      </div>
      <ul className="flex w-full flex-col gap-2">
        {questions.map((question) => (
          <li key={question}>
            <Button
              variant="outline"
              className="h-auto w-full justify-start py-2.5 text-left font-normal whitespace-normal"
              onClick={() => onPick(question)}
            >
              {question}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
