export default function TypingIndicator() {
  return (
    <div className="flex w-fit items-center gap-1 rounded-lg bg-zinc-100 px-3 py-2.5 dark:bg-zinc-800">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.3s] dark:bg-zinc-500" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:-0.15s] dark:bg-zinc-500" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 dark:bg-zinc-500" />
    </div>
  );
}
