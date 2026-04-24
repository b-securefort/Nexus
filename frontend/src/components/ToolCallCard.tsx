import { ChevronDown, ChevronRight, CheckCircle, XCircle, Loader2, Play } from "lucide-react";

export interface ToolCallDisplay {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  executing?: boolean;
  streamingOutput?: string;
  expanded: boolean;
}

/** Format tool args into a human-readable command string. */
export function formatCommand(toolName: string, args: Record<string, unknown>): string {
  if (toolName === "az_cli" && Array.isArray(args.args)) {
    return `az ${(args.args as string[]).join(" ")}`;
  }
  if (toolName === "run_shell" && typeof args.command === "string") {
    return args.command;
  }
  if (toolName === "az_resource_graph" && typeof args.query === "string") {
    return args.query;
  }
  if (toolName === "search_kb" && typeof args.query === "string") {
    return `search: ${args.query}`;
  }
  if (toolName === "read_kb_file" && typeof args.path === "string") {
    return args.path;
  }
  if (toolName === "fetch_ms_docs" && typeof args.query === "string") {
    return `docs: ${args.query}`;
  }
  if (toolName === "update_learnings") {
    return `[${args.category || "learning"}] ${args.summary || ""}`;
  }
  if (toolName === "read_learnings") {
    return "read learn.md";
  }
  const filtered = Object.entries(args).filter(([k]) => k !== "reason");
  if (filtered.length === 0) return "";
  return filtered
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

/** Check if a tool result indicates an error. */
export function isErrorResult(result: string | undefined): boolean {
  if (!result) return false;
  return (
    result.startsWith("Error") ||
    result.startsWith("error") ||
    result.includes("Exit code: 1") ||
    result.includes("Exit code: 2")
  );
}

interface ToolCallCardProps {
  tc: ToolCallDisplay;
  onToggle: (call_id: string) => void;
}

export function ToolCallCard({ tc, onToggle }: ToolCallCardProps) {
  const cmd = formatCommand(tc.name, tc.args);
  const hasError = isErrorResult(tc.result);
  const isDone = !!tc.result;
  const isExecuting = !!tc.executing && !isDone;
  const hasStreamingOutput = !!tc.streamingOutput;
  const showExpanded = tc.expanded || (hasError && isDone) || (isExecuting && hasStreamingOutput);

  return (
    <div
      className={`bg-base-800/40 border rounded-xl overflow-hidden transition-[border-color] duration-200 ${
        hasError
          ? "border-red-800/50"
          : isDone
          ? "border-base-700/50"
          : isExecuting
          ? "border-yellow-700/30"
          : "border-accent/30"
      }`}
    >
      <button
        onClick={() => onToggle(tc.call_id)}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-base-300 hover:bg-base-700/50 transition-colors duration-100"
      >
        {isDone ? (
          hasError ? (
            <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
          ) : (
            <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
          )
        ) : isExecuting ? (
          <Play className="w-3.5 h-3.5 text-yellow-400 animate-pulse flex-shrink-0" />
        ) : (
          <Loader2 className="w-3.5 h-3.5 text-accent-light animate-spin flex-shrink-0" />
        )}

        <span className="font-mono text-xs text-base-400">{tc.name}</span>

        {cmd && (
          <span className="text-xs text-base-300 truncate max-w-[60%]" title={cmd}>
            {cmd.length > 80 ? cmd.slice(0, 80) + "…" : cmd}
          </span>
        )}

        <span className="ml-auto flex items-center gap-1">
          {isDone && hasError && (
            <span className="text-[10px] text-red-400 font-medium">FAILED</span>
          )}
          {isExecuting && (
            <span className="text-[10px] text-yellow-400 font-medium">EXECUTING</span>
          )}
          {showExpanded ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5" />
          )}
        </span>
      </button>

      {showExpanded && (
        <div className="border-t border-base-700/50 px-3 py-2.5 text-xs space-y-2.5">
          {cmd && (
            <div>
              <div className="text-base-500 mb-1">Command:</div>
              <pre className="bg-base-900/80 rounded-lg p-2.5 overflow-x-auto text-base-200 font-mono text-sm whitespace-pre-wrap">
                {cmd}
              </pre>
            </div>
          )}

          {tc.args.reason && (
            <div className="text-base-400">
              <span className="text-base-500">Reason: </span>
              {String(tc.args.reason)}
            </div>
          )}

          {isExecuting && tc.streamingOutput && (
            <div>
              <div className="text-yellow-400 mb-1 flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                Live output:
              </div>
              <pre className="bg-base-900/80 rounded-lg p-2.5 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap text-base-300 font-mono text-[11px]">
                {tc.streamingOutput}
                <span className="inline-block w-1.5 h-3 bg-yellow-400 rounded-sm animate-soft-pulse ml-0.5" />
              </pre>
            </div>
          )}

          {tc.result && (
            <div>
              <div className={`mb-1 ${hasError ? "text-red-400" : "text-base-500"}`}>
                {hasError ? "Error:" : "Output:"}
              </div>
              <pre
                className={`bg-base-900/80 rounded-lg p-2.5 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap ${
                  hasError ? "text-red-300" : "text-base-300"
                }`}
              >
                {tc.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
