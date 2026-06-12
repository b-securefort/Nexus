import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { ArrowUp, Square, Paperclip, X as XIcon } from "lucide-react";
import { useAppStore } from "../store/useAppStore";
import {
  sendChatMessage,
  resolveApproval,
  submitQuestionAnswers,
  refreshArmToken,
} from "../api/chat";
import type { QuestionAnswer } from "../api/chat";
import { fetchConversation } from "../api/conversations";
import { MessageBubble } from "./MessageBubble";
import { ApprovalCard } from "./ApprovalCard";
import { QuestionCard } from "./QuestionCard";
import { ToolCallCard } from "./ToolCallCard";
import { ContextUsageIndicator } from "./ContextUsageIndicator";
import type {
  Message,
  ApprovalInfo,
  ContextUsage,
  QuestionInfo,
  QuestionAnswerEntry,
  TokenRefreshRequired,
} from "../types";
import { msalInstance } from "../auth/AuthProvider";
import { ARM_SCOPE } from "../auth/msalConfig";
import { APP_ICON } from "../branding";
import { pickGreeting } from "../greetings";

const SUGGESTIONS = [
  "Search the knowledge base",
  "Check Azure resource status",
  "Help me debug an issue",
  "Explain our architecture",
];

export function ChatWindow() {
  const [input, setInput] = useState("");
  // Set when the backend ends a turn via `iteration_limit` (tool budget used
  // up, wrap-up summary persisted). Renders a "Continue" affordance instead
  // of the old dead-end error banner.
  const [iterationLimitHit, setIterationLimitHit] = useState(false);
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
    pendingQuestion,
    resolvedAnswers,
    error,
    toolCalls,
    streamingSegments,
    pendingAttachments,
    contextUsage,
    setConversationId,
    setMessages,
    addMessage,
    setStreamingContent,
    appendStreamingContent,
    setIsStreaming,
    setPendingApproval,
    setPendingQuestion,
    setQuestionAnswers,
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
    setContextUsage,
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

  // Load conversation messages when conversationId changes. Track the
  // previous id so we can distinguish "new conversation just got an id
  // assigned by the server" (null → X — keep usage) from "user navigated
  // between two persisted conversations" (X → Y — clear stale usage,
  // since token counts aren't persisted per-message).
  const prevConversationIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (conversationId) {
      const prev = prevConversationIdRef.current;
      if (prev !== null && prev !== conversationId) {
        setContextUsage(null);
      }
      prevConversationIdRef.current = conversationId;
      fetchConversation(conversationId)
        .then((conv) => setMessages(conv.messages))
        .catch(() => setError("Failed to load conversation"));
    } else {
      prevConversationIdRef.current = null;
    }
  }, [conversationId, setMessages, setError, setContextUsage]);

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

        case "question_required":
          setPendingQuestion(d as unknown as QuestionInfo);
          break;

        case "question_answered": {
          const qid = d.question_id as string;
          const answers = d.answers as QuestionAnswerEntry[];
          setQuestionAnswers(qid, answers);
          // The live card stays mounted in answered state until the model's
          // next message arrives (server will re-render the conversation
          // history with the natural-dialogue rendering).
          setPendingQuestion(null);
          break;
        }

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
          if (d.usage) {
            setContextUsage(d.usage as ContextUsage);
          }
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

        case "iteration_limit":
          // Not an error: tool results + a wrap-up summary are persisted.
          // Offer one-click resumption (a plain "continue" message).
          setIterationLimitHit(true);
          break;

        case "token_refresh_required": {
          const info = d as unknown as TokenRefreshRequired;
          // Silently acquire a fresh ARM token via MSAL and POST it back
          // so the in-flight orchestrator turn can resume Azure tool calls.
          if (import.meta.env.VITE_DEV_AUTH_BYPASS !== "true") {
            (async () => {
              try {
                const accounts = msalInstance.getAllAccounts();
                if (accounts.length === 0) return;
                const resp = await msalInstance.acquireTokenSilent({
                  scopes: [ARM_SCOPE],
                  account: accounts[0],
                });
                if (resp.accessToken) {
                  await refreshArmToken(
                    info.conversation_id,
                    resp.accessToken
                  );
                }
              } catch {
                // Silent refresh failed — user will need to retry manually
                console.warn("[Nexus] ARM token silent refresh failed");
              }
            })();
          }
          break;
        }
      }
    },
    [
      appendStreamingContent,
      addToolCall,
      setToolCallExecuting,
      appendToolCallOutput,
      setPendingApproval,
      setPendingQuestion,
      setQuestionAnswers,
      setToolCallResult,
      setIsStreaming,
      setStreamingContent,
      setConversationId,
      setMessages,
      setError,
      clearToolCalls,
      setContextUsage,
      streamingContent,
    ]
  );

  const handleSend = async () => {
    // Read pendingAttachments fresh from store to avoid stale closure
    const currentAttachments = useAppStore.getState().pendingAttachments;
    const hasText = input.trim().length > 0;
    const hasFiles = currentAttachments.length > 0;
    if ((!hasText && !hasFiles) || isStreaming) return;
    setIterationLimitHit(false);
    if (!conversationId && !selectedSkillId) {
      setError("Please select a skill to start a new conversation");
      return;
    }

    const message = input.trim() || (hasFiles ? "[Attached image(s)]" : "");
    const files = [...currentAttachments];
    console.log(`[NEXUS] Sending: text="${message.slice(0, 50)}", files=${files.length}`, files.map(f => `${f.name}(${f.size})`));
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

  // One-click resumption after an iteration_limit turn end. The turn's tool
  // results and wrap-up summary are already persisted, so a plain "continue"
  // user message resumes from that checkpoint with full history.
  const sendContinue = async () => {
    if (isStreaming || !conversationId) return;
    setIterationLimitHit(false);
    setError(null);
    setStreamingContent("");
    clearToolCalls();
    setIsStreaming(true);
    addMessage({
      id: Date.now(),
      role: "user",
      content: "continue",
      created_at: new Date().toISOString(),
      attachments_json: null,
    });
    abortRef.current = new AbortController();
    try {
      await sendChatMessage(
        { conversation_id: conversationId, message: "continue" },
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

  const handleStop = useCallback(() => {
    // Abort the in-flight SSE request. The backend's StreamingResponse is
    // cancelled on disconnect; its `finally` clears the lease + ARM override
    // (cleanup_interrupted_turn). No new tool calls run after this point; a
    // tool already executing finishes server-side but its result is discarded.
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
    setStreamingContent("");
    // A pending approval/question is moot once the turn is aborted — drop the
    // cards so they don't linger waiting on a stream that no longer exists.
    setPendingApproval(null);
    setPendingQuestion(null);
    // Reconcile the view with what the backend persisted before the stop, then
    // drop the live (now-aborted) tool-call cards — same order as the "done"
    // path so saved tool calls in history don't flash out and back in.
    const cid = useAppStore.getState().conversationId;
    if (cid) {
      fetchConversation(cid)
        .then((conv) => {
          setMessages(conv.messages);
          clearToolCalls();
        })
        .catch(() => clearToolCalls());
    } else {
      clearToolCalls();
    }
  }, [
    setIsStreaming, setStreamingContent, setMessages, clearToolCalls,
    setPendingApproval, setPendingQuestion,
  ]);

  const handleApproval = async (action: "approve" | "deny") => {
    if (!pendingApproval) return;
    try {
      await resolveApproval(pendingApproval.approval_id, action);
      setPendingApproval(null);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleAnswerQuestion = async (answers: QuestionAnswerEntry[]) => {
    if (!pendingQuestion) return;
    const qid = pendingQuestion.question_id;
    try {
      // Optimistically lock the card while the request is in flight; the
      // live card stays visible (in resolved state) until the server's
      // question_answered event arrives or the next assistant message
      // re-renders this conversation.
      setQuestionAnswers(qid, answers);
      const payload: QuestionAnswer[] = answers.map((a) => ({
        question: a.question,
        selected: a.selected,
        ...(a.notes ? { notes: a.notes } : {}),
      }));
      await submitQuestionAnswers(qid, payload);
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
            console.log(`[NEXUS] Pasted image: ${file.name} (${file.type}, ${file.size} bytes)`);
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

  const canSend =
    !isStreaming &&
    !pendingApproval &&
    !pendingQuestion &&
    (input.trim().length > 0 || pendingAttachments.length > 0);

  // Static time-of-day greeting, randomized once per session (see greetings.ts).
  const [greeting] = useState(() => pickGreeting());

  const composerDisabled = isStreaming || !!pendingApproval || !!pendingQuestion;

  // Single unified composer surface (textarea + toolbar), reused by both the
  // centered empty-state hero and the bottom-pinned conversation layout.
  const composer = (
    <div className="rounded-2xl border border-base-700/60 bg-base-800/60 focus-within:border-accent/40 focus-within:ring-1 focus-within:ring-accent/25 transition-[border-color,box-shadow] duration-150 ease-[var(--ease-out)]">
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        multiple
        onChange={handleFileSelect}
        className="hidden"
      />

      {/* Attachment previews */}
      {pendingAttachments.length > 0 && (
        <div className="flex gap-2 px-3.5 pt-3 flex-wrap">
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
                <span className="text-[10px] text-white/80 truncate block">{file.name}</span>
              </div>
            </div>
          ))}
        </div>
      )}

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
            : pendingQuestion
            ? "Answer the questions above to continue..."
            : pendingAttachments.length > 0
            ? "Add a message about the image(s)..."
            : "Message Nexus..."
        }
        disabled={composerDisabled}
        rows={1}
        autoFocus
        className="w-full bg-transparent px-4 pt-3.5 pb-1 text-[15px] text-base-100 placeholder-base-500 resize-none focus:outline-none disabled:opacity-40 leading-relaxed"
        style={{ minHeight: "48px", maxHeight: "200px" }}
      />

      {/* Toolbar */}
      <div className="flex items-center gap-1 px-2.5 pb-2.5">
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={composerDisabled}
          className="p-2 text-base-400 hover:text-base-200 hover:bg-base-700/50 rounded-lg disabled:opacity-40 transition-colors duration-150 ease-[var(--ease-out)]"
          title="Attach image (or paste from clipboard)"
        >
          <Paperclip className="w-[18px] h-[18px]" />
        </button>
        <div className="ml-auto flex items-center gap-3">
          <ContextUsageIndicator usage={contextUsage} />
          {isStreaming ? (
            <button
              onClick={handleStop}
              aria-label="Stop generation"
              title="Stop"
              className="w-9 h-9 rounded-full bg-danger-strong hover:brightness-110 text-white flex items-center justify-center transition-[filter,transform] duration-150 ease-[var(--ease-out)]"
            >
              <Square className="w-4 h-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!canSend}
              aria-label="Send message"
              className="w-9 h-9 rounded-full bg-accent hover:bg-accent-hover disabled:bg-base-700 disabled:text-base-500 disabled:cursor-not-allowed text-white flex items-center justify-center transition-[background-color,transform] duration-150 ease-[var(--ease-out)]"
            >
              <ArrowUp className="w-5 h-5" strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>
    </div>
  );

  // Fresh conversation → centered hero: greeting, composer and suggestions
  // read as one unit instead of a floating island over a bottom-pinned input.
  const showHero =
    messages.length === 0 &&
    !isStreaming &&
    streamingSegments.length === 0 &&
    !pendingApproval &&
    !pendingQuestion;

  if (showHero) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-8">
          <div className="w-full max-w-2xl">
            <div className="text-center mb-8">
              <div className="w-16 h-16 rounded-2xl bg-accent/10 flex items-center justify-center mx-auto mb-5">
                <img src={APP_ICON} alt="NEXUS" className="w-11 h-11 object-contain" />
              </div>
              <h2 className="text-3xl font-semibold text-base-100 tracking-tight mb-2 animate-fade-in-up">{greeting}</h2>
              <p className="text-sm text-base-500 leading-relaxed">
                {selectedSkillId
                  ? "Ask me anything — I can search your knowledge base, run Azure commands, and more."
                  : "Select a skill from the dropdown above to get started."}
              </p>
            </div>

            {error && (
              <div className="bg-danger/10 border border-danger/30 rounded-xl px-4 py-3 text-danger text-sm mb-4 animate-fade-in-up">
                {error}
              </div>
            )}

            {composer}

            {selectedSkillId && (
              <div className="grid grid-cols-2 gap-2.5 mt-4 stagger-children">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => {
                      setInput(suggestion);
                      textareaRef.current?.focus();
                    }}
                    className="text-left px-3.5 py-2.5 bg-base-800/40 hover:bg-base-800 border border-base-700/40 rounded-xl text-sm text-base-400 hover:text-base-200 transition-colors duration-150 ease-[var(--ease-out)]"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
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
          <div className="space-y-2.5 animate-fade-in-up">
            {streamingSegments.map((seg, i) => {
              if (seg.type === "text") {
                const isLast = i === streamingSegments.length - 1;
                return (
                  <div key={`seg-text-${i}`} className="text-[15px] leading-[1.7] text-base-200 whitespace-pre-wrap">
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
              <span className="inline-block w-1.5 h-4 bg-accent-light rounded-sm animate-soft-pulse" />
            )}
          </div>
        )}

        {/* Pending approval */}
        {pendingApproval && (
          <div className="animate-fade-in-up">
            <ApprovalCard approval={pendingApproval} onAction={handleApproval} />
          </div>
        )}

        {/* Pending or just-resolved question */}
        {pendingQuestion && (
          <div className="animate-fade-in-up">
            <QuestionCard
              question={pendingQuestion}
              resolved={resolvedAnswers[pendingQuestion.question_id]}
              onSubmit={handleAnswerQuestion}
            />
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="bg-danger/10 border border-danger/30 rounded-xl px-4 py-3 text-danger text-sm animate-fade-in-up">
            {error}
          </div>
        )}

        {/* Iteration budget used up — offer one-click resumption */}
        {iterationLimitHit && !isStreaming && (
          <div className="bg-warning/10 border border-warning/30 rounded-xl px-4 py-3 text-warning text-sm animate-fade-in-up flex items-center justify-between gap-3">
            <span>
              I used my full tool budget for this turn. Progress is saved —
              want me to keep going?
            </span>
            <button
              onClick={sendContinue}
              className="shrink-0 bg-accent hover:bg-accent-hover text-white rounded-lg px-3 py-1.5 text-sm font-medium transition-colors"
            >
              Continue
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Composer pinned at bottom */}
      <div className="px-6 pb-5 pt-2">
        <div className="max-w-3xl mx-auto">
          {composer}
        </div>
      </div>
    </div>
  );
}
