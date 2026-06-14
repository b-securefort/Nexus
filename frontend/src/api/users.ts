/**
 * Admin API client for per-user usage caps (DESIGN.md §5 2026-06-14).
 * Caps are in credits (1 credit = $0.01); the backend stores them as USD.
 */

import { apiFetch } from "./client";

export interface UserUsageRow {
  oid: string;
  email: string;
  display_name: string;
  cap_credits: number | null; // per-user override, null = uses default
  effective_cap_credits: number; // resolved cap actually applied
  spent_this_week_credits: number;
  remaining_credits: number;
  week_resets_at: string;
}

export interface UserListResponse {
  items: UserUsageRow[];
  default_cap_credits: number;
}

export async function listUsers(): Promise<UserListResponse> {
  const res = await apiFetch("/api/users");
  if (!res.ok) {
    if (res.status === 403) throw new Error("Architect role required to manage user caps.");
    throw new Error(`Failed to fetch users (${res.status})`);
  }
  return res.json();
}

/** Set a user's weekly cap in credits, or pass null to clear (revert to default). */
export async function updateUserCap(
  oid: string,
  capCredits: number | null,
): Promise<UserUsageRow> {
  const res = await apiFetch(`/api/users/${encodeURIComponent(oid)}`, {
    method: "PATCH",
    body: JSON.stringify({ cap_credits: capCredits }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail || `Failed to update cap (${res.status})`);
  }
  return res.json();
}
