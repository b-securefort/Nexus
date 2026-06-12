import { useState, useEffect } from "react";
import { ShieldAlert, Check, X, Clock, ShieldCheck, AlertTriangle, OctagonAlert, Loader2, Info, Download } from "lucide-react";
import type { ApprovalInfo, RiskLevel } from "../types";
import { apiFetch } from "../api/client";

interface Props {
  approval: ApprovalInfo;
  onAction: (action: "approve" | "deny") => void;
  timeoutSeconds?: number;
}

function formatCommand(toolName: string, args: Record<string, unknown>): string {
  if (toolName === "az_cli" && Array.isArray(args.args)) {
    return `az ${(args.args as string[]).join(" ")}`;
  }
  if (toolName === "execute_script" && typeof args.path === "string") {
    return `script: output/scripts/${String(args.path).replace(/^scripts\//, "")}`;
  }
  if (toolName === "az_resource_graph" && typeof args.query === "string") {
    return args.query;
  }
  // Fallback for unknown tools
  return Object.entries(args)
    .filter(([k]) => k !== "reason")
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("\n");
}

// Visual treatment per advisory risk tier. `pending` is the in-flight state
// while the review LLM runs — Allow stays disabled until it resolves.
const RISK_UI: Record<RiskLevel, { label: string; Icon: typeof Check; className: string }> = {
  pending: { label: "Assessing risk…", Icon: Loader2, className: "text-base-400" },
  safe: { label: "Safe to run", Icon: ShieldCheck, className: "text-success" },
  caution: { label: "Review before running", Icon: AlertTriangle, className: "text-warning" },
  destructive: { label: "Destructive — review carefully", Icon: OctagonAlert, className: "text-danger" },
};

export function ApprovalCard({ approval, onAction, timeoutSeconds = 600 }: Props) {
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(interval);
          return 0;
        }
        return r - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;

  const risk: RiskLevel | null = approval.risk_level ?? null;
  const isAssessing = risk === "pending";
  const isDestructive = risk === "destructive";
  // Advisory only: the verdict never blocks execution, it only gates the UI so
  // the user can't approve a destructive command before its risk is shown.
  const allowDisabled = remaining === 0 || isAssessing;
  const riskUi = risk ? RISK_UI[risk] : null;

  const handleApproveClick = () => {
    if (isDestructive && !confirming) {
      setConfirming(true);
      return;
    }
    onAction("approve");
  };

  // Prefer the backend's deterministic resolved command (shows the real
  // script/body payload, not a pointer); fall back to local reconstruction only
  // on older payloads that don't carry it (§5 2026-06-12).
  const commandText =
    approval.rendered_command ?? formatCommand(approval.tool_name, approval.args);

  // Download the full (uncapped) command when it was truncated for the card.
  // Fetched via apiFetch so the bearer token rides along (a bare <a href> 401s
  // in MSAL mode), then handed to the browser as a blob download.
  const handleDownloadCommand = async () => {
    try {
      const resp = await apiFetch(`/api/approvals/${approval.approval_id}/command`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `approval-${approval.approval_id}-command.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      /* best-effort download; nothing to surface on failure */
    }
  };

  return (
    <div className="bg-warning/5 border border-warning/25 rounded-xl p-5 space-y-3.5">
      {/* Header */}
      <div className="flex items-center gap-2.5 text-warning">
        <ShieldAlert className="w-5 h-5" />
        <span className="font-semibold text-sm tracking-tight">Approval Required</span>
        <span className="ml-auto flex items-center gap-1 text-sm text-warning/80">
          <Clock className="w-3.5 h-3.5" />
          {minutes}:{seconds.toString().padStart(2, "0")}
        </span>
      </div>

      {/* Risk badge (advisory) */}
      {riskUi && (
        <div className={`flex items-center gap-2 ${riskUi.className}`}>
          <riskUi.Icon className={`w-4 h-4 ${isAssessing ? "animate-spin" : ""}`} />
          <span className="text-sm font-medium">{riskUi.label}</span>
        </div>
      )}

      {/* Tool name */}
      <div>
        <span className="text-base-400 text-sm">Tool: </span>
        <span className="font-mono text-sm text-warning">{approval.tool_name}</span>
      </div>

      {/* What this command does (review LLM description) */}
      {approval.risk_description && (
        <div>
          <span className="text-base-400 text-sm">What it does: </span>
          <span className="text-base-200 text-sm">{approval.risk_description}</span>
        </div>
      )}

      {/* Command — deterministic backend render (full payload, not a pointer) */}
      <div>
        <span className="text-base-400 text-sm">Command:</span>
        <pre className="mt-1.5 bg-base-900/80 rounded-lg p-3 text-sm text-base-200 font-mono overflow-x-auto overflow-y-auto max-h-40 whitespace-pre-wrap">
          {commandText}
        </pre>
        {approval.command_truncated && (
          <button
            onClick={handleDownloadCommand}
            className="mt-2 flex items-center gap-1.5 text-sm text-warning hover:text-warning/80 transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            Command truncated — download full command to review
          </button>
        )}
      </div>

      {/* Double-confirm prompt for destructive commands */}
      {confirming && (
        <div className="flex items-center gap-2 text-danger text-sm">
          <OctagonAlert className="w-4 h-4 shrink-0" />
          <span>This is flagged as destructive. Run it anyway?</span>
        </div>
      )}

      {/* Buttons */}
      <div className="flex gap-3 pt-1">
        <button
          onClick={handleApproveClick}
          disabled={allowDisabled}
          className={`flex items-center gap-2 ${
            confirming ? "bg-danger-strong" : "bg-success-strong"
          } hover:brightness-110 disabled:bg-base-800 disabled:text-base-600 text-white px-4 py-2 rounded-xl transition-[background-color,transform,filter] duration-150 ease-[var(--ease-out)] text-sm font-medium`}
        >
          <Check className="w-4 h-4" />
          {confirming ? "Yes, run it" : "Approve"}
        </button>
        <button
          onClick={() => (confirming ? setConfirming(false) : onAction("deny"))}
          disabled={remaining === 0}
          className="flex items-center gap-2 bg-danger-strong hover:brightness-110 disabled:bg-base-800 disabled:text-base-600 text-white px-4 py-2 rounded-xl transition-[background-color,transform,filter] duration-150 ease-[var(--ease-out)] text-sm font-medium"
        >
          <X className="w-4 h-4" />
          {confirming ? "Cancel" : "Deny"}
        </button>
      </div>

      {/* AI-generated disclaimer */}
      {riskUi && !isAssessing && (
        <div className="flex items-center gap-1.5 text-xs text-base-500 pt-0.5">
          <Info className="w-3 h-3 shrink-0" />
          <span>Risk assessment is AI-generated and may be inaccurate.</span>
        </div>
      )}
    </div>
  );
}
