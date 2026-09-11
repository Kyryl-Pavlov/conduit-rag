"use client";

import { useState } from "react";

import ChatWindow from "./ChatWindow";
import { MenuIcon } from "./icons";
import Sidebar from "./Sidebar";
import ThemeToggle from "./ThemeToggle";

export default function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex h-full w-full overflow-hidden">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-zinc-200 px-4 py-3 sm:px-6 sm:py-4 dark:border-zinc-800">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open files sidebar"
              className="-ml-1 shrink-0 rounded-md p-2 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 md:hidden dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
            >
              <MenuIcon className="h-5 w-5" />
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold sm:text-2xl">Conduit RAG</h1>
              <p className="hidden truncate text-sm text-zinc-500 sm:block">
                Upload a text file, then ask questions about it.
              </p>
            </div>
          </div>
          <ThemeToggle />
        </header>

        <div className="flex min-h-0 flex-1 flex-col p-3 sm:p-6">
          <ChatWindow />
        </div>
      </div>
    </div>
  );
}
