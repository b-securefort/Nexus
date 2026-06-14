import { useState, useRef, useEffect } from "react";
import { X as XIcon } from "lucide-react";
import { fetchWeeklyBudget } from "../api/usage";
import type { WeeklyBudget } from "../types";

interface Props {
  // Bump this to trigger a refetch (e.g. after each completed chat turn).
  refreshSignal?: number;
}

// 1 usage credit = $0.01 → dollars × 100, shown as whole credits. The cap is
// configured in USD (users.credit_cap_usd); "credits" is purely a display unit.
const CREDITS_PER_USD = 100;
function credits(n: number | undefined): string {
  if (n === undefined) return "—";
  return Math.round(n * CREDITS_PER_USD).toLocaleString("en-US");
}

function formatResetDate(iso: string | undefined): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

// A weekly *spend* budget indicator — how much of this week's cap is left.
// Deliberately distinct from ContextUsageIndicator (which is context-window
// occupancy, not spend). Note the colour logic is INVERTED relative to that
// gauge: here a LOW remaining fraction is the warning state.
export function WeeklyBudgetIndicator({ refreshSignal }: Props) {
  const [budget, setBudget] = useState<WeeklyBudget | null>(null);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchWeeklyBudget()
      .then((b) => {
        if (!cancelled) setBudget(b);
      })
      .catch(() => {
        // Read-only accessory — a failed fetch just hides the indicator.
        if (!cancelled) setBudget(null);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshSignal]);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  // Nothing to show until loaded, or when the feature is disabled.
  if (!budget || !budget.enabled) {
    return null;
  }

  const frac = budget.remaining_fraction ?? 0;
  const pct = frac * 100;
  const pctRounded = Math.round(pct);

  // SVG circular progress (mirrors ContextUsageIndicator's geometry for a
  // consistent look in the composer row).
  const radius = 7;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (frac * circumference);

  // Inverted bands: low remaining = bad.
  const ringColor =
    frac <= 0.1 ? "stroke-danger" : frac <= 0.3 ? "stroke-warning" : "stroke-accent-light";

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Show weekly budget"
        aria-expanded={open}
        className="flex items-center gap-2 text-xs text-base-500 hover:text-base-300 transition-colors"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" className="-rotate-90">
          <circle cx="9" cy="9" r={radius} fill="none" strokeWidth="2" className="stroke-base-700" />
          <circle
            cx="9"
            cy="9"
            r={radius}
            fill="none"
            strokeWidth="2"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            strokeLinecap="round"
            className={ringColor}
            style={{ transition: "stroke-dashoffset 300ms ease" }}
          />
        </svg>
        <span>{credits(budget.remaining_usd)} credits left</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Weekly budget breakdown"
          className="absolute bottom-full mb-2 right-0 z-40 w-[320px] max-w-[calc(100vw-3rem)] bg-base-900 border border-base-700/70 rounded-xl shadow-xl p-4 animate-fade-in-up"
        >
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-sm font-semibold text-base-100">Weekly budget</h3>
              <p className="text-xs text-base-500 mt-0.5">
                Resets {formatResetDate(budget.week_resets_at)}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close weekly budget panel"
              className="text-base-500 hover:text-base-200"
            >
              <XIcon className="w-4 h-4" />
            </button>
          </div>

          <p className="text-xs text-base-300 mb-1">
            {credits(budget.remaining_usd)} of {credits(budget.cap_usd)} credits remaining ({pctRounded}%)
          </p>
          <p className="text-[11px] text-base-500 mb-2">
            Usage credits drawn from your weekly cap — not context usage.
            Overspend carries into next week.
          </p>

          {/* Remaining bar (fills with what's LEFT) */}
          <div className="flex h-2 w-full rounded-full overflow-hidden bg-base-800 mb-3">
            <div
              style={{ width: `${pct}%` }}
              className={
                frac <= 0.1 ? "bg-danger" : frac <= 0.3 ? "bg-warning" : "bg-accent-light"
              }
            />
          </div>

          <div className="space-y-1 text-xs">
            <div className="flex justify-between">
              <span className="text-base-400">Spent this week</span>
              <span className="text-base-200 tabular-nums">{credits(budget.spent_this_week_usd)}</span>
            </div>
            {(budget.carryover_debt_usd ?? 0) > 0 && (
              <div className="flex justify-between">
                <span className="text-base-400">Carried-over debt</span>
                <span className="text-warning tabular-nums">
                  −{credits(budget.carryover_debt_usd)}
                </span>
              </div>
            )}
            <div className="flex justify-between border-t border-base-800 pt-1 mt-1">
              <span className="text-base-300">Remaining</span>
              <span className="text-base-100 tabular-nums">{credits(budget.remaining_usd)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
