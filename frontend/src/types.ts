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

// "pending" while the advisory review LLM runs; resolves to safe/caution/destructive.
export type RiskLevel = "pending" | "safe" | "caution" | "destructive";

export interface ApprovalInfo {
  approval_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  reason: string;
  // Advisory risk verdict (§5 2026-06-04). May be null on older payloads.
  risk_level?: RiskLevel | null;
  risk_description?: string | null;
  // Deterministic, LLM-free render of the exact command (script/body_file content
  // inlined, up to 64 KB). Absent on older payloads → card falls back to its own
  // formatCommand. When command_truncated, the card offers a download (§5 2026-06-12).
  rendered_command?: string | null;
  command_truncated?: boolean;
}

export interface QuestionOption {
  label: string;
  description?: string;
}

export interface QuestionItem {
  question: string;
  header: string;
  options: QuestionOption[];
  multi_select: boolean;
}

export interface QuestionInfo {
  question_id: string;
  call_id: string;
  questions: QuestionItem[];
}

export interface QuestionAnswerEntry {
  question: string;
  selected: string[];
  notes?: string;
}

export interface ContextUsageSegment {
  label: string;
  tokens: number;
}

export interface ContextUsage {
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  context_window: number;
  model: string;
  // Input-side occupancy breakdown (System prompt / Knowledge base / Learnings /
  // Tools / Messages), scaled to sum to prompt_tokens. Absent on older payloads.
  segments?: ContextUsageSegment[];
}

// SSE event types
export type SSEEvent =
  | { type: "token"; data: { text: string } }
  | { type: "tool_call_start"; data: { call_id: string; name: string; args: Record<string, unknown> } }
  | { type: "tool_executing"; data: { call_id: string; name: string } }
  | { type: "tool_output_chunk"; data: { call_id: string; chunk: string } }
  | { type: "approval_required"; data: ApprovalInfo }
  | { type: "question_required"; data: QuestionInfo }
  | { type: "question_answered"; data: { question_id: string; call_id: string; answers: QuestionAnswerEntry[] } }
  | { type: "tool_result"; data: { call_id: string; name: string; content: string } }
  | { type: "message_saved"; data: { message_id: number; role: string } }
  | { type: "done"; data: { conversation_id: number; usage?: ContextUsage } }
  | { type: "error"; data: { message: string } }
  | { type: "token_refresh_required"; data: TokenRefreshRequired };

export interface TokenRefreshRequired {
  conversation_id: number;
  tool_name: string;
  status: "missing" | "expired" | "near_expiry";
}

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
