import { useState, useRef, useEffect, useCallback, useMemo, memo } from "react";
import { Send, Loader2, Sparkles, Bot } from "lucide-react";
import { useAppStore } from "../store/useAppStore";
import { sendChatMessage, resumeChat, resolveApproval, fetchGreeting } from "../api/chat";
import { fetchConversation } from "../api/conversations";
import { MessageBubble } from "./MessageBubble";
import { ApprovalCard } from "./ApprovalCard";
import { ToolCallCard } from "./ToolCallCard";
import type { Message, ApprovalInfo } from "../types";

/** Simple time-of-day fallback while the AI greeting loads. */
function getFallbackGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Hey there, night owl";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

// Fire greeting fetch at module load time (parallel with page render)
const greetingPromise = fetchGreeting()
  .then((g) => g || getFallbackGreeting())
  .catch(() => getFallbackGreeting());

// --- Stable action references (zustand actions never change identity) ---
const actionsSelector = (s: ReturnType<typeof useAppStore.getState>) => ({
  setConversationId: s.setConversationId,
  setMessages: s.setMessages,
  addMessage: s.addMessage,
  setStreamingContent: s.setStreamingContent,
  appendStreamingContent: s.appendStreamingContent,
  setIsStreaming: s.setIsStreaming,
  setPendingApproval: s.setPendingApproval,
  setError: s.setError,
  addToolCall: s.addToolCall,
  setToolCallExecuting: s.setToolCallExecuting,
  appendToolCallOutput: s.appendToolCallOutput,
  setToolCallResult: s.setToolCallResult,
  toggleToolCallExpanded: s.toggleToolCallExpanded,
  clearToolCalls: s.clearToolCalls,
});

// Grab actions once at module level — they never change
let _actions: ReturnType<typeof actionsSelector> | null = null;
function getActions() {
  if (!_actions) _actions = actionsSelector(useAppStore.getState());
  return _actions;
}

/**
 * Build a map from tool_call_id → result content from tool messages.
 * Avoids passing the entire messages array to every MessageBubble.
 */
function useToolResultMap(messages: Message[]): Map<string, string> {
  return useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) {
      if (m.role === "tool" && m.tool_call_id && m.content) {
        map.set(m.tool_call_id, m.content);
      }
    }
    return map;
  }, [messages]);
}

/** Isolated input area — only re-renders for its own state + isStreaming/pendingApproval. */
const ChatInput = memo(function ChatInput({
  onSend,
  onSuggestion,
}: {
  onSend: (message: string) => void;
  onSuggestion?: (text: string) => void;
}) {
  const [input, setInput] = useState("");
  const isStreaming = useAppStore((s) => s.isStreaming);
  const pendingApproval = useAppStore((s) => s.pendingApproval);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!input.trim() || isStreaming || pendingApproval) return;
      onSend(input.trim());
      setInput("");
    }
  };

  const handleClick = () => {
    if (!input.trim() || isStreaming || pendingApproval) return;
    onSend(input.trim());
    setInput("");
  };

  // Allow parent to fill suggestions
  useEffect(() => {
    if (onSuggestion) {
      (onSuggestion as unknown as { _setter: typeof setInput })._setter = setInput;
    }
  }, [onSuggestion]);

  const canSend = !isStreaming && !pendingApproval && input.trim().length > 0;

  return (
    <div className="border-t border-base-800/80 px-6 py-4">
      <div className="flex gap-3 max-w-4xl mx-auto">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            pendingApproval
              ? "Waiting for approval decision..."
              : "Type your message..."
          }
          disabled={isStreaming || !!pendingApproval}
          rows={1}
          className="flex-1 bg-base-800/60 border border-base-700/60 rounded-xl px-4 py-3 text-base-100 placeholder-base-600 resize-none focus:outline-none focus:ring-1 focus:ring-accent/50 focus:border-accent/40 disabled:opacity-40 transition-[border-color,box-shadow] duration-150 text-sm leading-relaxed"
          style={{ minHeight: "44px", maxHeight: "200px" }}
        />
        <button
          onClick={handleClick}
          disabled={!canSend}
          className="bg-accent hover:bg-accent-hover disabled:bg-base-800 disabled:text-base-600 disabled:cursor-not-allowed text-white rounded-xl px-4 py-3 transition-[background-color,transform] duration-150"
        >
          {isStreaming ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </button>
      </div>
    </div>
  );
});

/** Streaming area — only subscribes to streaming-specific state. */
const StreamingArea = memo(function StreamingArea() {
  const isStreaming = useAppStore((s) => s.isStreaming);
  const streamingSegments = useAppStore((s) => s.streamingSegments);
  const toolCalls = useAppStore((s) => s.toolCalls);
  const toggleToolCallExpanded = useAppStore((s) => s.toggleToolCallExpanded);

  if (!isStreaming || streamingSegments.length === 0) return null;

  return (
    <div className="flex justify-start gap-3 animate-fade-in-up">
      <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center flex-shrink-0 mt-1">
        <Bot className="w-3.5 h-3.5 text-accent-light" />
      </div>
      <div className="max-w-[80%] space-y-2">
        {streamingSegments.map((seg, i) => {
          if (seg.type === "text") {
            const isLast = i === streamingSegments.length - 1;
            return (
              <div key={`seg-text-${i}`} className="bg-base-800/80 rounded-xl px-4 py-3 text-base-100 whitespace-pre-wrap text-sm leading-relaxed">
                {seg.content}
                {isLast && (
                  <span className="inline-block w-1.5 h-4 bg-accent-light rounded-sm animate-soft-pulse ml-1 align-middle" />
                )}
              </div>
            );
          }
          const tc = toolCalls.find((t) => t.call_id === seg.call_id);
          if (!tc) return null;
          return <ToolCallCard key={tc.call_id} tc={tc} onToggle={toggleToolCallExpanded} />;
        })}
        {streamingSegments[streamingSegments.length - 1]?.type === "tool_call" && isStreaming && (
          <div className="bg-base-800/80 rounded-xl px-4 py-3">
            <span className="inline-block w-1.5 h-4 bg-accent-light rounded-sm animate-soft-pulse" />
          </div>
        )}
      </div>
    </div>
  );
});

export function ChatWindow() {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const inputSetterRef = useRef<((text: string) => void) | null>(null);

  // Granular selectors — each only triggers re-render when its value changes
  const conversationId = useAppStore((s) => s.conversationId);
  const selectedSkillId = useAppStore((s) => s.selectedSkillId);
  const messages = useAppStore((s) => s.messages);
  const isStreaming = useAppStore((s) => s.isStreaming);
  const pendingApproval = useAppStore((s) => s.pendingApproval);
  const error = useAppStore((s) => s.error);
  const toolCalls = useAppStore((s) => s.toolCalls);

  const actions = getActions();

  const toolResultMap = useToolResultMap(messages);

  // --- Throttled scroll-to-bottom ---
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollToBottom = useCallback(() => {
    if (scrollTimerRef.current) return; // already scheduled
    scrollTimerRef.current = setTimeout(() => {
      scrollTimerRef.current = null;
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      });
    }, 120);
  }, []);

  // Scroll on messages change (discrete events)
  useEffect(() => {
    scrollToBottom();
  }, [messages, pendingApproval, scrollToBottom]);

  // Scroll on streaming via a separate subscription to avoid re-rendering this component
  useEffect(() => {
    let prevLen = 0;
    return useAppStore.subscribe((state) => {
      const newLen = state.streamingSegments.length + state.toolCalls.length;
      if (newLen !== prevLen) {
        prevLen = newLen;
        scrollToBottom();
      }
    });
  }, [scrollToBottom]);

  // Load conversation messages when conversationId changes
  useEffect(() => {
    if (conversationId) {
      fetchConversation(conversationId)
        .then((conv) => actions.setMessages(conv.messages))
        .catch(() => actions.setError("Failed to load conversation"));
    }
  }, [conversationId, actions]);

  // SSE handler — no state values in deps (uses store.getState inside), so never recreated during streaming
  const handleSSEEvent = useCallback(
    (eventType: string, data: unknown) => {
      const d = data as Record<string, unknown>;

      switch (eventType) {
        case "token":
          actions.appendStreamingContent(d.text as string);
          break;

        case "tool_call_start":
          actions.addToolCall({
            call_id: d.call_id as string,
            name: d.name as string,
            args: d.args as Record<string, unknown>,
          });
          break;

        case "tool_executing":
          actions.setToolCallExecuting(d.call_id as string);
          break;

        case "tool_output_chunk":
          actions.appendToolCallOutput(d.call_id as string, d.chunk as string);
          break;

        case "approval_required":
          actions.setPendingApproval(d as unknown as ApprovalInfo);
          break;

        case "tool_result":
          actions.setToolCallResult(d.call_id as string, d.content as string);
          break;

        case "message_saved":
          break;

        case "done":
          actions.setIsStreaming(false);
          actions.setStreamingContent("");
          if (d.conversation_id) {
            actions.setConversationId(d.conversation_id as number);
            fetchConversation(d.conversation_id as number)
              .then((conv) => {
                actions.setMessages(conv.messages);
                actions.clearToolCalls();
              })
              .catch(() => {});
          }
          break;

        case "error":
          actions.setError(d.message as string);
          actions.setIsStreaming(false);
          break;
      }
    },
    [actions]
  );

  const handleSend = useCallback(
    async (message: string) => {
      const state = useAppStore.getState();
      if (state.isStreaming) return;
      if (!state.conversationId && !state.selectedSkillId) {
        actions.setError("Please select a skill to start a new conversation");
        return;
      }

      actions.setError(null);
      actions.setStreamingContent("");
      actions.clearToolCalls();
      actions.setIsStreaming(true);

      const tempMsg: Message = {
        id: Date.now(),
        role: "user",
        content: message,
        created_at: new Date().toISOString(),
      };
      actions.addMessage(tempMsg);

      abortRef.current = new AbortController();

      try {
        await sendChatMessage(
          {
            conversation_id: state.conversationId,
            skill_id: state.conversationId ? undefined : state.selectedSkillId,
            message,
          },
          handleSSEEvent,
          abortRef.current.signal
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          actions.setError((err as Error).message);
        }
        actions.setIsStreaming(false);
      }
    },
    [handleSSEEvent, actions]
  );

  const handleApproval = useCallback(
    async (action: "approve" | "deny") => {
      const approval = useAppStore.getState().pendingApproval;
      if (!approval) return;
      try {
        await resolveApproval(approval.approval_id, action);
        actions.setPendingApproval(null);
      } catch (err) {
        actions.setError((err as Error).message);
      }
    },
    [actions]
  );

  const [greeting, setGreeting] = useState("");

  useEffect(() => {
    let cancelled = false;
    greetingPromise.then((g) => {
      if (!cancelled) setGreeting(g);
    });
    return () => { cancelled = true; };
  }, []);

  // Suggestion handler for empty state buttons
  const handleSuggestion = useCallback((text: string) => {
    inputSetterRef.current?.(text);
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {messages.length === 0 && !isStreaming && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center max-w-lg animate-fade-in-up">
              <div className="w-14 h-14 rounded-2xl bg-accent/10 flex items-center justify-center mx-auto mb-6">
                <Sparkles className="w-7 h-7 text-accent-light" />
              </div>
              {greeting ? (
                <h2 className="text-2xl font-semibold text-base-100 tracking-tight mb-2 animate-fade-in-up">{greeting}</h2>
              ) : (
                <div className="h-8 w-56 mx-auto mb-2 rounded-lg bg-base-800/60 animate-soft-pulse" />
              )}
              <p className="text-sm text-base-500 leading-relaxed mb-8">
                {selectedSkillId
                  ? "Ask me anything — I can search your knowledge base, run Azure commands, and more."
                  : "Select a skill from the dropdown above to get started."}
              </p>
              {selectedSkillId && (
                <div className="grid grid-cols-2 gap-2.5 max-w-md mx-auto">
                  {[
                    "Search the knowledge base",
                    "Check Azure resource status",
                    "Help me debug an issue",
                    "Explain our architecture",
                  ].map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => handleSend(suggestion)}
                      className="text-left px-3.5 py-2.5 bg-base-800/50 hover:bg-base-800 border border-base-700/40 rounded-xl text-sm text-base-300 hover:text-base-100 transition-colors duration-150"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            toolCalls={toolCalls}
            toolResultMap={toolResultMap}
            onToggleToolCall={actions.toggleToolCallExpanded}
          />
        ))}

        {/* Streaming assistant content + live tool calls (interleaved) */}
        <StreamingArea />

        {/* Pending approval */}
        {pendingApproval && (
          <div className="animate-fade-in-up">
            <ApprovalCard approval={pendingApproval} onAction={handleApproval} />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="bg-red-950/40 border border-red-800/40 rounded-xl px-4 py-3 text-red-300 text-sm animate-fade-in-up">
            {error}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area — isolated component, not affected by streaming re-renders */}
      <ChatInput onSend={handleSend} />
    </div>
  );
}
