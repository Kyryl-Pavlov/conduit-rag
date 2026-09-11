// Matches the design doc's §1.4 API contract verbatim.

export type QueryRequest = {
  query: string;
  top_k?: number;
};

export type QueryResult = {
  chunk_id: string;
  file_id: string;
  text: string;
  metadata: Record<string, unknown>;
  similarity: number;
};

export type QueryResponse = {
  query: string;
  results: QueryResult[];
  answer: string;
};

export type FileStatusValue = "processing" | "indexed" | "failed";

export type StatusResponse = {
  file_id: string;
  status: FileStatusValue;
  completed_workers: number;
  total_workers: number;
};

export type UploadUrlResponse = {
  file_id: string;
  upload_url: string;
};

export type FileRecord = {
  file_id: string;
  status: FileStatusValue;
  completed_workers: number;
  total_workers: number;
  file_size_bytes: number;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  results?: QueryResult[];
};
