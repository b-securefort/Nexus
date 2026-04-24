import { ChatWindow } from "../components/ChatWindow";
import { ConversationList } from "../components/ConversationList";
import { SkillPicker } from "../components/SkillPicker";
import { Sparkles, Settings, PanelLeftClose, PanelLeft, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";

export function ChatPage() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const setSidebarOpen = useAppStore((s) => s.setSidebarOpen);
  const resetChat = useAppStore((s) => s.resetChat);

  return (
    <div className="flex h-screen bg-base-950 text-base-100">
      {/* Mobile backdrop */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`
          sidebar-panel
          fixed md:relative z-40 md:z-auto
          h-full bg-base-950 border-r border-base-800/80
          flex flex-col
          transition-[width,transform] duration-300 ease-[var(--ease-out)]
          ${sidebarOpen
            ? "w-72 translate-x-0"
            : "w-0 -translate-x-full md:translate-x-0 md:w-0"
          }
        `}
      >
        <div className={`flex flex-col h-full ${sidebarOpen ? "opacity-100" : "opacity-0"} transition-opacity duration-200 min-w-[288px]`}>
          {/* Sidebar header: logo + new chat + collapse */}
          <div className="px-3 py-3 border-b border-base-800/80">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center">
                  <Sparkles className="w-4 h-4 text-accent-light" />
                </div>
                <span className="font-semibold text-base-100 tracking-tight">Nexus</span>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => { resetChat(); }}
                  className="p-1.5 text-base-400 hover:text-base-200 hover:bg-base-800/80 rounded-lg transition-colors"
                  title="New Chat"
                >
                  <Plus className="w-5 h-5" />
                </button>
                <button
                  onClick={toggleSidebar}
                  className="p-1.5 text-base-400 hover:text-base-200 hover:bg-base-800/80 rounded-lg transition-colors"
                  title="Close sidebar"
                >
                  <PanelLeftClose className="w-5 h-5" />
                </button>
              </div>
            </div>
          </div>

          {/* Conversation list */}
          <div className="flex-1 overflow-hidden">
            <ConversationList />
          </div>

          {/* Footer */}
          <div className="px-4 py-3 border-t border-base-800/80">
            <Link
              to="/skills"
              className="flex items-center gap-2 text-sm text-base-500 hover:text-base-300 transition-colors"
            >
              <Settings className="w-4 h-4" />
              Manage Skills
            </Link>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col bg-base-900 min-w-0">
        {/* Header */}
        <div className="h-14 border-b border-base-800/80 flex items-center px-4 gap-3">
          {!sidebarOpen && (
            <div className="flex items-center gap-1.5">
              <button
                onClick={toggleSidebar}
                className="p-1.5 text-base-400 hover:text-base-200 hover:bg-base-800/80 rounded-lg transition-colors"
                title="Open sidebar"
              >
                <PanelLeft className="w-5 h-5" />
              </button>
              <button
                onClick={() => { resetChat(); }}
                className="p-1.5 text-base-400 hover:text-base-200 hover:bg-base-800/80 rounded-lg transition-colors"
                title="New Chat"
              >
                <Plus className="w-5 h-5" />
              </button>
              <div className="w-px h-5 bg-base-800/80 mx-1" />
            </div>
          )}
          <SkillPicker />
        </div>

        {/* Chat */}
        <div className="flex-1 overflow-hidden">
          <ChatWindow />
        </div>
      </div>
    </div>
  );
}
