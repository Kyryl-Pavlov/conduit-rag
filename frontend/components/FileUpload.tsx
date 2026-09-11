"use client";

import { useState } from "react";

import { confirmUploadComplete, putFileToUploadUrl, requestUploadUrl } from "@/lib/api";

export default function FileUpload({
  onUploaded,
}: {
  onUploaded?: (fileId: string) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleFileSelected(file: File) {
    setUploading(true);
    setError(null);
    try {
      const contentType = file.type || "application/octet-stream";
      const { file_id, upload_url } = await requestUploadUrl(file.name, contentType);
      await putFileToUploadUrl(upload_url, file, contentType);
      await confirmUploadComplete(file_id, file.size);
      onUploaded?.(file_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <label className="flex cursor-pointer flex-col items-center gap-2 rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 hover:border-zinc-400 dark:border-zinc-700">
        <span>{uploading ? "Uploading…" : "Click to choose a .txt or .pdf file to ingest"}</span>
        <input
          type="file"
          accept=".txt,text/plain,.pdf,application/pdf"
          className="hidden"
          disabled={uploading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFileSelected(file);
            e.target.value = "";
          }}
        />
      </label>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </div>
  );
}
