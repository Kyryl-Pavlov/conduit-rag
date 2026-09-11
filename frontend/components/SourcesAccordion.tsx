"use client";

import { useState } from "react";

import type { QueryResult } from "@/lib/types";

import { ChevronDownIcon } from "./icons";
import SourceCitation from "./SourceCitation";

export default function SourcesAccordion({
  results,
  citationOrder,
}: {
  results: QueryResult[];
  citationOrder: string[];
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex w-full max-w-[80%] flex-col gap-1">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className="flex items-center gap-1 text-xs font-medium text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
      >
        <ChevronDownIcon className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`} />
        {expanded ? "Hide" : "Show"} sources ({results.length})
      </button>
      {expanded && (
        <div className="flex flex-col gap-1">
          {results.map((result) => {
            const number = citationOrder.indexOf(result.chunk_id) + 1;
            return (
              <SourceCitation
                key={result.chunk_id}
                result={result}
                citationNumber={number > 0 ? number : undefined}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
