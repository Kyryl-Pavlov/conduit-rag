"use client";

import { useCallback, useEffect, useState } from "react";

import FileList from "./FileList";
import FileUpload from "./FileUpload";
import { XIcon } from "./icons";

export default function Sidebar({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [pendingFileIds, setPendingFileIds] = useState<string[]>([]);

  const handleUploaded = useCallback((fileId: string) => {
    setPendingFileIds((ids) => [...ids, fileId]);
  }, []);

  const handlePendingResolved = useCallback((fileId: string) => {
    setPendingFileIds((ids) => ids.filter((id) => id !== fileId));
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex h-full w-72 shrink-0 flex-col border-r border-zinc-200 bg-background transition-transform duration-200 ease-in-out md:static md:z-auto md:translate-x-0 dark:border-zinc-800 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-zinc-200 p-4 md:hidden dark:border-zinc-800">
          <span className="text-sm font-semibold">Files</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close files sidebar"
            className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            <XIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="p-4">
          <FileUpload onUploaded={handleUploaded} />
        </div>
        <FileList pendingFileIds={pendingFileIds} onPendingResolved={handlePendingResolved} />
      </aside>
    </>
  );
}
