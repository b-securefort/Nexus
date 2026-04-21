import { apiFetch } from "./client";
import type { Skill, ToolInfo, CreateSkillRequest, UpdateSkillRequest } from "../types";

export async function fetchSkills(): Promise<Skill[]> {
  const res = await apiFetch("/api/skills");
  if (!res.ok) throw new Error("Failed to fetch skills");
  const data = await res.json();
  return Array.isArray(data) ? data : data.value ?? [];
}

export async function fetchTools(): Promise<ToolInfo[]> {
  const res = await apiFetch("/api/tools");
  if (!res.ok) throw new Error("Failed to fetch tools");
  return res.json();
}

export async function fetchPersonalSkill(name: string): Promise<Skill & { system_prompt: string }> {
  const res = await apiFetch(`/api/skills/personal/${name}`);
  if (!res.ok) throw new Error("Skill not found");
  return res.json();
}

export async function createPersonalSkill(body: CreateSkillRequest): Promise<Skill> {
  const res = await apiFetch("/api/skills/personal", {
    method: "POST",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed" }));
    throw new Error(err.detail || "Failed to create skill");
  }
  return res.json();
}

export async function updatePersonalSkill(
  name: string,
  body: UpdateSkillRequest
): Promise<Skill> {
  const res = await apiFetch(`/api/skills/personal/${name}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed" }));
    throw new Error(err.detail || "Failed to update skill");
  }
  return res.json();
}

export async function deletePersonalSkill(name: string): Promise<void> {
  const res = await apiFetch(`/api/skills/personal/${name}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete skill");
}
