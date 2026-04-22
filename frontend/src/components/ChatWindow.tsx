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
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
        {messages.length === 0 && !isStreaming && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center max-w-sm">
              <div className="w-12 h-12 rounded-2xl bg-accent/10 flex items-center justify-center mx-auto mb-5">
                <Send className="w-5 h-5 text-accent-light" />
              </div>
              <h2 className="text-lg font-medium text-base-300 tracking-tight mb-2">Start a conversation</h2>
              <p className="text-sm text-base-500 leading-relaxed">
                {selectedSkillId
                  ? "Type a message below to begin."
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
          <div className="flex justify-start animate-fade-in-up">
            <div className="bg-base-800/80 rounded-xl px-4 py-3 max-w-[80%] text-base-100 whitespace-pre-wrap text-sm leading-relaxed">
              {streamingContent}
              <span className="inline-block w-1.5 h-4 bg-accent-light rounded-sm animate-soft-pulse ml-1 align-middle" />
            </div>
          </div>
        )}

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

      {/* Input area */}
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
            onClick={handleSend}
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
    </div>
  );
}
