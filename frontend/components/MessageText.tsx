import type { ReactNode } from "react";

// Minimal renderer for the answer format the system prompt asks the model
// for: headers, paragraphs, bullet/numbered lists, **bold**, and
// [chunk_id] citations. Not a general markdown parser — deliberately
// narrow to that shape.

function renderInline(text: string, citationOrder: string[]): ReactNode[] {
  const tokenRegex = /(\*\*[^*]+\*\*|\[[^[\]]+\])/g;
  return text
    .split(tokenRegex)
    .filter((part) => part !== "")
    .map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={i}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("[") && part.endsWith("]")) {
        const chunkId = part.slice(1, -1);
        const number = citationOrder.indexOf(chunkId) + 1;
        if (number > 0) {
          return (
            <sup key={i}>
              <a
                href={`#source-${chunkId}`}
                className="ml-0.5 rounded bg-blue-100 px-1 py-0.5 text-[10px] font-semibold text-blue-700 no-underline hover:bg-blue-200 dark:bg-blue-900/60 dark:text-blue-300 dark:hover:bg-blue-900"
              >
                {number}
              </a>
            </sup>
          );
        }
      }
      return part;
    });
}

const HEADER_CLASSES: Record<number, string> = {
  1: "text-base font-semibold",
  2: "text-base font-semibold",
  3: "text-sm font-semibold",
  4: "text-sm font-semibold",
};

export default function MessageText({
  text,
  citationOrder,
}: {
  text: string;
  citationOrder: string[];
}) {
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let listType: "ul" | "ol" | null = null;

  function flushList() {
    if (listItems.length === 0 || listType === null) return;
    const Tag = listType;
    blocks.push(
      <Tag
        key={`list-${blocks.length}`}
        className={`ml-4 space-y-1 ${listType === "ul" ? "list-disc" : "list-decimal"}`}
      >
        {listItems.map((item, i) => (
          <li key={i}>{renderInline(item, citationOrder)}</li>
        ))}
      </Tag>,
    );
    listItems = [];
    listType = null;
  }

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (line === "") {
      flushList();
      continue;
    }

    const headerMatch = line.match(/^(#{1,4})\s+(.*)$/);
    if (headerMatch) {
      flushList();
      const level = headerMatch[1].length;
      const Tag = `h${Math.min(level, 4)}` as "h1" | "h2" | "h3" | "h4";
      blocks.push(
        <Tag key={blocks.length} className={HEADER_CLASSES[level]}>
          {renderInline(headerMatch[2], citationOrder)}
        </Tag>,
      );
      continue;
    }

    const bulletMatch = line.match(/^[-*]\s+(.*)$/);
    if (bulletMatch) {
      if (listType !== "ul") flushList();
      listType = "ul";
      listItems.push(bulletMatch[1]);
      continue;
    }

    const orderedMatch = line.match(/^\d+[.)]\s+(.*)$/);
    if (orderedMatch) {
      if (listType !== "ol") flushList();
      listType = "ol";
      listItems.push(orderedMatch[1]);
      continue;
    }

    flushList();
    blocks.push(<p key={blocks.length}>{renderInline(line, citationOrder)}</p>);
  }
  flushList();

  return <div className="flex flex-col gap-2">{blocks}</div>;
}
