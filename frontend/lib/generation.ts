import Anthropic from "@anthropic-ai/sdk";
import type { MessageParam, Tool, ToolResultBlockParam } from "@anthropic-ai/sdk/resources/messages";

import { embedText } from "./bedrock";
import type { QueryResult } from "./types";
import { similaritySearch } from "./vectordb";

const MODEL_ID = "claude-opus-5";
const MAX_TOOL_ROUNDS = 3;

const SYSTEM_PROMPT = `You answer questions using context chunks from the user's uploaded documents.
If the provided context doesn't contain enough information to answer, call search_documents with a
refined or more specific query to retrieve additional chunks -- do not guess or say you lack information
without first trying at least one search. Reference chunk_id in brackets when citing, e.g. [sample.txt_w0_c0].`;

const SEARCH_TOOL: Tool = {
  name: "search_documents",
  description:
    "Search the user's uploaded documents for chunks relevant to a query. Use this when the " +
    "context provided so far doesn't answer the question, or when you need a different angle " +
    "(a synonym, a narrower term, a related concept) than the original question used.",
  input_schema: {
    type: "object",
    properties: {
      query: { type: "string", description: "Search query -- can differ from the user's original question." },
      top_k: { type: "integer", description: "Number of chunks to retrieve. Defaults to 5." },
    },
    required: ["query"],
  },
};

// Lazy: constructed on first use, not at module load, so routes that never
// reach generation (e.g. zero retrieval results) don't require
// ANTHROPIC_API_KEY to be set at all.
let client: Anthropic | undefined;
function getClient(): Anthropic {
  if (!client) client = new Anthropic();
  return client;
}

function formatChunks(chunks: QueryResult[]): string {
  return chunks.map((chunk) => `[${chunk.chunk_id}] ${chunk.text}`).join("\n\n");
}

export type GeneratedAnswer = {
  answer: string;
  // initialChunks plus every chunk retrieved via search_documents tool calls --
  // the model can cite any of these, so the caller must return this full set
  // (not just initialChunks) for citation lookups to find tool-retrieved chunks.
  chunks: QueryResult[];
};

export async function generateAnswer(
  question: string,
  initialChunks: QueryResult[],
): Promise<GeneratedAnswer> {
  const context =
    initialChunks.length > 0 ? formatChunks(initialChunks) : "(no relevant documents found)";

  const seenChunks = new Map<string, QueryResult>(initialChunks.map((c) => [c.chunk_id, c]));

  const messages: MessageParam[] = [
    { role: "user", content: `Context:\n${context}\n\nQuestion: ${question}` },
  ];

  for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
    const response = await getClient().messages.create({
      model: MODEL_ID,
      max_tokens: 1024,
      system: SYSTEM_PROMPT,
      tools: [SEARCH_TOOL],
      messages,
    });

    messages.push({ role: "assistant", content: response.content });

    if (response.stop_reason !== "tool_use") {
      const textBlock = response.content.find((block) => block.type === "text");
      return { answer: textBlock?.text ?? "", chunks: Array.from(seenChunks.values()) };
    }

    const toolResults: ToolResultBlockParam[] = [];
    for (const block of response.content) {
      if (block.type !== "tool_use" || block.name !== "search_documents") continue;
      const input = block.input as { query: string; top_k?: number };
      const vector = await embedText(input.query);
      const chunks = await similaritySearch(vector, input.top_k ?? 5);
      for (const chunk of chunks) seenChunks.set(chunk.chunk_id, chunk);
      toolResults.push({
        type: "tool_result",
        tool_use_id: block.id,
        content: chunks.length > 0 ? formatChunks(chunks) : "No matching chunks found for this query.",
      });
    }
    messages.push({ role: "user", content: toolResults });
  }

  return {
    answer: "I wasn't able to find enough information after multiple searches.",
    chunks: Array.from(seenChunks.values()),
  };
}
