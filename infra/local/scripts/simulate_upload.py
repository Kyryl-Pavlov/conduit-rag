"""Local-only stand-in for the real S3 -> dispatch-queue bucket notification
(infra/cloud/main.tf's aws_s3_bucket_notification). MinIO has no generic
bridge to an arbitrary SQS-protocol endpoint like ElasticMQ, and building one
isn't worth it for a dev loop with no frontend yet -- so this script uploads
the file, then sends the same S3 ObjectCreated event shape dispatcher/handler.py
already expects from a real notification.

Meant to be run from the host machine against docker-compose's published
ports (not from inside a container), hence the localhost values below.

This script only ever talks to the local emulator stack, never real AWS, so
these are forced rather than defaulted -- a real AWS_ACCESS_KEY_ID already
exported in the host shell (common for engineers who use the AWS CLI day to
day) must not silently leak through and produce a confusing MinIO auth error.

Usage: python simulate_upload.py <path-to-a-.txt-file>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["AWS_ACCESS_KEY_ID"] = "localaccesskey"
os.environ["AWS_SECRET_ACCESS_KEY"] = "localsecretkey"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ENDPOINT_URL_S3"] = "http://localhost:9000"
os.environ["AWS_ENDPOINT_URL_SQS"] = "http://localhost:9324"
os.environ["AWS_CONFIG_FILE"] = str(Path(__file__).resolve().parent.parent / "aws-config")

import boto3  # noqa: E402  (must follow the AWS_* env overrides above)

UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "conduit-rag-dev-uploads")
DISPATCH_QUEUE_URL = os.environ.get(
    "DISPATCH_QUEUE_URL", "http://localhost:9324/queue/dispatch-queue"
)


def simulate_upload(file_path: Path) -> None:
    key = file_path.name
    body = file_path.read_bytes()

    s3 = boto3.client("s3")
    s3.put_object(Bucket=UPLOAD_BUCKET, Key=key, Body=body)
    print(f"uploaded key={key} bytes={len(body)} bucket={UPLOAD_BUCKET}")

    s3_event = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": UPLOAD_BUCKET},
                    "object": {"key": key, "size": len(body)},
                },
            }
        ]
    }
    sqs = boto3.client("sqs")
    sqs.send_message(QueueUrl=DISPATCH_QUEUE_URL, MessageBody=json.dumps(s3_event))
    print(f"sent synthetic S3 event to dispatch-queue queue_url={DISPATCH_QUEUE_URL}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: python {sys.argv[0]} <path-to-file>", file=sys.stderr)
        sys.exit(1)
    simulate_upload(Path(sys.argv[1]))
