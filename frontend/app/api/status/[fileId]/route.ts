import { GetCommand } from "@aws-sdk/lib-dynamodb";
import { NextResponse } from "next/server";

import { getDynamoClient } from "@/lib/aws-clients";
import type { StatusResponse } from "@/lib/types";

const FILE_STATUS_TABLE = process.env.FILE_STATUS_TABLE ?? "file-status";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ fileId: string }> },
) {
  const { fileId } = await params;

  const result = await getDynamoClient().send(
    new GetCommand({
      TableName: FILE_STATUS_TABLE,
      Key: { file_id: fileId },
    }),
  );

  if (!result.Item) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  const response: StatusResponse = {
    file_id: result.Item.file_id,
    status: result.Item.status,
    completed_workers: result.Item.completed_workers,
    total_workers: result.Item.total_workers,
  };
  return NextResponse.json(response);
}
