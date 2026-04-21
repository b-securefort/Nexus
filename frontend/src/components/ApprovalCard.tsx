import { useState, useEffect } from "react";
import { ShieldAlert, Check, X, Clock } from "lucide-react";
import type { ApprovalInfo } from "../types";

interface Props {
  approval: ApprovalInfo;
  onAction: (action: "approve" | "deny") => void;
  timeoutSeconds?: number;
}

function formatCommand(toolName: string, args: Record<string, unknown>): string {
  if (toolName === "az_cli" && Array.isArray(args.args)) {
    return `az ${(args.args as string[]).join(" ")}`;
  }
  if (toolName === "run_shell" && typeof args.command === "string") {
    return args.command;
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

export function ApprovalCard({ approval, onAction, timeoutSeconds = 600 }: Props) {
  const [remaining, setRemaining] = useState(timeoutSeconds);

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

  return (
    <div className="bg-amber-900/20 border border-amber-600/50 rounded-lg p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2 text-amber-400">
        <ShieldAlert className="w-5 h-5" />
        <span className="font-semibold">Approval Required</span>
        <span className="ml-auto flex items-center gap-1 text-sm text-amber-500">
          <Clock className="w-3.5 h-3.5" />
          {minutes}:{seconds.toString().padStart(2, "0")}
        </span>
      </div>

      {/* Tool name */}
      <div>
        <span className="text-zinc-400 text-sm">Tool: </span>
        <span className="font-mono text-sm text-amber-300">{approval.tool_name}</span>
      </div>

      {/* Reason */}
      {approval.reason && (
        <div>
          <span className="text-zinc-400 text-sm">Reason: </span>
          <span className="text-zinc-200 text-sm">{approval.reason}</span>
        </div>
      )}

      {/* Command */}
      <div>
        <span className="text-zinc-400 text-sm">Command:</span>
        <pre className="mt-1 bg-zinc-900 rounded p-3 text-sm text-zinc-200 font-mono overflow-x-auto whitespace-pre-wrap">
          {formatCommand(approval.tool_name, approval.args)}
        </pre>
      </div>

      {/* Buttons */}
      <div className="flex gap-3">
        <button
          onClick={() => onAction("approve")}
          disabled={remaining === 0}
          className="flex items-center gap-2 bg-green-700 hover:bg-green-600 disabled:bg-zinc-700 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
        >
          <Check className="w-4 h-4" />
          Approve
        </button>
        <button
          onClick={() => onAction("deny")}
          disabled={remaining === 0}
          className="flex items-center gap-2 bg-red-700 hover:bg-red-600 disabled:bg-zinc-700 text-white px-4 py-2 rounded-lg transition-colors text-sm font-medium"
        >
          <X className="w-4 h-4" />
          Deny
        </button>
      </div>
    </div>
  );
}
