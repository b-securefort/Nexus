import { ChatWindow } from "../components/ChatWindow";
import { ConversationList } from "../components/ConversationList";
import { SkillPicker } from "../components/SkillPicker";
import { Sparkles, Settings } from "lucide-react";
import { Link } from "react-router-dom";

export function ChatPage() {
  return (
    <div className="flex h-screen bg-zinc-900 text-zinc-100">
      {/* Sidebar */}
      <div className="w-64 bg-zinc-900 border-r border-zinc-700 flex flex-col">
        <div className="p-3 border-b border-zinc-700">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-blue-400" />
            <span className="font-semibold text-zinc-100">Nexus</span>
          </div>
        </div>
        <div className="flex-1 overflow-hidden">
          <ConversationList />
        </div>
        <div className="p-3 border-t border-zinc-700">
          <Link
            to="/skills"
            className="flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            <Settings className="w-4 h-4" />
            Manage Skills
          </Link>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col">
        {/* Header */}
        <div className="h-14 border-b border-zinc-700 flex items-center px-4 gap-4">
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
