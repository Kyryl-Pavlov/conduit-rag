"""Local entrypoint: polls write-queue in batches of up to 10 and invokes
db_writer.handler.handler."""

import os

from sqs_poller import poll_queue_batched

from db_writer.handler import handler

if __name__ == "__main__":
    queue_url = os.environ["WRITE_QUEUE_URL"]
    poll_queue_batched(queue_url, handler)
