import { Bot, Plus } from "lucide-react";

export default function AgentsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">AI Agents</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Create and manage your AI sales conversion agents.
          </p>
        </div>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New Agent
        </button>
      </div>

      {/* Empty state — replaced with real data once API is wired */}
      <div className="flex flex-col items-center justify-center rounded-xl border-2 border-dashed border-neutral-200 py-16 dark:border-neutral-800">
        <Bot
          className="h-10 w-10 text-neutral-300 dark:text-neutral-700"
          aria-hidden="true"
        />
        <h3 className="mt-4 text-sm font-semibold text-neutral-900 dark:text-neutral-100">
          No agents yet
        </h3>
        <p className="mt-1 max-w-sm text-center text-sm text-neutral-500">
          Create your first AI agent to start converting high-intent visitors
          into qualified leads.
        </p>
      </div>
    </div>
  );
}
