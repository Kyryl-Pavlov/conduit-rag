"""Fargate task entrypoint for real (non-fake) video analysis. Started by the
video-analysis Step Functions state machine's `ecs:runTask.sync` state.
Unlike every Lambda in this repo, this process has no imposed timeout -- it
does the FULL synchronous job in one process (download, call VIDEO_PROVIDER,
wait however long that call/poll takes, stage results, exit), keeping any
provider-side async submit/poll complexity entirely inside this process
rather than surfacing it to Step Functions.

Exit code is the ONLY status signal back to Step Functions: 0 means
ecs:runTask.sync proceeds to the success branch (InvokeVideoCompletionSucceeded);
nonzero means the state machine's Catch routes to the failure branch
(InvokeVideoCompletionFailed). No other side-channel status reporting exists.

AUTHORED, NOT EXERCISED: VIDEO_PROVIDER=fake (the only implemented provider)
never reaches this module -- dispatcher fabricates a fake result and fans out
directly, never starting a Step Functions execution. This module's
real-provider code path has never run end-to-end and can't until a real
provider is implemented in common/video.analyze_video and this stack is
actually deployed (terraform apply is out of scope for this pass).
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

import boto3

from common.video import VIDEO_PROVIDER, analyze_video

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXTRACTED_TEXT_SUFFIX = ".extracted"

s3 = boto3.client("s3")


def main() -> int:
    file_id = os.environ["FILE_ID"]
    s3_bucket = os.environ["S3_BUCKET"]

    if VIDEO_PROVIDER == "fake":
        logger.error(
            "VIDEO_PROVIDER=fake reached the Fargate task for file_id=%s -- dispatcher "
            "should have short-circuited before ever starting this execution; this "
            "indicates a bug, not a normal fake-mode flow.",
            file_id,
        )
        return 1

    try:
        with tempfile.NamedTemporaryFile() as tmp:
            s3.download_fileobj(s3_bucket, file_id, tmp)
            tmp.flush()
            jsonl_bytes = analyze_video(tmp.name, VIDEO_PROVIDER)
    except Exception:
        logger.exception("video analysis failed file_id=%s", file_id)
        return 1

    extracted_key = f"extracted/{file_id}{EXTRACTED_TEXT_SUFFIX}"
    s3.put_object(
        Bucket=s3_bucket, Key=extracted_key, Body=jsonl_bytes, ContentType="application/x-ndjson"
    )
    logger.info("staged video analysis result file_id=%s extracted_key=%s", file_id, extracted_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
