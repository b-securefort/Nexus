import { apiFetch } from "./client";

export type LearningStatus = "active" | "provisional" | "archived" | "rejected";
export type LearningType = "semantic" | "procedural";
export type LearningCategory =
  | "syntax-fix"
  | "known-issue"
  | "workaround"
  | "best-practice"
  | "gotcha";

export interface LearningSummary {
  id: number;
  type: LearningType;
  category: LearningCategory;
  tool_name: string;
  summary: string;
  status: LearningStatus;
  validation_count: number;
  failure_count: number;
  recorded_at: string;
  last_validated_at: string | null;
  last_retrieved_at: string | null;
}

export interface LearningDetail extends LearningSummary {
  details: string;
  archived_at: string | null;
  originating_conversation_id: number | null;
  judge_verdict: Record<string, unknown> | null;
  embed_model: string | null;
}

export interface ListLearningsResponse {
  items: LearningSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface ListLearningsParams {
  status?: LearningStatus;
  type?: LearningType;
  category?: LearningCategory;
  tool_name?: string;
  limit?: number;
  offset?: number;
}

export async function listLearnings(
  params: ListLearningsParams = {},
): Promise<ListLearningsResponse> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.type) qs.set("type", params.type);
  if (params.category) qs.set("category", params.category);
  if (params.tool_name) qs.set("tool_name", params.tool_name);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));

  const res = await apiFetch(`/api/learnings?${qs.toString()}`);
  if (!res.ok) {
    if (res.status === 403) {
      throw new Error("Architect role required to view learnings.");
    }
    throw new Error(`Failed to fetch learnings (${res.status})`);
  }
  return res.json();
}

export async function getLearning(id: number): Promise<LearningDetail> {
  const res = await apiFetch(`/api/learnings/${id}`);
  if (!res.ok) {
    if (res.status === 404) throw new Error("Learning not found");
    if (res.status === 403) throw new Error("Architect role required.");
    throw new Error(`Failed to fetch learning (${res.status})`);
  }
  return res.json();
}

export type PatchableStatus = "active" | "provisional" | "archived";

export async function patchLearningStatus(
  id: number,
  status: PatchableStatus,
): Promise<LearningDetail> {
  const res = await apiFetch(`/api/learnings/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail || `Failed to patch learning (${res.status})`);
  }
  return res.json();
}

export async function deleteLearning(id: number): Promise<void> {
  const res = await apiFetch(`/api/learnings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Failed to delete learning (${res.status})`);
  }
}
