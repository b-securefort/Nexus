import { useState, useRef, useEffect } from "react";
import { X as XIcon } from "lucide-react";
import type { ContextUsage } from "../types";

interface Props {
  usage: ContextUsage | null;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

interface Category {
  label: string;
  tokens: number;
  color: string;
  swatchClass: string;
}

function buildCategories(usage: ContextUsage): Category[] {
  const fresh = Math.max(usage.prompt_tokens - usage.cached_tokens, 0);
  const free = Math.max(
    usage.context_window - usage.prompt_tokens - usage.completion_tokens,
    0
  );
  return [
    { label: "Cached prompt", tokens: usage.cached_tokens, color: "#60a5fa", swatchClass: "bg-blue-400" },
    { label: "Fresh prompt", tokens: fresh, color: "#a78bfa", swatchClass: "bg-violet-400" },
    { label: "Completion", tokens: usage.completion_tokens, color: "#f87171", swatchClass: "bg-red-400" },
    { label: "Free space", tokens: free, color: "#3f3f46", swatchClass: "bg-base-700" },
  ];
}

export function ContextUsageIndicator({ usage }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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

  if (!usage) {
    return (
      <div className="text-xs text-base-600">
        Context usage will appear after the first reply
      </div>
    );
  }

  const used = usage.prompt_tokens + usage.completion_tokens;
  const pct = usage.context_window > 0 ? (used / usage.context_window) * 100 : 0;
  const pctRounded = Math.round(pct);

  // SVG circular progress
  const radius = 7;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  // Color the ring based on usage band
  const ringColor =
    pct >= 90 ? "stroke-red-400" : pct >= 70 ? "stroke-amber-400" : "stroke-accent-light";

  const categories = buildCategories(usage);
  const totalForBar = usage.context_window || 1;

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Show context usage"
        aria-expanded={open}
        className="flex items-center gap-2 text-xs text-base-500 hover:text-base-300 transition-colors"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" className="-rotate-90">
          <circle
            cx="9"
            cy="9"
            r={radius}
            fill="none"
            strokeWidth="2"
            className="stroke-base-700"
          />
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
        <span>
          {formatTokens(used)} / {formatTokens(usage.context_window)} tokens ({pctRounded}%)
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Context usage breakdown"
          className="absolute bottom-full mb-2 left-0 z-40 w-[420px] max-w-[calc(100vw-3rem)] bg-base-900 border border-base-700/70 rounded-xl shadow-xl p-4 animate-fade-in-up"
        >
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-sm font-semibold text-base-100">Context usage</h3>
              <p className="text-xs text-base-500 mt-0.5">{usage.model}</p>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close context usage panel"
              className="text-base-500 hover:text-base-200"
            >
              <XIcon className="w-4 h-4" />
            </button>
          </div>

          <p className="text-xs text-base-300 mb-2">
            {formatTokens(used)} / {formatTokens(usage.context_window)} tokens ({pctRounded}%)
          </p>

          {/* Segmented bar */}
          <div className="flex h-2 w-full rounded-full overflow-hidden bg-base-800 mb-3">
            {categories.map((c) => {
              const widthPct = (c.tokens / totalForBar) * 100;
              if (widthPct <= 0) return null;
              return (
                <div
                  key={c.label}
                  style={{ width: `${widthPct}%`, backgroundColor: c.color }}
                  title={`${c.label}: ${formatTokens(c.tokens)}`}
                />
              );
            })}
          </div>

          {/* Category table */}
          <div className="space-y-1.5">
            <div className="grid grid-cols-[1fr_auto_auto] gap-4 text-[10px] uppercase tracking-wide text-base-500 pb-1 border-b border-base-800">
              <span>Category</span>
              <span className="text-right">Tokens</span>
              <span className="text-right w-12">Usage</span>
            </div>
            {categories.map((c) => {
              const usagePct = usage.context_window > 0
                ? (c.tokens / usage.context_window) * 100
                : 0;
              return (
                <div
                  key={c.label}
                  className="grid grid-cols-[1fr_auto_auto] gap-4 text-xs items-center"
                >
                  <span className="flex items-center gap-2 text-base-200">
                    <span
                      className={`inline-block w-2.5 h-2.5 rounded-sm ${c.swatchClass}`}
                      aria-hidden
                    />
                    {c.label}
                  </span>
                  <span className="text-right text-base-300 tabular-nums">
                    {formatTokens(c.tokens)}
                  </span>
                  <span className="text-right text-base-400 tabular-nums w-12">
                    {usagePct.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
