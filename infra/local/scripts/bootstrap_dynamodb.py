"""Idempotent local DynamoDB table creation, mirroring
infra/cloud/modules/dynamodb/main.tf exactly (same keys, same types).

Runs once as a one-shot compose service against dynamodb-local; safe to
re-run (skips tables that already exist).
"""

from __future__ import annotations

import logging
import time

import boto3
from botocore.exceptions import ConnectionClosedError, EndpointConnectionError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONNECT_RETRIES = 20
RETRY_DELAY_SECONDS = 3

TABLES = [
    {
        "TableName": "file-status",
        "KeySchema": [{"AttributeName": "file_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "file_id", "AttributeType": "S"}],
    },
    {
        "TableName": "chunk-completion",
        "KeySchema": [{"AttributeName": "chunk_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "chunk_id", "AttributeType": "S"}],
    },
]


def _list_tables_with_retry(dynamodb) -> set[str]:
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            return set(dynamodb.list_tables()["TableNames"])
        except (EndpointConnectionError, ConnectionClosedError) as e:
            logger.warning(
                "dynamodb-local not reachable yet (attempt %s/%s), retrying in %ss: %s",
                attempt,
                CONNECT_RETRIES,
                RETRY_DELAY_SECONDS,
                e,
            )
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError("dynamodb-local never became reachable")


def main() -> None:
    dynamodb = boto3.client("dynamodb")
    existing = _list_tables_with_retry(dynamodb)

    for table in TABLES:
        name = table["TableName"]
        if name in existing:
            logger.info("table already exists, skipping name=%s", name)
            continue
        dynamodb.create_table(BillingMode="PAY_PER_REQUEST", **table)
        dynamodb.get_waiter("table_exists").wait(TableName=name)
        logger.info("created table name=%s", name)


if __name__ == "__main__":
    main()
