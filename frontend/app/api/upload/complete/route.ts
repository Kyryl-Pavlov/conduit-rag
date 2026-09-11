import { SendMessageCommand } from "@aws-sdk/client-sqs";
import { NextResponse } from "next/server";

import { getSqsClient } from "@/lib/aws-clients";

const UPLOAD_BUCKET = process.env.UPLOAD_BUCKET ?? "conduit-rag-dev-uploads";
const DISPATCH_QUEUE_URL = process.env.DISPATCH_QUEUE_URL;

/**
 * Local-only substitute for the real S3 bucket notification defined in
 * infra/cloud/main.tf, which isn't reproducible locally (MinIO has no
 * generic bridge to an arbitrary SQS-protocol endpoint like ElasticMQ) --
 * see infra/local/scripts/simulate_upload.py, which does the same thing from
 * the CLI. Safe to call in a real deployment too: dispatcher/handler.py's
 * create_file_status_record is idempotent (conditional put on file_id), so
 * if a real S3 notification also fires for the same upload, one of the two
 * arrivals is just a no-op skip, not a duplicate dispatch.
 */
export async function POST(request: Request) {
  const body = await request.json();
  const fileId = body?.file_id;
  const size = body?.size;

  if (typeof fileId !== "string" || fileId.length === 0 || typeof size !== "number") {
    return NextResponse.json({ error: "file_id and size are required" }, { status: 400 });
  }
  if (size <= 0) {
    return NextResponse.json({ error: "uploaded file is empty" }, { status: 400 });
  }
  if (!DISPATCH_QUEUE_URL) {
    return NextResponse.json({ error: "DISPATCH_QUEUE_URL is not configured" }, { status: 500 });
  }

  const s3Event = {
    Records: [
      {
        eventName: "ObjectCreated:Put",
        s3: {
          bucket: { name: UPLOAD_BUCKET },
          object: { key: fileId, size },
        },
      },
    ],
  };

  await getSqsClient().send(
    new SendMessageCommand({
      QueueUrl: DISPATCH_QUEUE_URL,
      MessageBody: JSON.stringify(s3Event),
    }),
  );

  return NextResponse.json({ ok: true });
}
