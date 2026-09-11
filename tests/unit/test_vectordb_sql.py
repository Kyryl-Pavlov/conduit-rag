import json
from unittest.mock import MagicMock

import common.vectordb as vectordb_module
from common.vectordb import VectorDBClient, _vector_literal


def test_vector_literal_is_json_array():
    assert _vector_literal([0.1, 0.2, 0.3]) == json.dumps([0.1, 0.2, 0.3])


def test_data_api_upsert_sends_expected_statement(monkeypatch):
    monkeypatch.setattr(vectordb_module, "DB_MODE", "data_api")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:123456789012:cluster:test")
    monkeypatch.setenv(
        "AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"
    )
    monkeypatch.setenv("AURORA_DATABASE_NAME", "conduit_rag")

    fake_client = MagicMock()
    monkeypatch.setattr(vectordb_module.boto3, "client", lambda service: fake_client)

    client = VectorDBClient()
    client.upsert_chunk(
        chunk_id="file.txt_w0_c0",
        file_id="file.txt",
        text="hello",
        vector=[0.1, 0.2],
        metadata={"chunk_index": 0},
    )

    fake_client.execute_statement.assert_called_once()
    kwargs = fake_client.execute_statement.call_args.kwargs
    assert kwargs["resourceArn"] == "arn:aws:rds:us-east-1:123456789012:cluster:test"
    assert kwargs["secretArn"] == "arn:aws:secretsmanager:us-east-1:123456789012:secret:test"
    assert kwargs["database"] == "conduit_rag"
    assert "CAST(:embedding AS vector)" in kwargs["sql"]
    assert "CAST(:metadata AS jsonb)" in kwargs["sql"]

    params = {p["name"]: p["value"] for p in kwargs["parameters"]}
    assert params["chunk_id"] == {"stringValue": "file.txt_w0_c0"}
    assert params["file_id"] == {"stringValue": "file.txt"}
    assert params["embedding"] == {"stringValue": json.dumps([0.1, 0.2])}
    assert params["metadata"] == {"stringValue": json.dumps({"chunk_index": 0})}

    client.close()  # no-op for data_api, must not raise


def test_psycopg_upsert_sends_expected_statement(monkeypatch):
    monkeypatch.setattr(vectordb_module, "DB_MODE", "psycopg")

    fake_cursor = MagicMock()
    fake_cursor.__enter__ = MagicMock(return_value=fake_cursor)
    fake_cursor.__exit__ = MagicMock(return_value=False)
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    monkeypatch.setattr(vectordb_module, "_connect_psycopg", lambda: fake_conn)

    client = VectorDBClient()
    client.upsert_chunk(
        chunk_id="file.txt_w0_c0",
        file_id="file.txt",
        text="hello",
        vector=[0.1, 0.2],
        metadata={"chunk_index": 0},
    )

    fake_cursor.execute.assert_called_once()
    sql, params = fake_cursor.execute.call_args.args
    assert "%s::vector" in sql
    assert "%s::jsonb" in sql
    assert params == (
        "file.txt_w0_c0",
        "file.txt",
        "hello",
        json.dumps([0.1, 0.2]),
        json.dumps({"chunk_index": 0}),
    )
    fake_conn.commit.assert_called_once()

    client.close()
    fake_conn.close.assert_called_once()
