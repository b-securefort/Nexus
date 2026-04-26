export interface Skill {
  id: string;
  name: string;
  display_name: string;
  description: string;
  tools: string[];
  source: "shared" | "personal";
  system_prompt?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  requires_approval: boolean;
}

export interface ConversationSummary {
  id: number;
  title: string;
  skill_id: string;
  created_at: string;
  updated_at: string;
}

export interface Attachment {
  filename: string;
  original_name: string;
  content_type: string;
  url: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls_json?: string | null;
  tool_call_id?: string | null;
  tool_name?: string | null;
  attachments_json?: string | null;
  created_at: string;
}

export interface ConversationDetail {
  id: number;
  title: string;
  skill_id: string;
  skill_snapshot_json: string;
  created_at: string;
  updated_at: string;
  messages: Message[];
}

export interface ApprovalInfo {
  approval_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  reason: string;
}

// SSE event types
export type SSEEvent =
  | { type: "token"; data: { text: string } }
  | { type: "tool_call_start"; data: { call_id: string; name: string; args: Record<string, unknown> } }
  | { type: "tool_executing"; data: { call_id: string; name: string } }
  | { type: "tool_output_chunk"; data: { call_id: string; chunk: string } }
  | { type: "approval_required"; data: ApprovalInfo }
  | { type: "tool_result"; data: { call_id: string; name: string; content: string } }
  | { type: "message_saved"; data: { message_id: number; role: string } }
  | { type: "done"; data: { conversation_id: number } }
  | { type: "error"; data: { message: string } };

export interface ChatRequest {
  conversation_id?: number | null;
  skill_id?: string | null;
  message: string;
  files?: File[];
}

export interface CreateSkillRequest {
  name: string;
  display_name: string;
  description: string;
  system_prompt: string;
  tools: string[];
}

export interface UpdateSkillRequest {
  display_name?: string;
  description?: string;
  system_prompt?: string;
  tools?: string[];
}
