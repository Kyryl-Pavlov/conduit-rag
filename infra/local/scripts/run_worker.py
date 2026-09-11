"""Local entrypoint: polls worker-queue and invokes worker.handler.handler."""

import os

from sqs_poller import poll_queue

from worker.handler import handler

if __name__ == "__main__":
    queue_url = os.environ["WORKER_QUEUE_URL"]
    poll_queue(queue_url, handler)
