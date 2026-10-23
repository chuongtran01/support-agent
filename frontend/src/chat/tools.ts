// What to say while a tool runs. The API sends the tool's name and nothing else.
const STATUS: Record<string, string> = {
  search_knowledge: "Looking through our help articles…",
  lookup_order: "Looking up your order…",
  issue_refund: "Processing your refund…",
  create_ticket: "Creating a support ticket…",
  escalate: "Passing this to a person…",
};

export function toolStatus(name: string): string {
  return STATUS[name] ?? "Working on it…";
}
