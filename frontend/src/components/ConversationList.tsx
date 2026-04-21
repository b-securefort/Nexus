import { useEffect, useState } from "react";
import { MessageSquare, Plus, Trash2 } from "lucide-react";
import { fetchConversations, deleteConversation } from "../api/conversations";
import { useAppStore } from "../store/useAppStore";
import type { ConversationSummary } from "../types";

export function ConversationList() {
  const {
    conversations,
    setConversations,
    conversationId,
    setConversationId,
    resetChat,
    setSelectedSkillId,
  } = useAppStore();

  const [loading, setLoading] = useState(true);

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
    // Refresh every 30 seconds
    const interval = setInterval(loadConversations, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleNewChat = () => {
    resetChat();
  };

  const handleSelectConversation = (conv: ConversationSummary) => {
    setConversationId(conv.id);
    setSelectedSkillId(conv.skill_id);
  };

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      if (conversationId === id) {
        resetChat();
      }
      loadConversations();
    } catch {
      // Ignore
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const hours = diff / (1000 * 60 * 60);

    if (hours < 1) return "Just now";
    if (hours < 24) return `${Math.floor(hours)}h ago`;
    if (hours < 168) return `${Math.floor(hours / 24)}d ago`;
    return date.toLocaleDateString();
  };

  return (
    <div className="flex flex-col h-full">
      {/* New chat button */}
      <div className="p-3">
        <button
          onClick={handleNewChat}
          className="w-full flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg px-3 py-2.5 text-sm font-medium transition-colors"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* Conversations list */}
      <div className="flex-1 overflow-y-auto px-2">
        {loading && conversations.length === 0 && (
          <div className="text-center text-zinc-500 text-sm py-4">
            Loading...
          </div>
        )}

        {!loading && conversations.length === 0 && (
          <div className="text-center text-zinc-500 text-sm py-4">
            No conversations yet
          </div>
        )}

        {conversations.map((conv) => (
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
            className={`w-full text-left rounded-lg px-3 py-2.5 mb-1 group transition-colors cursor-pointer ${
              conversationId === conv.id
                ? "bg-zinc-700"
                : "hover:bg-zinc-800"
            }`}
          >
            <div className="flex items-start gap-2">
              <MessageSquare className="w-4 h-4 text-zinc-500 mt-0.5 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-zinc-200 truncate">
                  {conv.title}
                </div>
                <div className="text-xs text-zinc-500 mt-0.5">
                  {formatDate(conv.updated_at)}
                </div>
              </div>
              <button
                onClick={(e) => handleDelete(e, conv.id)}
                className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 transition-all p-1"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
