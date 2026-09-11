import { InvokeModelCommand } from "@aws-sdk/client-bedrock-runtime";

import { getBedrockClient } from "./aws-clients";

/**
 * TS port of common/bedrock.py's embed_text -- must stay behaviorally
 * equivalent (same model, same dimensions) since query-time embeddings are
 * compared against chunk embeddings written by the Python worker. The exact
 * fake-mode vectors don't need to match byte-for-byte (see bedrock.py's
 * comment: fake vectors are retrieval-meaningless in both languages
 * already), but EMBEDDINGS_PROVIDER must be set the same way on both sides
 * or one side's real vectors get compared against the other's noise.
 */
const DEFAULT_MODEL_ID = "amazon.titan-embed-text-v2:0";
const DEFAULT_DIMENSIONS = 1024;

const EMBEDDINGS_PROVIDER = process.env.EMBEDDINGS_PROVIDER ?? "bedrock";

function fakeEmbed(text: string, dimensions: number): number[] {
  // Deterministic stand-in seeded by the text itself -- not required to
  // match Python's hash/RNG choice, only to be self-consistent.
  let seed = 0;
  for (let i = 0; i < text.length; i++) {
    seed = (seed * 31 + text.charCodeAt(i)) >>> 0;
  }
  const vector: number[] = [];
  for (let i = 0; i < dimensions; i++) {
    seed = (seed * 1103515245 + 12345) >>> 0;
    vector.push((seed / 0xffffffff) * 2 - 1);
  }
  return vector;
}

export async function embedText(
  text: string,
  modelId: string = DEFAULT_MODEL_ID,
  dimensions: number = DEFAULT_DIMENSIONS,
): Promise<number[]> {
  if (EMBEDDINGS_PROVIDER === "fake") {
    return fakeEmbed(text, dimensions);
  }
  const client = getBedrockClient();
  const response = await client.send(
    new InvokeModelCommand({
      modelId,
      body: JSON.stringify({ inputText: text, dimensions, normalize: true }),
      contentType: "application/json",
      accept: "application/json",
    }),
  );
  const payload: { embedding: number[] } = JSON.parse(
    Buffer.from(response.body as Uint8Array).toString("utf-8"),
  );
  return payload.embedding;
}
