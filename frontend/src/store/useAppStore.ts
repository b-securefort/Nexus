import { create } from "zustand";
import type { Message, ApprovalInfo, ConversationSummary } from "../types";

export type StreamingSegment =
  | { type: "text"; content: string }
  | { type: "tool_call"; call_id: string };

interface AppState {
  // Current conversation
  conversationId: number | null;
  selectedSkillId: string | null;
  messages: Message[];
  streamingContent: string;
  isStreaming: boolean;
  pendingApproval: ApprovalInfo | null;
  error: string | null;

  // Pending file attachments for next message
  pendingAttachments: File[];

  // Tool calls
  toolCalls: Array<{
    call_id: string;
    name: string;
    args: Record<string, unknown>;
    result?: string;
    executing?: boolean;
    streamingOutput?: string;
    expanded: boolean;
  }>;

  // Ordered streaming segments (text interleaved with tool calls)
  streamingSegments: StreamingSegment[];

  // Conversations list
  conversations: ConversationSummary[];

  // Sidebar
  sidebarOpen: boolean;
  searchQuery: string;

  // Actions
  setConversationId: (id: number | null) => void;
  setSelectedSkillId: (id: string | null) => void;
  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  setStreamingContent: (content: string) => void;
  appendStreamingContent: (chunk: string) => void;
  setIsStreaming: (streaming: boolean) => void;
  setPendingApproval: (approval: ApprovalInfo | null) => void;
  setError: (error: string | null) => void;
  addToolCall: (call: { call_id: string; name: string; args: Record<string, unknown> }) => void;
  setToolCallExecuting: (call_id: string) => void;
  appendToolCallOutput: (call_id: string, chunk: string) => void;
  setToolCallResult: (call_id: string, result: string) => void;
  toggleToolCallExpanded: (call_id: string) => void;
  clearToolCalls: () => void;
  setConversations: (conversations: ConversationSummary[]) => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSearchQuery: (query: string) => void;
  addPendingAttachment: (file: File) => void;
  removePendingAttachment: (index: number) => void;
  clearPendingAttachments: () => void;
  resetChat: () => void;
}

export const useAppStore = create<AppState>((set) => ({
  conversationId: null,
  selectedSkillId: null,
  messages: [],
  streamingContent: "",
  isStreaming: false,
  pendingApproval: null,
  error: null,
  pendingAttachments: [],
  toolCalls: [],
  streamingSegments: [],
  conversations: [],
  sidebarOpen: true,
  searchQuery: "",

  setConversationId: (id) => set({ conversationId: id }),
  setSelectedSkillId: (id) => set({ selectedSkillId: id }),
  setMessages: (messages) => set({ messages }),
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  setStreamingContent: (content) => set({ streamingContent: content }),
  appendStreamingContent: (chunk) =>
    set((state) => {
      const segments = [...state.streamingSegments];
      const last = segments[segments.length - 1];
      if (last && last.type === "text") {
        segments[segments.length - 1] = { type: "text", content: last.content + chunk };
      } else {
        segments.push({ type: "text", content: chunk });
      }
      return {
        streamingContent: state.streamingContent + chunk,
        streamingSegments: segments,
      };
    }),
  setIsStreaming: (streaming) => set({ isStreaming: streaming }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  setError: (error) => set({ error }),
  addToolCall: (call) =>
    set((state) => ({
      toolCalls: [...state.toolCalls, { ...call, expanded: false }],
      streamingSegments: [...state.streamingSegments, { type: "tool_call" as const, call_id: call.call_id }],
    })),
  setToolCallExecuting: (call_id) =>
    set((state) => ({
      toolCalls: state.toolCalls.map((tc) =>
        tc.call_id === call_id ? { ...tc, executing: true, expanded: true } : tc
      ),
    })),
  appendToolCallOutput: (call_id, chunk) =>
    set((state) => ({
      toolCalls: state.toolCalls.map((tc) =>
        tc.call_id === call_id
          ? { ...tc, streamingOutput: (tc.streamingOutput || "") + chunk }
          : tc
      ),
    })),
  setToolCallResult: (call_id, result) =>
    set((state) => ({
      toolCalls: state.toolCalls.map((tc) =>
        tc.call_id === call_id ? { ...tc, result } : tc
      ),
    })),
  toggleToolCallExpanded: (call_id) =>
    set((state) => ({
      toolCalls: state.toolCalls.map((tc) =>
        tc.call_id === call_id ? { ...tc, expanded: !tc.expanded } : tc
      ),
    })),
  clearToolCalls: () => set({ toolCalls: [], streamingSegments: [] }),
  setConversations: (conversations) => set({ conversations }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setSearchQuery: (query) => set({ searchQuery: query }),
  addPendingAttachment: (file) =>
    set((state) => ({ pendingAttachments: [...state.pendingAttachments, file] })),
  removePendingAttachment: (index) =>
    set((state) => ({
      pendingAttachments: state.pendingAttachments.filter((_, i) => i !== index),
    })),
  clearPendingAttachments: () => set({ pendingAttachments: [] }),
  resetChat: () =>
    set({
      conversationId: null,
      messages: [],
      streamingContent: "",
      isStreaming: false,
      pendingApproval: null,
      error: null,
      pendingAttachments: [],
      toolCalls: [],
      streamingSegments: [],
    }),
}));
