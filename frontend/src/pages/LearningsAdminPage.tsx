import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Brain, RefreshCw, Trash2, X } from "lucide-react";
import {
  deleteLearning,
  getLearning,
  listLearnings,
  patchLearningStatus,
  type LearningCategory,
  type LearningDetail,
  type LearningStatus,
  type LearningSummary,
  type LearningType,
  type PatchableStatus,
} from "../api/learnings";

const STATUS_OPTIONS: { value: LearningStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "active", label: "Active (canonical)" },
  { value: "provisional", label: "Provisional" },
  { value: "archived", label: "Archived" },
  { value: "rejected", label: "Rejected (judge audit)" },
];

const TYPE_OPTIONS: { value: LearningType | ""; label: string }[] = [
  { value: "", label: "All types" },
  { value: "semantic", label: "Semantic (facts)" },
  { value: "procedural", label: "Procedural (rules)" },
];

const CATEGORY_OPTIONS: { value: LearningCategory | ""; label: string }[] = [
  { value: "", label: "All categories" },
  { value: "syntax-fix", label: "syntax-fix" },
  { value: "known-issue", label: "known-issue" },
  { value: "workaround", label: "workaround" },
  { value: "best-practice", label: "best-practice" },
  { value: "gotcha", label: "gotcha" },
];

const STATUS_BADGE: Record<LearningStatus, string> = {
  active: "bg-success/10 text-success border border-success/30",
  provisional: "bg-warning/10 text-warning border border-warning/30",
  archived: "bg-base-800 text-base-500 border border-base-800",
  rejected: "bg-danger/10 text-danger border border-danger/30",
};

const PAGE_SIZE = 50;

export function LearningsAdminPage() {
  const [items, setItems] = useState<LearningSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<LearningStatus | "">("");
  const [typeFilter, setTypeFilter] = useState<LearningType | "">("");
  const [categoryFilter, setCategoryFilter] = useState<LearningCategory | "">("");
  const [toolFilter, setToolFilter] = useState("");

  // Detail panel
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<LearningDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    listLearnings({
      status: statusFilter || undefined,
      type: typeFilter || undefined,
      category: categoryFilter || undefined,
      tool_name: toolFilter || undefined,
      limit: PAGE_SIZE,
      offset,
    })
      .then((resp) => {
        setItems(resp.items);
        setTotal(resp.total);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, [statusFilter, typeFilter, categoryFilter, toolFilter, offset]);

  // Reset offset when filters change
  useEffect(() => {
    setOffset(0);
  }, [statusFilter, typeFilter, categoryFilter, toolFilter]);

  useEffect(() => {
    if (selectedId === null) {
      setSelected(null);
      return;
    }
    setDetailLoading(true);
    getLearning(selectedId)
      .then(setSelected)
      .catch(() => setSelected(null))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  const handlePatchStatus = async (id: number, status: PatchableStatus) => {
    try {
      const updated = await patchLearningStatus(id, status);
      setSelected(updated);
      // Refresh list to show new status
      load();
    } catch (e: any) {
      alert(`Status change failed: ${e.message}`);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm(`Delete learning #${id}? This is permanent.`)) return;
    try {
      await deleteLearning(id);
      setSelectedId(null);
      load();
    } catch (e: any) {
      alert(`Delete failed: ${e.message}`);
    }
  };

  const pagination = useMemo(() => {
    if (total <= PAGE_SIZE) return null;
    const page = Math.floor(offset / PAGE_SIZE) + 1;
    const pages = Math.ceil(total / PAGE_SIZE);
    return { page, pages };
  }, [total, offset]);

  return (
    <div className="min-h-screen bg-base-950 text-base-100">
      <div className="max-w-6xl mx-auto px-6 py-10">
        <div className="flex items-center gap-4 mb-8">
          <Link to="/" className="text-base-500 hover:text-base-300 p-1">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center">
            <Brain className="w-3.5 h-3.5 text-accent-light" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight">Agent Learnings</h1>
          <span className="text-xs text-base-500">
            {total} total{pagination && ` · page ${pagination.page} / ${pagination.pages}`}
          </span>
          <button
            onClick={load}
            className="ml-auto text-base-500 hover:text-base-300 p-2 rounded-md hover:bg-base-900"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* Filters */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as LearningStatus | "")}
            className="bg-base-900 border border-base-800 rounded-lg px-3 py-2 text-sm"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value || "any"} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as LearningType | "")}
            className="bg-base-900 border border-base-800 rounded-lg px-3 py-2 text-sm"
          >
            {TYPE_OPTIONS.map((o) => (
              <option key={o.value || "any"} value={o.value}>{o.label}</option>
            ))}
          </select>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as LearningCategory | "")}
            className="bg-base-900 border border-base-800 rounded-lg px-3 py-2 text-sm"
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.value || "any"} value={o.value}>{o.label}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="Filter by tool name…"
            value={toolFilter}
            onChange={(e) => setToolFilter(e.target.value)}
            className="bg-base-900 border border-base-800 rounded-lg px-3 py-2 text-sm font-mono"
          />
        </div>

        {error && (
          <div className="bg-danger/10 border border-danger/30 text-danger px-4 py-3 rounded-lg mb-4 text-sm">
            {error}
          </div>
        )}

        {/* Table */}
        <div className="bg-base-900 border border-base-800/80 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-[11px] text-base-500 uppercase tracking-wider border-b border-base-800/80">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">Status</th>
                <th className="text-left px-4 py-2.5 font-medium">Type</th>
                <th className="text-left px-4 py-2.5 font-medium">Category</th>
                <th className="text-left px-4 py-2.5 font-medium">Tool</th>
                <th className="text-left px-4 py-2.5 font-medium">Summary</th>
                <th className="text-right px-4 py-2.5 font-medium">Val / Fail</th>
                <th className="text-left px-4 py-2.5 font-medium">Recorded</th>
              </tr>
            </thead>
            <tbody>
              {loading && items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-base-500">Loading…</td></tr>
              )}
              {!loading && items.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-base-500">No learnings match.</td></tr>
              )}
              {items.map((it) => (
                <tr
                  key={it.id}
                  onClick={() => setSelectedId(it.id)}
                  className="border-b border-base-800/40 hover:bg-base-800/40 cursor-pointer"
                >
                  <td className="px-4 py-2">
                    <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${STATUS_BADGE[it.status]}`}>
                      {it.status}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-base-400 text-xs">{it.type}</td>
                  <td className="px-4 py-2 text-base-400 text-xs font-mono">{it.category}</td>
                  <td className="px-4 py-2 text-base-300 text-xs font-mono">{it.tool_name}</td>
                  <td className="px-4 py-2 text-base-200 max-w-md truncate">{it.summary}</td>
                  <td className="px-4 py-2 text-right text-xs text-base-500 font-mono">
                    <span className="text-success">{it.validation_count}</span>
                    {" / "}
                    <span className="text-danger">{it.failure_count}</span>
                  </td>
                  <td className="px-4 py-2 text-xs text-base-500">
                    {new Date(it.recorded_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {pagination && (
          <div className="flex items-center justify-center gap-2 mt-4 text-sm">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="px-3 py-1.5 bg-base-900 border border-base-800 rounded-md hover:bg-base-800 disabled:opacity-40"
            >
              Previous
            </button>
            <span className="text-base-500 text-xs px-2">
              {offset + 1}–{Math.min(offset + items.length, total)} of {total}
            </span>
            <button
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="px-3 py-1.5 bg-base-900 border border-base-800 rounded-md hover:bg-base-800 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        )}
      </div>

      {/* Detail drawer */}
      {selectedId !== null && (
        <div
          className="fixed inset-0 bg-black/60 z-40 flex justify-end"
          onClick={() => setSelectedId(null)}
        >
          <div
            className="w-full max-w-2xl h-full bg-base-950 border-l border-base-800 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 bg-base-950 border-b border-base-800/80 px-6 py-4 flex items-center gap-3">
              <h2 className="text-base font-semibold">Learning #{selectedId}</h2>
              <button
                onClick={() => setSelectedId(null)}
                className="ml-auto text-base-500 hover:text-base-300 p-1"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="px-6 py-5 space-y-5">
              {detailLoading && <div className="text-base-500 text-sm">Loading…</div>}
              {!detailLoading && selected && (
                <>
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${STATUS_BADGE[selected.status]}`}>
                      {selected.status}
                    </span>
                    <span className="text-xs text-base-500 font-mono">{selected.type}</span>
                    <span className="text-xs text-base-500 font-mono">{selected.category}</span>
                    <span className="text-xs text-base-500 font-mono">{selected.tool_name}</span>
                  </div>

                  <div>
                    <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Summary</div>
                    <div className="text-sm text-base-200">{selected.summary}</div>
                  </div>

                  <div>
                    <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Details</div>
                    <pre className="text-xs text-base-300 bg-base-900 border border-base-800 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed">{selected.details}</pre>
                  </div>

                  <div className="grid grid-cols-2 gap-4 text-xs">
                    <div>
                      <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Validations</div>
                      <div className="text-success font-mono">{selected.validation_count}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Failures</div>
                      <div className="text-danger font-mono">{selected.failure_count}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Recorded</div>
                      <div className="text-base-400">{new Date(selected.recorded_at).toLocaleString()}</div>
                    </div>
                    <div>
                      <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Last validated</div>
                      <div className="text-base-400">
                        {selected.last_validated_at ? new Date(selected.last_validated_at).toLocaleString() : "—"}
                      </div>
                    </div>
                    {selected.originating_conversation_id !== null && (
                      <div>
                        <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Origin conv.</div>
                        <div className="text-base-400">#{selected.originating_conversation_id}</div>
                      </div>
                    )}
                    {selected.embed_model && (
                      <div>
                        <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">Embed model</div>
                        <div className="text-base-400 font-mono">{selected.embed_model}</div>
                      </div>
                    )}
                  </div>

                  {selected.judge_verdict && (
                    <div>
                      <div className="text-[10px] text-base-500 uppercase tracking-wider mb-1">LLM judge verdict</div>
                      <pre className="text-xs text-base-300 bg-base-900 border border-base-800 rounded-lg p-3 whitespace-pre-wrap font-mono">
                        {JSON.stringify(selected.judge_verdict, null, 2)}
                      </pre>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="border-t border-base-800/80 pt-4 flex gap-2">
                    {selected.status !== "rejected" && (
                      <>
                        {selected.status !== "active" && (
                          <button
                            onClick={() => handlePatchStatus(selected.id, "active")}
                            className="px-3 py-2 text-xs bg-success/10 text-success border border-success/30 rounded-md hover:bg-success/20"
                          >
                            Promote to active
                          </button>
                        )}
                        {selected.status !== "provisional" && (
                          <button
                            onClick={() => handlePatchStatus(selected.id, "provisional")}
                            className="px-3 py-2 text-xs bg-warning/10 text-warning border border-warning/30 rounded-md hover:bg-warning/20"
                          >
                            Demote to provisional
                          </button>
                        )}
                        {selected.status !== "archived" && (
                          <button
                            onClick={() => handlePatchStatus(selected.id, "archived")}
                            className="px-3 py-2 text-xs bg-base-800 text-base-300 border border-base-800 rounded-md hover:bg-base-700"
                          >
                            Archive
                          </button>
                        )}
                      </>
                    )}
                    {selected.status === "rejected" && (
                      <span className="text-xs text-base-500 italic">
                        Rejected (judge audit) — status cannot be changed.
                      </span>
                    )}
                    <button
                      onClick={() => handleDelete(selected.id)}
                      className="ml-auto px-3 py-2 text-xs bg-danger/10 text-danger border border-danger/30 rounded-md hover:bg-danger/20 flex items-center gap-1.5"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
