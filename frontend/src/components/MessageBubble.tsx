import { ChevronDown, ChevronRight, User, Bot, Wrench, CheckCircle, XCircle, Loader2, Play } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";

interface ToolCallDisplay {
  call_id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  executing?: boolean;
  streamingOutput?: string;
  expanded: boolean;
}

interface Props {
  message: Message;
  toolCalls: ToolCallDisplay[];
  allMessages: Message[];
  onToggleToolCall: (call_id: string) => void;
}

/** Format tool args into a human-readable command string. */
function formatCommand(toolName: string, args: Record<string, unknown>): string {
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
  // Fallback
  const filtered = Object.entries(args).filter(([k]) => k !== "reason");
  if (filtered.length === 0) return "";
  return filtered
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
}

/** Check if a tool result indicates an error. */
function isErrorResult(result: string | undefined): boolean {
  if (!result) return false;
  return (
    result.startsWith("Error") ||
    result.startsWith("error") ||
    result.includes("Exit code: 1") ||
    result.includes("Exit code: 2")
  );
}

/** Truncate long results for inline preview. */
function getResultPreview(result: string): string {
  const firstLine = result.split("\n").find((l) => l.trim()) || result;
  return firstLine.length > 120 ? firstLine.slice(0, 120) + "…" : firstLine;
}

export function MessageBubble({ message, toolCalls, allMessages, onToggleToolCall }: Props) {
  if (message.role === "tool") {
    // Tool messages are shown as part of tool call cards
    return null;
  }

  const isUser = message.role === "user";

  // Find tool calls for this assistant message
  let messageTCs: ToolCallDisplay[] = [];
  if (message.role === "assistant" && message.tool_calls_json) {
    try {
      const calls = JSON.parse(message.tool_calls_json) as Array<{
        id: string;
        function: { name: string; arguments: string };
      }>;
      messageTCs = calls.map((c) => {
        // First check live streaming state
        const existing = toolCalls.find((tc) => tc.call_id === c.id);
        if (existing) return existing;

        // Fall back to historical tool-role messages
        const historyMsg = allMessages.find(
          (m) => m.role === "tool" && m.tool_call_id === c.id
        );
        return {
          call_id: c.id,
          name: c.function.name,
          args: JSON.parse(c.function.arguments || "{}"),
          result: historyMsg?.content,
          expanded: false,
        };
      });
    } catch {
      // Ignore parse errors
    }
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} gap-2`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center flex-shrink-0 mt-1">
          <Bot className="w-4 h-4 text-white" />
        </div>
      )}

      <div className={`max-w-[80%] space-y-2`}>
        {/* Message content */}
        {message.content && (
          <div
            className={`rounded-lg px-4 py-3 ${
              isUser
                ? "bg-blue-600 text-white whitespace-pre-wrap"
                : "bg-zinc-800 text-zinc-100 prose prose-invert prose-sm max-w-none"
            }`}
          >
            {isUser ? (
              message.content
            ) : (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            )}
          </div>
        )}

        {/* Tool call cards */}
        {messageTCs.map((tc) => {
          const cmd = formatCommand(tc.name, tc.args);
          const hasError = isErrorResult(tc.result);
          const isDone = !!tc.result;
          const isExecuting = !!tc.executing && !isDone;
          const hasStreamingOutput = !!tc.streamingOutput;
          // Auto-expand on error, or when executing with output
          const showExpanded = tc.expanded || (hasError && isDone) || (isExecuting && hasStreamingOutput);

          return (
            <div
              key={tc.call_id}
              className={`bg-zinc-800/50 border rounded-lg overflow-hidden ${
                hasError
                  ? "border-red-700/60"
                  : isDone
                  ? "border-zinc-700/60"
                  : isExecuting
                  ? "border-yellow-700/40"
                  : "border-blue-700/40"
              }`}
            >
              <button
                onClick={() => onToggleToolCall(tc.call_id)}
                className="w-full flex items-center gap-2 px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-700/50 transition-colors"
              >
                {/* Status icon */}
                {isDone ? (
                  hasError ? (
                    <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
                  ) : (
                    <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
                  )
                ) : isExecuting ? (
                  <Play className="w-3.5 h-3.5 text-yellow-400 animate-pulse flex-shrink-0" />
                ) : (
                  <Loader2 className="w-3.5 h-3.5 text-blue-400 animate-spin flex-shrink-0" />
                )}

                {/* Tool name */}
                <span className="font-mono text-xs text-zinc-400">{tc.name}</span>

                {/* Command preview */}
                {cmd && (
                  <span className="text-xs text-zinc-300 truncate max-w-[60%]" title={cmd}>
                    {cmd.length > 80 ? cmd.slice(0, 80) + "…" : cmd}
                  </span>
                )}

                <span className="ml-auto flex items-center gap-1">
                  {/* Status badge */}
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
                <div className="border-t border-zinc-700 px-3 py-2 text-xs space-y-2">
                  {/* Command */}
                  {cmd && (
                    <div>
                      <div className="text-zinc-500 mb-1">Command:</div>
                      <pre className="bg-zinc-900 rounded p-2 overflow-x-auto text-zinc-200 font-mono text-sm whitespace-pre-wrap">
                        {cmd}
                      </pre>
                    </div>
                  )}

                  {/* Reason (for approval tools) */}
                  {tc.args.reason && (
                    <div className="text-zinc-400">
                      <span className="text-zinc-500">Reason: </span>
                      {String(tc.args.reason)}
                    </div>
                  )}

                  {/* Streaming output (live) */}
                  {isExecuting && tc.streamingOutput && (
                    <div>
                      <div className="text-yellow-400 mb-1 flex items-center gap-1">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Live output:
                      </div>
                      <pre className="bg-zinc-900 rounded p-2 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap text-zinc-300 font-mono text-[11px]">
                        {tc.streamingOutput}
                        <span className="inline-block w-1.5 h-3 bg-yellow-400 animate-pulse ml-0.5" />
                      </pre>
                    </div>
                  )}

                  {/* Final result */}
                  {tc.result && (
                    <div>
                      <div className={`mb-1 ${hasError ? "text-red-400" : "text-zinc-500"}`}>
                        {hasError ? "Error:" : "Output:"}
                      </div>
                      <pre
                        className={`bg-zinc-900 rounded p-2 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap ${
                          hasError ? "text-red-300" : "text-zinc-300"
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
        })}
      </div>

      {isUser && (
        <div className="w-8 h-8 rounded-full bg-zinc-600 flex items-center justify-center flex-shrink-0 mt-1">
          <User className="w-4 h-4 text-white" />
        </div>
      )}
    </div>
  );
}
