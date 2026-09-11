"use client";

import { useEffect, useRef, useState } from "react";

import { queryApi } from "@/lib/api";
import { extractCitationOrder } from "@/lib/citations";
import type { ChatMessage } from "@/lib/types";

import MessageText from "./MessageText";
import SourcesAccordion from "./SourcesAccordion";
import TypingIndicator from "./TypingIndicator";

export default function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;

    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", text }]);
    setInput("");
    setSending(true);

    try {
      const response = await queryApi(text);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response.answer,
          results: response.results,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: err instanceof Error ? `Error: ${err.message}` : "Query failed",
        },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
        {messages.map((message) => {
          const citationOrder =
            message.role === "assistant" ? extractCitationOrder(message.text, message.results) : [];
          return (
            <div
              key={message.id}
              className={`flex flex-col gap-1 ${message.role === "user" ? "items-end" : "items-start"}`}
            >
              <div
                className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                  message.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                }`}
              >
                {message.role === "assistant" ? (
                  <MessageText text={message.text} citationOrder={citationOrder} />
                ) : (
                  message.text
                )}
              </div>
              {message.results && message.results.length > 0 && (
                <SourcesAccordion results={message.results} citationOrder={citationOrder} />
              )}
            </div>
          );
        })}
        {sending && (
          <div className="flex flex-col items-start gap-1">
            <TypingIndicator />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about your uploaded documents…"
          className="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          disabled={sending}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}
