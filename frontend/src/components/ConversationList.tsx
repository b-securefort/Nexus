import { useEffect, useState, useRef, useMemo } from "react";
import { Search, MoreHorizontal, Pencil, Trash2, X, Check } from "lucide-react";
import { fetchConversations, deleteConversation, renameConversation } from "../api/conversations";
import { useAppStore } from "../store/useAppStore";
import type { ConversationSummary } from "../types";

function groupByDate(conversations: ConversationSummary[]) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterdayStart = new Date(todayStart.getTime() - 86400000);
  const prev7Start = new Date(todayStart.getTime() - 7 * 86400000);
  const prev30Start = new Date(todayStart.getTime() - 30 * 86400000);

  const groups: { label: string; items: ConversationSummary[] }[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 Days", items: [] },
    { label: "Previous 30 Days", items: [] },
    { label: "Older", items: [] },
  ];

  for (const conv of conversations) {
    const d = new Date(conv.updated_at);
    if (d >= todayStart) groups[0].items.push(conv);
    else if (d >= yesterdayStart) groups[1].items.push(conv);
    else if (d >= prev7Start) groups[2].items.push(conv);
    else if (d >= prev30Start) groups[3].items.push(conv);
    else groups[4].items.push(conv);
  }

  return groups.filter((g) => g.items.length > 0);
}

export function ConversationList() {
  const {
    conversations,
    setConversations,
    conversationId,
    setConversationId,
    resetChat,
    setSelectedSkillId,
    searchQuery,
    setSearchQuery,
  } = useAppStore();

  const [loading, setLoading] = useState(true);
  const [menuOpenId, setMenuOpenId] = useState<number | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  const loadConversations = () => {
    setLoading(true);
    fetchConversations()
      .then((convs) => {
        setConversations(convs);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadConversations();
    const interval = setInterval(loadConversations, 30000);
    return () => clearInterval(interval);
  }, []);

  // Close context menu on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenId(null);
      }
    };
    if (menuOpenId !== null) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [menuOpenId]);

  // Focus rename input
  useEffect(() => {
    if (renamingId !== null) renameInputRef.current?.focus();
  }, [renamingId]);

  const handleSelectConversation = (conv: ConversationSummary) => {
    if (renamingId === conv.id) return;
    setConversationId(conv.id);
    setSelectedSkillId(conv.skill_id);
  };

  const handleDelete = async (id: number) => {
    setMenuOpenId(null);
    try {
      await deleteConversation(id);
      if (conversationId === id) resetChat();
      loadConversations();
    } catch { /* ignore */ }
  };

  const handleStartRename = (conv: ConversationSummary) => {
    setMenuOpenId(null);
    setRenamingId(conv.id);
    setRenameValue(conv.title);
  };

  const handleConfirmRename = async () => {
    if (renamingId === null || !renameValue.trim()) return;
    try {
      await renameConversation(renamingId, renameValue.trim());
      loadConversations();
    } catch { /* ignore */ }
    setRenamingId(null);
  };

  const handleCancelRename = () => {
    setRenamingId(null);
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); handleConfirmRename(); }
    if (e.key === "Escape") handleCancelRename();
  };

  // Filter conversations by search
  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return conversations;
    const q = searchQuery.toLowerCase();
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, searchQuery]);

  // Group by date
  const groups = useMemo(() => groupByDate(filtered), [filtered]);

  return (
    <div className="flex flex-col h-full">
      {/* Search */}
      <div className="px-3 pt-3 pb-1">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search chats..."
            className="w-full bg-base-800/60 border border-base-700/40 rounded-lg pl-8 pr-8 py-1.5 text-sm text-base-200 placeholder-base-500 focus:outline-none focus:ring-1 focus:ring-accent/40 focus:border-accent/30 transition-[border-color,box-shadow] duration-150"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-base-500 hover:text-base-300"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Conversations grouped by date */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        {loading && conversations.length === 0 && (
          <div className="text-center text-base-600 text-sm py-8">Loading...</div>
        )}
        {!loading && conversations.length === 0 && (
          <div className="text-center text-base-600 text-sm py-8">No conversations yet</div>
        )}
        {!loading && searchQuery && filtered.length === 0 && conversations.length > 0 && (
          <div className="text-center text-base-600 text-sm py-8">No matching chats</div>
        )}

        {groups.map((group) => (
          <div key={group.label} className="mb-2">
            <div className="px-3 pt-3 pb-1">
              <span className="text-[11px] font-medium text-base-500 uppercase tracking-wider">
                {group.label}
              </span>
            </div>
            {group.items.map((conv) => (
              <div
                key={conv.id}
                role="button"
                tabIndex={0}
                onClick={() => handleSelectConversation(conv)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSelectConversation(conv);
                  }
                }}
                className={`relative w-full text-left rounded-lg px-3 py-2 mb-0.5 group transition-[background-color,transform] duration-150 cursor-pointer ${
                  conversationId === conv.id
                    ? "bg-base-800 ring-1 ring-base-700/80"
                    : "hover:bg-base-800/60"
                }`}
              >
                {renamingId === conv.id ? (
                  /* Inline rename */
                  <div className="flex items-center gap-1.5">
                    <input
                      ref={renameInputRef}
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={handleRenameKeyDown}
                      onBlur={handleConfirmRename}
                      className="flex-1 bg-base-900 border border-accent/40 rounded px-2 py-0.5 text-sm text-base-200 focus:outline-none min-w-0"
                      maxLength={100}
                    />
                    <button onClick={handleConfirmRename} className="p-0.5 text-green-400 hover:text-green-300">
                      <Check className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={handleCancelRename} className="p-0.5 text-base-500 hover:text-base-300">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ) : (
                  /* Normal display */
                  <div className="flex items-center gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-base-200 truncate">{conv.title}</div>
                    </div>
                    {/* Three-dot context menu trigger */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpenId(menuOpenId === conv.id ? null : conv.id);
                      }}
                      className={`flex-shrink-0 p-1 rounded transition-[opacity,color] duration-150 ${
                        menuOpenId === conv.id
                          ? "opacity-100 text-base-300"
                          : "opacity-0 group-hover:opacity-100 text-base-500 hover:text-base-300"
                      }`}
                    >
                      <MoreHorizontal className="w-4 h-4" />
                    </button>
                  </div>
                )}

                {/* Context menu dropdown */}
                {menuOpenId === conv.id && (
                  <div
                    ref={menuRef}
                    className="absolute right-2 top-full mt-1 z-50 bg-base-800 border border-base-700/60 rounded-lg shadow-xl py-1 min-w-[140px] animate-scale-in"
                  >
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleStartRename(conv);
                      }}
                      className="w-full flex items-center gap-2.5 px-3 py-1.5 text-sm text-base-300 hover:bg-base-700/60 transition-colors"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                      Rename
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(conv.id);
                      }}
                      className="w-full flex items-center gap-2.5 px-3 py-1.5 text-sm text-red-400 hover:bg-base-700/60 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Delete
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
