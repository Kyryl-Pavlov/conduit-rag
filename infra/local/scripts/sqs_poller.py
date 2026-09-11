"""Generic long-polling SQS consumer for local dev.

Mimics AWS Lambda's SQS event-source-mapping closely enough for local
iteration: long-polls one message at a time (mirroring the real
`batch_size = 1` event source mappings in infra/cloud/modules/lambda), wraps it
as a synthetic Lambda event, invokes the real handler unchanged, and deletes
the message only on success. On exception the message is left on the queue --
it becomes visible again after the queue's visibility timeout expires,
mirroring Lambda's retry-via-redrive-policy behavior (eventually landing in
the DLQ per infra/local/elasticmq.conf's redrive config).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import boto3
from botocore.exceptions import ConnectionClosedError, EndpointConnectionError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WAIT_TIME_SECONDS = 20
MAX_NUMBER_OF_MESSAGES = 1
RETRY_DELAY_SECONDS = 3


def poll_queue(queue_url: str, handler_fn: Callable[[dict, None], None]) -> None:
    sqs = boto3.client("sqs")
    logger.info("polling queue_url=%s", queue_url)

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=MAX_NUMBER_OF_MESSAGES,
                WaitTimeSeconds=WAIT_TIME_SECONDS,
            )
        except (EndpointConnectionError, ConnectionClosedError) as e:
            # Queue not reachable yet (e.g. still starting up) -- retry rather
            # than crash-loop the container.
            logger.warning("queue not reachable, retrying in %ss: %s", RETRY_DELAY_SECONDS, e)
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        messages = response.get("Messages", [])
        for message in messages:
            event = {"Records": [{"body": message["Body"]}]}
            try:
                handler_fn(event, None)
            except Exception:
                logger.exception(
                    "handler failed, leaving message on queue for retry message_id=%s",
                    message["MessageId"],
                )
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            logger.info("processed and deleted message_id=%s", message["MessageId"])


def poll_queue_batched(
    queue_url: str, handler_fn: Callable[[dict, None], None], max_messages: int = 10
) -> None:
    """Like poll_queue, but bundles up to `max_messages` into one synthetic
    Lambda event (multiple Records), matching a real batch_size=10 SQS event
    source mapping -- for db_writer, which processes a batch under one
    Aurora/Postgres connection. All-or-nothing: the whole batch is deleted
    only if the handler succeeds; on exception, every message in the batch is
    left for retry (matches AWS Lambda's default SQS batch-failure behavior).
    Safe here because every step db_writer takes is idempotent (see
    common/db.py's mark_chunk_completion), so retrying an already-partially-
    processed batch is a no-op, not a correctness problem.
    """
    sqs = boto3.client("sqs")
    logger.info("polling (batched) queue_url=%s", queue_url)

    while True:
        try:
            response = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=WAIT_TIME_SECONDS,
            )
        except (EndpointConnectionError, ConnectionClosedError) as e:
            logger.warning("queue not reachable, retrying in %ss: %s", RETRY_DELAY_SECONDS, e)
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        messages = response.get("Messages", [])
        if not messages:
            continue

        event = {"Records": [{"body": m["Body"]} for m in messages]}
        try:
            handler_fn(event, None)
        except Exception:
            logger.exception(
                "handler failed, leaving batch of %d message(s) on queue for retry",
                len(messages),
            )
            continue

        for message in messages:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
        logger.info("processed and deleted batch of %d message(s)", len(messages))
