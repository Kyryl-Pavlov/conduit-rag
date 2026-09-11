import { NextResponse } from "next/server";

import { embedText } from "@/lib/bedrock";
import { generateAnswer } from "@/lib/generation";
import type { QueryRequest, QueryResponse } from "@/lib/types";
import { similaritySearch } from "@/lib/vectordb";

const DEFAULT_TOP_K = 5;

export async function POST(request: Request) {
  const body: QueryRequest = await request.json();
  const topK = body.top_k ?? DEFAULT_TOP_K;

  const queryVector = await embedText(body.query);
  const results = await similaritySearch(queryVector, topK);
  const { answer, chunks } = await generateAnswer(body.query, results);

  const response: QueryResponse = { query: body.query, results: chunks, answer };
  return NextResponse.json(response);
}
