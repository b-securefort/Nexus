import { useState, memo } from "react";
import { User, Bot } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import { ToolCallCard } from "./ToolCallCard";
import type { ToolCallDisplay } from "./ToolCallCard";

interface Props {
  message: Message;
  toolCalls: ToolCallDisplay[];
  toolResultMap: Map<string, string>;
  onToggleToolCall: (call_id: string) => void;
}

export const MessageBubble = memo(function MessageBubble({ message, toolCalls, toolResultMap, onToggleToolCall }: Props) {
  // Local expanded state for historical tool calls (not in the live store)
  const [localExpanded, setLocalExpanded] = useState<Record<string, boolean>>({});

  if (message.role === "tool") {
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
        // Use live streaming state if available
        const existing = toolCalls.find((tc) => tc.call_id === c.id);
        if (existing) return existing;

        // Historical: use pre-built map instead of scanning all messages
        const historyContent = toolResultMap.get(c.id);
        return {
          call_id: c.id,
          name: c.function.name,
          args: JSON.parse(c.function.arguments || "{}"),
          result: historyContent,
          expanded: !!localExpanded[c.id],
        };
      });
    } catch {
      // Ignore parse errors
    }
  }

  const handleToggle = (callId: string) => {
    // If it's a live streaming tool call, use store toggle
    const isLive = toolCalls.some((tc) => tc.call_id === callId);
    if (isLive) {
      onToggleToolCall(callId);
    } else {
      // Historical: toggle locally
      setLocalExpanded((prev) => ({ ...prev, [callId]: !prev[callId] }));
    }
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} gap-3 animate-fade-in-up`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center flex-shrink-0 mt-1">
          <Bot className="w-3.5 h-3.5 text-accent-light" />
        </div>
      )}

      <div className={`max-w-[80%] space-y-2`}>
        {message.content && (
          <div
            className={`rounded-xl px-4 py-3 ${
              isUser
                ? "bg-accent text-white text-sm leading-relaxed whitespace-pre-wrap"
                : "bg-base-800/80 text-base-100 prose prose-invert prose-sm prose-chat max-w-none"
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

        {messageTCs.map((tc) => (
          <ToolCallCard key={tc.call_id} tc={tc} onToggle={handleToggle} />
        ))}
      </div>

      {isUser && (
        <div className="w-7 h-7 rounded-lg bg-base-700/60 flex items-center justify-center flex-shrink-0 mt-1">
          <User className="w-3.5 h-3.5 text-base-400" />
        </div>
      )}
    </div>
  );
});
