import type { Citation } from "@/api/types";
import {
  Popover,
  PopoverContent,
  PopoverDescription,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";

/** A numbered marker after a fact. It opens the title and heading of the source. */
export function SourceChip({
  number,
  citation,
}: {
  number: number;
  citation: Citation;
}) {
  return (
    <Popover>
      <PopoverTrigger
        aria-label={`Source ${number}: ${citation.title}`}
        className="mx-0.5 inline-flex size-6 -translate-y-0.5 items-center justify-center rounded-full border bg-background text-xs font-medium text-foreground align-middle hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        {number}
      </PopoverTrigger>
      <PopoverContent className="w-64">
        <PopoverTitle>{citation.title}</PopoverTitle>
        <PopoverDescription>{citation.heading_path}</PopoverDescription>
      </PopoverContent>
    </Popover>
  );
}
