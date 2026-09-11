import type { QueryResult } from "./types";

// Chunk ids the model actually cited, in first-appearance order, restricted
// to ids that exist in this message's own retrieval results — a hallucinated
// or malformed [bracket] is left as plain text rather than numbered.
export function extractCitationOrder(text: string, results?: QueryResult[]): string[] {
  if (!results || results.length === 0) return [];
  const validIds = new Set(results.map((r) => r.chunk_id));
  const order: string[] = [];
  const seen = new Set<string>();
  const regex = /\[([^[\]]+)\]/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text))) {
    const id = match[1];
    if (validIds.has(id) && !seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
  }
  return order;
}
