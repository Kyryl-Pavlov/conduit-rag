import type {
  FileRecord,
  QueryRequest,
  QueryResponse,
  StatusResponse,
  UploadUrlResponse,
} from "./types";

export async function requestUploadUrl(
  filename: string,
  contentType: string,
): Promise<UploadUrlResponse> {
  const res = await fetch("/api/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, contentType }),
  });
  if (!res.ok) throw new Error(`upload URL request failed: ${res.status}`);
  return res.json();
}

export async function putFileToUploadUrl(
  uploadUrl: string,
  file: File,
  contentType: string,
): Promise<void> {
  const res = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": contentType },
    body: file,
  });
  if (!res.ok) throw new Error(`upload failed: ${res.status}`);
}

export async function confirmUploadComplete(fileId: string, size: number): Promise<void> {
  const res = await fetch("/api/upload/complete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, size }),
  });
  if (!res.ok) throw new Error(`confirm upload failed: ${res.status}`);
}

export async function getStatus(fileId: string): Promise<StatusResponse> {
  const res = await fetch(`/api/status/${encodeURIComponent(fileId)}`);
  if (!res.ok) throw new Error(`status request failed: ${res.status}`);
  return res.json();
}

export async function listFiles(): Promise<FileRecord[]> {
  const res = await fetch("/api/files");
  if (!res.ok) throw new Error(`file list request failed: ${res.status}`);
  const data: { files: FileRecord[] } = await res.json();
  return data.files;
}

export async function deleteFile(fileId: string): Promise<void> {
  const res = await fetch(`/api/files/${encodeURIComponent(fileId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`delete failed: ${res.status}`);
}

export async function queryApi(query: string, topK?: number): Promise<QueryResponse> {
  const body: QueryRequest = { query, top_k: topK };
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`query failed: ${res.status}`);
  return res.json();
}
