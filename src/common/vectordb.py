"""Vector store client with two backends behind one interface.

Production Aurora is reached via the RDS Data API (no VPC needed for any
Lambda in this pipeline -- see infra/cloud/modules/aurora's comments). The
Data API has no local emulator, so local docker-compose talks to the plain
`pgvector/pgvector` container over the normal Postgres wire protocol via
psycopg instead. Toggle with DB_MODE=data_api|psycopg (default data_api,
matching the EMBEDDINGS_PROVIDER=fake toggle already used for Bedrock).

Both backends run equivalent SQL against the same `chunks` table (see
infra/local/postgres-init/002-schema.sql / infra/cloud/modules/aurora's
schema null_resource, which must stay identical).
"""

from __future__ import annotations

import json
import os

import boto3

DB_MODE = os.environ.get("DB_MODE", "data_api")

_UPSERT_COLUMNS = """
    INSERT INTO chunks (chunk_id, file_id, text, embedding, metadata)
    VALUES ({placeholders})
    ON CONFLICT (chunk_id) DO UPDATE SET
      file_id = EXCLUDED.file_id,
      text = EXCLUDED.text,
      embedding = EXCLUDED.embedding,
      metadata = EXCLUDED.metadata
"""


def _vector_literal(vector: list[float]) -> str:
    """pgvector accepts a JSON-array-shaped text literal, e.g. '[0.1,0.2]'."""
    return json.dumps(vector)


class VectorDBClient:
    """One instance per Lambda invocation -- open at the top of the handler,
    `close()` in `finally`, mirroring "one Aurora connection per invocation"
    (meaningful for the psycopg backend; the Data API backend is a stateless
    HTTPS client, so close() is a no-op there)."""

    def __init__(self) -> None:
        self._mode = DB_MODE
        if self._mode == "psycopg":
            self._conn = _connect_psycopg()
        else:
            self._client = boto3.client("rds-data")
            self._cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
            self._secret_arn = os.environ["AURORA_SECRET_ARN"]
            self._database = os.environ["AURORA_DATABASE_NAME"]

    def upsert_chunk(
        self,
        chunk_id: str,
        file_id: str,
        text: str,
        vector: list[float],
        metadata: dict,
    ) -> None:
        if self._mode == "psycopg":
            self._upsert_psycopg(chunk_id, file_id, text, vector, metadata)
        else:
            self._upsert_data_api(chunk_id, file_id, text, vector, metadata)

    def _upsert_psycopg(
        self, chunk_id: str, file_id: str, text: str, vector: list[float], metadata: dict
    ) -> None:
        sql = _UPSERT_COLUMNS.format(placeholders="%s, %s, %s, %s::vector, %s::jsonb")
        with self._conn.cursor() as cur:
            cur.execute(
                sql,
                (chunk_id, file_id, text, _vector_literal(vector), json.dumps(metadata)),
            )
        self._conn.commit()

    def _upsert_data_api(
        self, chunk_id: str, file_id: str, text: str, vector: list[float], metadata: dict
    ) -> None:
        sql = _UPSERT_COLUMNS.format(
            placeholders=(
                ":chunk_id, :file_id, :text, "
                "CAST(:embedding AS vector), CAST(:metadata AS jsonb)"
            )
        )
        self._client.execute_statement(
            resourceArn=self._cluster_arn,
            secretArn=self._secret_arn,
            database=self._database,
            sql=sql,
            parameters=[
                {"name": "chunk_id", "value": {"stringValue": chunk_id}},
                {"name": "file_id", "value": {"stringValue": file_id}},
                {"name": "text", "value": {"stringValue": text}},
                {"name": "embedding", "value": {"stringValue": _vector_literal(vector)}},
                {"name": "metadata", "value": {"stringValue": json.dumps(metadata)}},
            ],
        )

    def close(self) -> None:
        if self._mode == "psycopg":
            self._conn.close()


def _connect_psycopg():
    import psycopg

    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "conduit_rag"),
        user=os.environ.get("POSTGRES_USER", "conduit_rag"),
        password=os.environ.get("POSTGRES_PASSWORD", "conduit_rag"),
    )
