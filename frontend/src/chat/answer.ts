import type { Citation } from "@/api/types";

export type AnswerPart =
  | { kind: "text"; text: string }
  | { kind: "source"; number: number; citation: Citation };

// The same shape of marker the backend looks for (backend/app/agent/citations.py).
const MARKER = /\[([a-z][a-z0-9]*(?:-[a-z0-9]+)*)\]/g;

/**
 * Splits a finished answer into text and numbered sources. A `[document_id]` marker for a
 * document in `citations` becomes a source, numbered in order of first use; a marker for
 * anything else was dropped by the backend, so it is removed here too.
 */
export function answerParts(text: string, citations: Citation[]): AnswerPart[] {
  const known = new Map(citations.map((c) => [c.document_id, c]));
  const numbers = new Map<string, number>();
  const parts: AnswerPart[] = [];
  let last = 0;

  const addText = (piece: string) => {
    if (!piece) return;
    const before = parts.at(-1);
    if (before?.kind === "text") before.text += piece;
    else parts.push({ kind: "text", text: piece });
  };

  for (const match of text.matchAll(MARKER)) {
    const citation = known.get(match[1]);
    addText(text.slice(last, match.index).replace(/ $/, "")); // no gap before the marker
    last = match.index + match[0].length;
    if (!citation) continue;
    if (!numbers.has(citation.document_id))
      numbers.set(citation.document_id, numbers.size + 1);
    parts.push({
      kind: "source",
      number: numbers.get(citation.document_id)!,
      citation,
    });
  }
  addText(text.slice(last));
  return parts;
}
