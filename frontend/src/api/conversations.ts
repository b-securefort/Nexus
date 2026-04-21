import { apiFetch } from "./client";
import type { ConversationSummary, ConversationDetail } from "../types";

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const res = await apiFetch("/api/conversations");
  if (!res.ok) throw new Error("Failed to fetch conversations");
  return res.json();
}

export async function fetchConversation(id: number): Promise<ConversationDetail> {
  const res = await apiFetch(`/api/conversations/${id}`);
  if (!res.ok) throw new Error("Conversation not found");
  return res.json();
}

export async function deleteConversation(id: number): Promise<void> {
  const res = await apiFetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete conversation");
}

export async function renameConversation(id: number, title: string): Promise<void> {
  const res = await apiFetch(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error("Failed to rename conversation");
}
