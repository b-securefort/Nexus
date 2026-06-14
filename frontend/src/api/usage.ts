/**
 * Usage / spend-cap API client (DESIGN.md §5 2026-06-14).
 */

import { apiFetch } from "./client";
import type { WeeklyBudget } from "../types";

/**
 * Fetch the current user's weekly spend-cap status for the budget indicator.
 * Read-only — never blocks anything. Returns `{ enabled: false }` when the
 * feature is off so the caller can simply render nothing.
 */
export async function fetchWeeklyBudget(): Promise<WeeklyBudget> {
  const res = await apiFetch("/api/usage/me");
  if (!res.ok) {
    throw new Error(`Failed to fetch weekly budget: ${res.status}`);
  }
  return (await res.json()) as WeeklyBudget;
}
