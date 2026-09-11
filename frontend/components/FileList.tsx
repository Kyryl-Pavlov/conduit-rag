"use client";

import { useEffect, useState } from "react";

import { deleteFile, listFiles } from "@/lib/api";
import type { FileRecord } from "@/lib/types";

const POLL_INTERVAL_MS = 3000;

const STATUS_STYLES: Record<string, string> = {
  indexed: "text-green-600 dark:text-green-400",
  processing: "text-amber-600 dark:text-amber-400",
  failed: "text-red-600 dark:text-red-400",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileList({
  pendingFileIds,
  onPendingResolved,
}: {
  pendingFileIds: string[];
  onPendingResolved: (fileId: string) => void;
}) {
  const [files, setFiles] = useState<FileRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualTick, setManualTick] = useState(0);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const result = await listFiles();
        if (cancelled) return;
        setFiles(result);
        setError(null);

        // A just-uploaded file may not show up in this snapshot yet -- the
        // dispatcher Lambda that creates its file_status record runs
        // asynchronously off an SQS message, so "no processing files in the
        // result" doesn't mean nothing is happening. Keep polling until every
        // pending upload has been observed with a terminal status.
        let stillPending = false;
        for (const fileId of pendingFileIds) {
          const match = result.find((f) => f.file_id === fileId);
          if (match && match.status !== "processing") {
            onPendingResolved(fileId);
          } else {
            stillPending = true;
          }
        }

        if (stillPending || result.some((f) => f.status === "processing")) {
          timer = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "failed to load files");
      }
    }

    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pendingFileIds, manualTick, onPendingResolved]);

  const loading = files === null && !error;

  async function handleDelete(fileId: string) {
    if (!window.confirm(`Delete "${fileId}"? This removes the file and its indexed data.`)) {
      return;
    }
    setDeletingId(fileId);
    try {
      await deleteFile(fileId);
      setFiles((current) => current?.filter((f) => f.file_id !== fileId) ?? current);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete file");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Files in storage
        </h2>
        <button
          type="button"
          onClick={() => setManualTick((n) => n + 1)}
          className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          Refresh
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {loading && <p className="px-2 text-sm text-zinc-500">Loading…</p>}
        {error && <p className="px-2 text-sm text-red-600">{error}</p>}
        {!loading && !error && files?.length === 0 && (
          <p className="px-2 text-sm text-zinc-500">No files uploaded yet.</p>
        )}
        <ul className="flex flex-col gap-1">
          {(files ?? []).map((file) => (
            <li
              key={file.file_id}
              className="flex items-start justify-between gap-2 rounded-md px-2 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <div className="min-w-0">
                <p className="truncate font-medium" title={file.file_id}>
                  {file.file_id}
                </p>
                <p className="flex flex-wrap items-center gap-x-1 text-xs text-zinc-500">
                  <span className={STATUS_STYLES[file.status] ?? ""}>{file.status}</span>
                  <span>· {formatBytes(file.file_size_bytes)}</span>
                  {file.status === "processing" && file.total_workers > 0 && (
                    <span>
                      ({file.completed_workers}/{file.total_workers})
                    </span>
                  )}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleDelete(file.file_id)}
                disabled={deletingId === file.file_id}
                className="shrink-0 text-xs text-zinc-500 hover:text-red-600 disabled:opacity-50 dark:hover:text-red-400"
              >
                {deletingId === file.file_id ? "Deleting…" : "Delete"}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
