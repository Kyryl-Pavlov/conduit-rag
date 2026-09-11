import type { QueryResult } from "@/lib/types";

export default function SourceCitation({
  result,
  citationNumber,
}: {
  result: QueryResult;
  citationNumber?: number;
}) {
  const page = typeof result.metadata?.page === "number" ? `, p. ${result.metadata.page}` : "";

  return (
    <div
      id={`source-${result.chunk_id}`}
      className="rounded border border-zinc-200 bg-zinc-50 p-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
    >
      <span className="font-medium">
        {citationNumber != null && (
          <span className="mr-1 rounded bg-blue-100 px-1 py-0.5 text-[10px] font-semibold text-blue-700 dark:bg-blue-900/60 dark:text-blue-300">
            {citationNumber}
          </span>
        )}
        {result.file_id}
        {page}
      </span>{" "}
      — similarity {result.similarity.toFixed(3)}
      <p className="mt-1 text-zinc-500">{result.text}</p>
    </div>
  );
}
