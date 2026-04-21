import { create } from "zustand";
import type { Message, ApprovalInfo, ConversationSummary } from "../types";

interface AppState {
  // Current conversation
  conversationId: number | null;
  selectedSkillId: string | null;
  messages: Message[];
  streamingContent: string;
  isStreaming: boolean;
  pendingApproval: ApprovalInfo | null;
  error: string | null;

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

  // Conversations list
  conversations: ConversationSummary[];

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
  setConversations: (conversations: ConversationSummary[]) => void;
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
  toolCalls: [],
  conversations: [],

  setConversationId: (id) => set({ conversationId: id }),
  setSelectedSkillId: (id) => set({ selectedSkillId: id }),
  setMessages: (messages) => set({ messages }),
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  setStreamingContent: (content) => set({ streamingContent: content }),
  appendStreamingContent: (chunk) =>
    set((state) => ({ streamingContent: state.streamingContent + chunk })),
  setIsStreaming: (streaming) => set({ isStreaming: streaming }),
  setPendingApproval: (approval) => set({ pendingApproval: approval }),
  setError: (error) => set({ error }),
  addToolCall: (call) =>
    set((state) => ({
      toolCalls: [...state.toolCalls, { ...call, expanded: false }],
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
  setConversations: (conversations) => set({ conversations }),
  resetChat: () =>
    set({
      conversationId: null,
      messages: [],
      streamingContent: "",
      isStreaming: false,
      pendingApproval: null,
      error: null,
      toolCalls: [],
    }),
}));
