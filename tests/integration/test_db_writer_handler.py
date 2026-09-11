"""Exercises db_writer against moto-mocked SQS/DynamoDB and the real local
Postgres from infra/local (DB_MODE=psycopg) -- per the design doc's own
guidance (section 4.2) to test pgvector against the real thing, not mocks.

Requires `docker compose up -d postgres` in infra/local/ with
infra/local/postgres-init/002-schema.sql applied. Skipped if Postgres isn't
reachable on localhost:5432.
"""

from __future__ import annotations

import json

import boto3
import psycopg
import pytest
from moto import mock_aws

import db_writer.handler as handler_module
from db_writer.handler import handler

FILE_STATUS_TABLE = "test-file-status"
CHUNK_COMPLETION_TABLE = "test-chunk-completion"
REGION = "us-east-1"

PG_CONFIG = dict(
    host="localhost",
    port=5432,
    dbname="conduit_rag",
    user="conduit_rag",
    password="conduit_rag",
)


def _postgres_available() -> bool:
    try:
        with psycopg.connect(**PG_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('chunks')")
                return cur.fetchone()[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="local Postgres (infra/local) not reachable on localhost:5432 with the chunks schema",
)


def _write_message(file_id: str, worker_index: int, chunk_index: int, chunk_count: int) -> dict:
    return {
        "chunk_id": f"{file_id}_w{worker_index}_c{chunk_index}",
        "file_id": file_id,
        "worker_index": worker_index,
        "vector": [0.1] * 1024,  # chunks.embedding is a fixed VECTOR(1024) column
        "text": f"chunk {chunk_index} of partition {worker_index}",
        "metadata": {
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "total_workers": 2,
        },
    }


@pytest.fixture
def env(monkeypatch):
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName=FILE_STATUS_TABLE,
            KeySchema=[{"AttributeName": "file_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "file_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.create_table(
            TableName=CHUNK_COMPLETION_TABLE,
            KeySchema=[{"AttributeName": "chunk_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "chunk_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        monkeypatch.setattr(handler_module, "FILE_STATUS_TABLE", FILE_STATUS_TABLE)
        monkeypatch.setattr(handler_module, "CHUNK_COMPLETION_TABLE", CHUNK_COMPLETION_TABLE)

        import common.vectordb as vectordb_module

        monkeypatch.setattr(vectordb_module, "DB_MODE", "psycopg")
        monkeypatch.setattr(
            vectordb_module,
            "_connect_psycopg",
            lambda: psycopg.connect(**PG_CONFIG),
        )

        file_status_table = dynamodb.Table(FILE_STATUS_TABLE)
        file_status_table.put_item(
            Item={
                "file_id": "int-test.txt",
                "status": "processing",
                "total_workers": 2,
                "completed_workers": 0,
                "chunks_written": {},
            }
        )

        yield {"dynamodb": dynamodb, "file_status_table": file_status_table}

        with psycopg.connect(**PG_CONFIG) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunks WHERE file_id = %s", ("int-test.txt",))
            conn.commit()


def test_partition_marked_complete_only_after_all_its_chunks_written(env):
    file_id = "int-test.txt"
    event = {
        "Records": [
            {
                "body": json.dumps(
                    _write_message(file_id, worker_index=0, chunk_index=0, chunk_count=2)
                )
            },
            {
                "body": json.dumps(
                    _write_message(file_id, worker_index=0, chunk_index=1, chunk_count=2)
                )
            },
        ]
    }

    handler(event, None)

    item = env["file_status_table"].get_item(Key={"file_id": file_id})["Item"]
    assert item["chunks_written"]["0"] == 2
    assert item["completed_workers"] == 1
    assert item["status"] == "processing"  # only 1 of 2 partitions done

    with psycopg.connect(**PG_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, text FROM chunks WHERE file_id = %s ORDER BY chunk_id", (file_id,)
            )
            rows = cur.fetchall()
    assert [r[0] for r in rows] == [f"{file_id}_w0_c0", f"{file_id}_w0_c1"]


def test_file_marked_indexed_once_all_partitions_complete(env):
    file_id = "int-test.txt"
    messages = [
        _write_message(file_id, worker_index=0, chunk_index=0, chunk_count=1),
        _write_message(file_id, worker_index=1, chunk_index=0, chunk_count=1),
    ]
    event = {"Records": [{"body": json.dumps(m)} for m in messages]}

    handler(event, None)

    item = env["file_status_table"].get_item(Key={"file_id": file_id})["Item"]
    assert item["completed_workers"] == 2
    assert item["status"] == "indexed"


def test_redelivered_message_does_not_double_count(env):
    file_id = "int-test.txt"
    message = _write_message(file_id, worker_index=0, chunk_index=0, chunk_count=2)
    event = {"Records": [{"body": json.dumps(message)}]}

    handler(event, None)
    handler(event, None)  # redelivery of the exact same message

    item = env["file_status_table"].get_item(Key={"file_id": file_id})["Item"]
    assert item["chunks_written"]["0"] == 1  # not 2
    assert item["completed_workers"] == 0  # chunk_count is 2, only 1 written
