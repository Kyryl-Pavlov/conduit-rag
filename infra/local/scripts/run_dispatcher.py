"""Local entrypoint: polls dispatch-queue and invokes dispatcher.handler.handler."""

import os

from sqs_poller import poll_queue

from dispatcher.handler import handler

if __name__ == "__main__":
    queue_url = os.environ["DISPATCH_QUEUE_URL"]
    poll_queue(queue_url, handler)
