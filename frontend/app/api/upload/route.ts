import { PutObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { NextResponse } from "next/server";

import { getS3Client } from "@/lib/aws-clients";
import type { UploadUrlResponse } from "@/lib/types";

const UPLOAD_BUCKET = process.env.UPLOAD_BUCKET ?? "conduit-rag-dev-uploads";
const PRESIGN_EXPIRES_SECONDS = 300;

export async function POST(request: Request) {
  const body = await request.json();
  const filename = body?.filename;
  const contentType = body?.contentType ?? "application/octet-stream";

  if (typeof filename !== "string" || filename.length === 0) {
    return NextResponse.json({ error: "filename is required" }, { status: 400 });
  }

  // file_id is the S3 key verbatim, matching dispatcher/handler.py's contract.
  const command = new PutObjectCommand({
    Bucket: UPLOAD_BUCKET,
    Key: filename,
    ContentType: contentType,
  });

  const uploadUrl = await getSignedUrl(getS3Client(), command, {
    expiresIn: PRESIGN_EXPIRES_SECONDS,
  });

  const response: UploadUrlResponse = { file_id: filename, upload_url: uploadUrl };
  return NextResponse.json(response);
}
