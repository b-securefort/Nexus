import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Loader2 } from "lucide-react";
import { useAppStore } from "../store/useAppStore";
import { sendChatMessage, resumeChat, resolveApproval } from "../api/chat";
import { fetchConversation } from "../api/conversations";
import { MessageBubble } from "./MessageBubble";
import { ApprovalCard } from "./ApprovalCard";
import type { Message, ApprovalInfo } from "../types";

export function ChatWindow() {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const {
    conversationId,
    selectedSkillId,
    messages,
    streamingContent,
    isStreaming,
    pendingApproval,
    error,
    toolCalls,
    setConversationId,
    setMessages,
    addMessage,
    setStreamingContent,
    appendStreamingContent,
    setIsStreaming,
    setPendingApproval,
    setError,
    addToolCall,
    setToolCallExecuting,
    appendToolCallOutput,
    setToolCallResult,
    toggleToolCallExpanded,
  } = useAppStore();

  const scrollToBottom = useCallback(() => {
    // Use requestAnimationFrame to ensure DOM has updated before scrolling
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingContent, toolCalls, pendingApproval, scrollToBottom]);

  // Load conversation messages when conversationId changes
  useEffect(() => {
    if (conversationId) {
      fetchConversation(conversationId)
        .then((conv) => setMessages(conv.messages))
        .catch(() => setError("Failed to load conversation"));
    }
  }, [conversationId, setMessages, setError]);

  const handleSSEEvent = useCallback(
    (eventType: string, data: unknown) => {
      const d = data as Record<string, unknown>;

      switch (eventType) {
        case "token":
          appendStreamingContent(d.text as string);
          break;

        case "tool_call_start":
          addToolCall({
            call_id: d.call_id as string,
            name: d.name as string,
            args: d.args as Record<string, unknown>,
          });
          break;

        case "tool_executing":
          setToolCallExecuting(d.call_id as string);
          break;

        case "tool_output_chunk":
          appendToolCallOutput(d.call_id as string, d.chunk as string);
          break;

        case "approval_required":
          setPendingApproval(d as unknown as ApprovalInfo);
          break;

        case "tool_result":
          setToolCallResult(d.call_id as string, d.content as string);
          break;

        case "message_saved": {
          const role = d.role as string;
          if (role === "assistant" && streamingContent) {
            // Flush streaming content as a real message
          }
          break;
        }

        case "done":
          setIsStreaming(false);
          setStreamingContent("");
          // Refresh messages from server to get final state
          if (d.conversation_id) {
            setConversationId(d.conversation_id as number);
            fetchConversation(d.conversation_id as number)
              .then((conv) => setMessages(conv.messages))
              .catch(() => {});
          }
          break;

        case "error":
          setError(d.message as string);
          setIsStreaming(false);
          break;
      }
    },
    [
      appendStreamingContent,
      addToolCall,
      setToolCallExecuting,
      appendToolCallOutput,
      setPendingApproval,
      setToolCallResult,
      setIsStreaming,
      setStreamingContent,
      setConversationId,
      setMessages,
      setError,
      streamingContent,
    ]
  );

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    if (!conversationId && !selectedSkillId) {
      setError("Please select a skill to start a new conversation");
      return;
    }

    const message = input.trim();
    setInput("");
    setError(null);
    setStreamingContent("");
    setIsStreaming(true);

    // Optimistically add user message
    const tempMsg: Message = {
      id: Date.now(),
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
    };
    addMessage(tempMsg);

    abortRef.current = new AbortController();

    try {
      await sendChatMessage(
        {
          conversation_id: conversationId,
          skill_id: conversationId ? undefined : selectedSkillId,
          message,
        },
        handleSSEEvent,
        abortRef.current.signal
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message);
      }
      setIsStreaming(false);
    }
  };

  const handleApproval = async (action: "approve" | "deny") => {
    if (!pendingApproval) return;
    try {
      await resolveApproval(pendingApproval.approval_id, action);
      setPendingApproval(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const canSend = !isStreaming && !pendingApproval && input.trim().length > 0;

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && !isStreaming && (
          <div className="flex items-center justify-center h-full text-zinc-500">
            <div className="text-center">
              <h2 className="text-xl font-semibold mb-2">Start a conversation</h2>
              <p className="text-sm">
                {selectedSkillId
                  ? "Type a message to begin."
                  : "Select a skill from the dropdown above, then type a message."}
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            toolCalls={toolCalls}
            allMessages={messages}
            onToggleToolCall={toggleToolCallExpanded}
          />
        ))}

        {/* Streaming assistant content */}
        {streamingContent && (
          <div className="flex justify-start">
            <div className="bg-zinc-800 rounded-lg px-4 py-3 max-w-[80%] text-zinc-100 whitespace-pre-wrap">
              {streamingContent}
              <span className="inline-block w-2 h-4 bg-blue-400 animate-pulse ml-1" />
            </div>
          </div>
        )}

        {/* Pending approval */}
        {pendingApproval && (
          <ApprovalCard approval={pendingApproval} onAction={handleApproval} />
        )}

        {/* Error */}
        {error && (
          <div className="bg-red-900/30 border border-red-700 rounded-lg px-4 py-3 text-red-300">
            {error}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-zinc-700 p-4">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              pendingApproval
                ? "Waiting for approval decision..."
                : "Type your message... (Shift+Enter for newline)"
            }
            disabled={isStreaming || !!pendingApproval}
            rows={1}
            className="flex-1 bg-zinc-800 border border-zinc-600 rounded-lg px-4 py-3 text-zinc-100 placeholder-zinc-500 resize-none focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            style={{ minHeight: "48px", maxHeight: "200px" }}
          />
          <button
            onClick={handleSend}
            disabled={!canSend}
            className="bg-blue-600 hover:bg-blue-500 disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg px-4 py-3 transition-colors"
          >
            {isStreaming ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
