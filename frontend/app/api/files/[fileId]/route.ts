import { DeleteObjectCommand } from "@aws-sdk/client-s3";
import { BatchWriteCommand, DeleteCommand, ScanCommand } from "@aws-sdk/lib-dynamodb";
import { NextResponse } from "next/server";

import { getDynamoClient, getS3ServerClient } from "@/lib/aws-clients";
import { deleteChunksByFileId } from "@/lib/vectordb";

const UPLOAD_BUCKET = process.env.UPLOAD_BUCKET ?? "conduit-rag-dev-uploads";
const FILE_STATUS_TABLE = process.env.FILE_STATUS_TABLE ?? "file-status";
const CHUNK_COMPLETION_TABLE = process.env.CHUNK_COMPLETION_TABLE ?? "chunk-completion";
const BATCH_WRITE_LIMIT = 25;

// Must match src/dispatcher/handler.py's EXTRACTED_TEXT_SUFFIX -- if that
// changes, this key stops matching real objects and PDF extractions leak.
const EXTRACTED_TEXT_SUFFIX = ".extracted";

// chunk_id is deterministically "{file_id}_w{worker_index}_c{local_chunk_num}"
// (see src/common/chunking.py's make_chunk_id) -- matched structurally
// rather than by prefix so a file_id that happens to contain "_w" can't
// produce a false match.
function chunkIdBelongsToFile(chunkId: string, fileId: string): boolean {
  if (!chunkId.startsWith(`${fileId}_w`)) return false;
  return /^_w\d+_c\d+$/.test(chunkId.slice(fileId.length));
}

function chunked<T>(items: T[], size: number): T[][] {
  const batches: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    batches.push(items.slice(i, i + size));
  }
  return batches;
}

async function deleteChunkCompletionRecords(fileId: string): Promise<void> {
  const ddb = getDynamoClient();

  // Table-wide Scan is fine at this phase's scale (dev/demo), same tradeoff
  // already accepted in app/api/files/route.ts -- there's no GSI on file_id.
  const result = await ddb.send(new ScanCommand({ TableName: CHUNK_COMPLETION_TABLE }));
  const matchingIds = (result.Items ?? [])
    .map((item) => item.chunk_id as string)
    .filter((chunkId) => chunkIdBelongsToFile(chunkId, fileId));

  for (const batch of chunked(matchingIds, BATCH_WRITE_LIMIT)) {
    await ddb.send(
      new BatchWriteCommand({
        RequestItems: {
          [CHUNK_COMPLETION_TABLE]: batch.map((chunkId) => ({
            DeleteRequest: { Key: { chunk_id: chunkId } },
          })),
        },
      }),
    );
  }
}

export async function DELETE(
  _request: Request,
  { params }: { params: Promise<{ fileId: string }> },
) {
  const { fileId } = await params;

  const s3 = getS3ServerClient();
  await s3.send(new DeleteObjectCommand({ Bucket: UPLOAD_BUCKET, Key: fileId }));
  // .pdf uploads stage extracted text at this derived key (see
  // src/dispatcher/handler.py) -- DeleteObject succeeds as a no-op if it
  // doesn't exist, so this is safe to run unconditionally for .txt files too.
  await s3.send(
    new DeleteObjectCommand({ Bucket: UPLOAD_BUCKET, Key: `extracted/${fileId}${EXTRACTED_TEXT_SUFFIX}` }),
  );
  await deleteChunksByFileId(fileId);
  await deleteChunkCompletionRecords(fileId);
  await getDynamoClient().send(
    new DeleteCommand({ TableName: FILE_STATUS_TABLE, Key: { file_id: fileId } }),
  );

  return NextResponse.json({ ok: true });
}
