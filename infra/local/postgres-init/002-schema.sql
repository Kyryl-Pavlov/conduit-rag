-- Mirrors infra/cloud/modules/aurora's schema null_resource -- keep both in
-- sync. embedding is fixed at 1024 dims (Titan Text Embeddings V2's default,
-- see common/bedrock.py's DEFAULT_DIMENSIONS).

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL,
    text       TEXT NOT NULL,
    embedding  VECTOR(1024) NOT NULL,
    metadata   JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chunks_file_id_idx ON chunks (file_id);

-- Forward-looking for the future query_api Lambda; unused this phase.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
