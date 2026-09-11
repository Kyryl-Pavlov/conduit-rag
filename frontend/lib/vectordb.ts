import { RDSDataClient, ExecuteStatementCommand } from "@aws-sdk/client-rds-data";
import { Client as PgClient } from "pg";

// Aliased: `pg`'s own query() return value is also structurally a
// "QueryResult" -- this import is our app-level chunk-result shape instead.
import type { QueryResult as ChunkResult } from "./types";

/**
 * Mirrors src/common/vectordb.py's DB_MODE toggle: production Aurora is
 * reached via the RDS Data API (no VPC needed), which has no local emulator,
 * so local docker-compose talks to the plain `pgvector/pgvector` container
 * over the wire protocol via `pg` instead. Both run against the same
 * `chunks` table (infra/local/postgres-init/002-schema.sql /
 * infra/cloud/modules/aurora).
 */
const DB_MODE = process.env.DB_MODE ?? "data_api";

export async function deleteChunksByFileId(fileId: string): Promise<void> {
  if (DB_MODE === "psycopg") {
    await deletePsycopg(fileId);
  } else {
    await deleteDataApi(fileId);
  }
}

async function deletePsycopg(fileId: string): Promise<void> {
  const client = new PgClient({
    host: process.env.POSTGRES_HOST ?? "localhost",
    port: Number(process.env.POSTGRES_PORT ?? "5432"),
    database: process.env.POSTGRES_DB ?? "conduit_rag",
    user: process.env.POSTGRES_USER ?? "conduit_rag",
    password: process.env.POSTGRES_PASSWORD ?? "conduit_rag",
  });
  await client.connect();
  try {
    await client.query("DELETE FROM chunks WHERE file_id = $1", [fileId]);
  } finally {
    await client.end();
  }
}

async function deleteDataApi(fileId: string): Promise<void> {
  const client = new RDSDataClient({});
  await client.send(
    new ExecuteStatementCommand({
      resourceArn: process.env.AURORA_CLUSTER_ARN,
      secretArn: process.env.AURORA_SECRET_ARN,
      database: process.env.AURORA_DATABASE_NAME,
      sql: "DELETE FROM chunks WHERE file_id = :file_id",
      parameters: [{ name: "file_id", value: { stringValue: fileId } }],
    }),
  );
}

/** pgvector accepts a JSON-array-shaped text literal, e.g. '[0.1,0.2]' -- mirrors common/vectordb.py's _vector_literal. */
function vectorLiteral(vector: number[]): string {
  return JSON.stringify(vector);
}

// Cosine distance via the chunks_embedding_hnsw_idx (vector_cosine_ops)
// index created in 002-schema.sql -- `<=>` returns distance, so similarity
// is 1 minus that.
const SIMILARITY_SEARCH_SQL = `
  SELECT chunk_id, file_id, text, metadata, 1 - (embedding <=> {embedding}) AS similarity
  FROM chunks
  ORDER BY embedding <=> {embedding}
  LIMIT {top_k}
`;

export async function similaritySearch(vector: number[], topK: number): Promise<ChunkResult[]> {
  if (DB_MODE === "psycopg") {
    return searchPsycopg(vector, topK);
  }
  return searchDataApi(vector, topK);
}

async function searchPsycopg(vector: number[], topK: number): Promise<ChunkResult[]> {
  const client = new PgClient({
    host: process.env.POSTGRES_HOST ?? "localhost",
    port: Number(process.env.POSTGRES_PORT ?? "5432"),
    database: process.env.POSTGRES_DB ?? "conduit_rag",
    user: process.env.POSTGRES_USER ?? "conduit_rag",
    password: process.env.POSTGRES_PASSWORD ?? "conduit_rag",
  });
  await client.connect();
  try {
    const sql = SIMILARITY_SEARCH_SQL.replace(/{embedding}/g, "$1::vector").replace(
      "{top_k}",
      "$2",
    );
    const result = await client.query(sql, [vectorLiteral(vector), topK]);
    return result.rows.map((row) => ({
      chunk_id: row.chunk_id,
      file_id: row.file_id,
      text: row.text,
      metadata: row.metadata,
      similarity: Number(row.similarity),
    }));
  } finally {
    await client.end();
  }
}

async function searchDataApi(vector: number[], topK: number): Promise<ChunkResult[]> {
  const client = new RDSDataClient({});
  const sql = SIMILARITY_SEARCH_SQL.replace(
    /{embedding}/g,
    "CAST(:embedding AS vector)",
  ).replace("{top_k}", ":top_k");
  const response = await client.send(
    new ExecuteStatementCommand({
      resourceArn: process.env.AURORA_CLUSTER_ARN,
      secretArn: process.env.AURORA_SECRET_ARN,
      database: process.env.AURORA_DATABASE_NAME,
      sql,
      parameters: [
        { name: "embedding", value: { stringValue: vectorLiteral(vector) } },
        { name: "top_k", value: { longValue: topK } },
      ],
      formatRecordsAs: "JSON",
    }),
  );
  const rows: Array<{
    chunk_id: string;
    file_id: string;
    text: string;
    metadata: Record<string, unknown>;
    similarity: number;
  }> = JSON.parse(response.formattedRecords ?? "[]");
  return rows.map((row) => ({
    chunk_id: row.chunk_id,
    file_id: row.file_id,
    text: row.text,
    metadata: row.metadata,
    similarity: row.similarity,
  }));
}
