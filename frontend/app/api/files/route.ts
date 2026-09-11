import { ScanCommand } from "@aws-sdk/lib-dynamodb";
import { NextResponse } from "next/server";

import { getDynamoClient } from "@/lib/aws-clients";
import type { FileRecord } from "@/lib/types";

const FILE_STATUS_TABLE = process.env.FILE_STATUS_TABLE ?? "file-status";

// Table-wide Scan is fine at this phase's scale (dev/demo, single-digit-to-low-hundreds
// of files); revisit with a paginated GSI query if the table grows large.
export async function GET() {
  const result = await getDynamoClient().send(
    new ScanCommand({ TableName: FILE_STATUS_TABLE }),
  );

  const files: FileRecord[] = (result.Items ?? [])
    .map((item) => ({
      file_id: item.file_id,
      status: item.status,
      completed_workers: item.completed_workers,
      total_workers: item.total_workers,
      file_size_bytes: item.file_size_bytes,
      created_at: item.created_at,
      updated_at: item.updated_at,
    }))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  return NextResponse.json({ files });
}
