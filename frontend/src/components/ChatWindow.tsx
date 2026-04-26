import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { Send, Loader2, Sparkles, Bot, Paperclip, X as XIcon } from "lucide-react";
import { useAppStore } from "../store/useAppStore";
import { sendChatMessage, resolveApproval, fetchGreeting } from "../api/chat";
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

export function ChatWindow() {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea to fit content
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, []);

  const {
    conversationId,
    selectedSkillId,
    messages,
    streamingContent,
    isStreaming,
    pendingApproval,
    error,
    toolCalls,
    streamingSegments,
    pendingAttachments,
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
    clearToolCalls,
    addPendingAttachment,
    removePendingAttachment,
    clearPendingAttachments,
  } = useAppStore();

  // Pre-build a map from tool_call_id → result content
  const toolResultMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) {
      if (m.role === "tool" && m.tool_call_id && m.content) {
        map.set(m.tool_call_id, m.content);
      }
    }
    return map;
  }, [messages]);

  const scrollToBottom = useCallback(() => {
    // Use requestAnimationFrame to ensure DOM has updated before scrolling
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingContent, streamingSegments, toolCalls, pendingApproval, scrollToBottom]);

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
              .then((conv) => {
                setMessages(conv.messages);
                clearToolCalls();
              })
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
      clearToolCalls,
      streamingContent,
    ]
  );

  const handleSend = async () => {
    // Read pendingAttachments fresh from store to avoid stale closure
    const currentAttachments = useAppStore.getState().pendingAttachments;
    const hasText = input.trim().length > 0;
    const hasFiles = currentAttachments.length > 0;
    if ((!hasText && !hasFiles) || isStreaming) return;
    if (!conversationId && !selectedSkillId) {
      setError("Please select a skill to start a new conversation");
      return;
    }

    const message = input.trim() || (hasFiles ? "[Attached image(s)]" : "");
    const files = [...currentAttachments];
    console.log(`[Nexus] Sending: text="${message.slice(0, 50)}", files=${files.length}`, files.map(f => `${f.name}(${f.size})`));
    setInput("");
    // Reset textarea height after clearing
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    setError(null);
    setStreamingContent("");
    clearToolCalls();
    clearPendingAttachments();
    setIsStreaming(true);

    // Optimistically add user message (with local preview URLs for attachments)
    const tempMsg: Message = {
      id: Date.now(),
      role: "user",
      content: message,
      created_at: new Date().toISOString(),
      attachments_json: files.length > 0
        ? JSON.stringify(files.map((f) => ({
            filename: f.name,
            original_name: f.name,
            content_type: f.type,
            url: URL.createObjectURL(f),
          })))
        : null,
    };
    addMessage(tempMsg);

    abortRef.current = new AbortController();

    try {
      await sendChatMessage(
        {
          conversation_id: conversationId,
          skill_id: conversationId ? undefined : selectedSkillId,
          message,
          files: files.length > 0 ? files : undefined,
        },
        handleSSEEvent,
        abortRef.current.signal
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError((err as Error).message);
        // Restore attachments so the user doesn't lose them on error
        if (files.length > 0) {
          for (const f of files) addPendingAttachment(f);
        }
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

  // Global paste listener — catches pastes anywhere on the page
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;

      let added = false;
      for (const item of Array.from(items)) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file && file.size > 0) {
            console.log(`[Nexus] Pasted image: ${file.name} (${file.type}, ${file.size} bytes)`);
            addPendingAttachment(file);
            added = true;
          }
        }
      }
      if (added) {
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [addPendingAttachment]);

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      for (const file of Array.from(files)) {
        if (file.type.startsWith("image/")) {
          addPendingAttachment(file);
        }
      }
      // Reset input so the same file can be selected again
      e.target.value = "";
    },
    [addPendingAttachment]
  );

  const canSend = !isStreaming && !pendingApproval && (input.trim().length > 0 || pendingAttachments.length > 0);

  const [greeting, setGreeting] = useState("");

  // Read from the module-level promise (already in-flight)
  useEffect(() => {
    let cancelled = false;
    greetingPromise.then((g) => {
      if (!cancelled) setGreeting(g);
    });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className={`max-w-4xl mx-auto space-y-5 ${messages.length === 0 && !isStreaming ? "h-full" : ""}`}>
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
                      onClick={() => {
                        setInput(suggestion);
                      }}
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
            onToggleToolCall={toggleToolCallExpanded}
          />
        ))}

        {/* Streaming assistant content + live tool calls (interleaved) */}
        {isStreaming && streamingSegments.length > 0 && (
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
              {/* Show cursor when streaming hasn't produced text yet after last tool call */}
              {streamingSegments[streamingSegments.length - 1]?.type === "tool_call" && isStreaming && (
                <div className="bg-base-800/80 rounded-xl px-4 py-3">
                  <span className="inline-block w-1.5 h-4 bg-accent-light rounded-sm animate-soft-pulse" />
                </div>
              )}
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
      </div>

      {/* Input area */}
      <div className="border-t border-base-800/80 px-6 py-4">
        <div className="max-w-4xl mx-auto">
          {/* Attachment previews */}
          {pendingAttachments.length > 0 && (
            <div className="flex gap-2 mb-2 flex-wrap">
              {pendingAttachments.map((file, i) => (
                <div key={`${file.name}-${i}`} className="relative group">
                  <img
                    src={URL.createObjectURL(file)}
                    alt={file.name}
                    className="w-16 h-16 object-cover rounded-lg border border-base-700/60"
                  />
                  <button
                    onClick={() => removePendingAttachment(i)}
                    className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-base-900 border border-base-700 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <XIcon className="w-3 h-3 text-base-400" />
                  </button>
                  <div className="absolute bottom-0 left-0 right-0 bg-black/60 rounded-b-lg px-1 py-0.5">
                    <span className="text-[10px] text-base-300 truncate block">{file.name}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="flex gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              onChange={handleFileSelect}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isStreaming || !!pendingApproval}
              className="self-end bg-base-800/60 border border-base-700/60 rounded-xl px-3 py-3 text-base-400 hover:text-base-200 hover:bg-base-800 disabled:opacity-40 transition-colors duration-150"
              title="Attach image (or paste from clipboard)"
            >
              <Paperclip className="w-5 h-5" />
            </button>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                autoResize();
              }}
              onKeyDown={handleKeyDown}
              placeholder={
                pendingApproval
                  ? "Waiting for approval decision..."
                  : pendingAttachments.length > 0
                  ? "Add a message about the image(s)..."
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
              aria-label="Send message"
              className="self-end bg-accent hover:bg-accent-hover disabled:bg-base-800 disabled:text-base-600 disabled:cursor-not-allowed text-white rounded-xl px-4 py-3 transition-[background-color,transform] duration-150"
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
    </div>
  );
}
