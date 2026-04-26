import { useState, memo, useMemo } from "react";
import { User, Bot } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message, Attachment } from "../types";
import { ToolCallCard } from "./ToolCallCard";
import type { ToolCallDisplay } from "./ToolCallCard";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

function resolveAttachmentUrl(url: string): string {
  // blob: URLs (optimistic local previews) or absolute URLs pass through
  if (url.startsWith("blob:") || url.startsWith("http://") || url.startsWith("https://") || url.startsWith("data:")) {
    return url;
  }
  // Relative API paths like /api/uploads/xxx.png
  return `${API_BASE}${url}`;
}

interface Props {
  message: Message;
  toolCalls: ToolCallDisplay[];
  toolResultMap: Map<string, string>;
  onToggleToolCall: (call_id: string) => void;
}

export const MessageBubble = memo(function MessageBubble({ message, toolCalls, toolResultMap, onToggleToolCall }: Props) {
  // Local expanded state for historical tool calls (not in the live store)
  const [localExpanded, setLocalExpanded] = useState<Record<string, boolean>>({});
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);

  // Parse attachments once
  const attachments: Attachment[] = useMemo(() => {
    if (!message.attachments_json) return [];
    try {
      return JSON.parse(message.attachments_json);
    } catch {
      return [];
    }
  }, [message.attachments_json]);

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

        {/* Attachments (images) */}
        {attachments.length > 0 && (
          <div className={`flex flex-wrap gap-2 ${!message.content ? "" : ""}`}>
            {attachments.map((att, i) => (
              <button
                key={att.filename || i}
                onClick={() => setLightboxUrl(resolveAttachmentUrl(att.url))}
                className="block rounded-lg overflow-hidden border border-base-700/40 hover:border-accent/50 transition-colors duration-150"
              >
                <img
                  src={resolveAttachmentUrl(att.url)}
                  alt={att.original_name || att.filename}
                  className="max-w-[240px] max-h-[180px] object-contain bg-base-900"
                  loading="lazy"
                />
              </button>
            ))}
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

      {/* Lightbox */}
      {lightboxUrl && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center cursor-pointer"
          onClick={() => setLightboxUrl(null)}
        >
          <img
            src={lightboxUrl}
            alt="Preview"
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
});
